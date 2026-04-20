# V1 design: single-document pipeline

Status: **approved**. Implementation follows the commit plan at the bottom.

## Goal

One tenant uploads one PDF; the backend parses, chunks, embeds, and indexes it. Later, the same tenant queries and gets top-k chunks back. Nothing else. Chat, agents, reranker, contextual retrieval, hybrid search, Arabic support, and RLS are explicitly out of scope.

The success criterion: an integration test uploads a small French PDF, polls until `status=ready`, searches with a phrase present in the doc, and asserts the top hit comes from that document. A second integration test proves tenant A cannot see tenant B's documents through either the Qdrant filter or the Postgres hydration query.

## Scope

**In V1:** tenants table, documents table, chunks table, first Alembic migration, upload endpoint, background worker, Docling PDF parse (no OCR), token-aware chunker (512/64), BGE-M3 dense embeddings via FastEmbed, Qdrant collection `documents_v1` with payload filter for tenant isolation, search endpoint, unit + integration tests including cross-tenant isolation.

**Not V1:** chat UI, Pydantic AI agents, reranker, contextual retrieval, sparse/multi-vector embeddings, BM25/hybrid, auth beyond the tenant header dependency, Postgres RLS, Arabic test fixtures, deletion, reparsing, versioning, S3 storage, frontend changes, 409 on duplicate uploads (the `content_hash` index is a dedup surface for V1.5; V1 allows duplicates through).

## Architecture

```
                          ┌──────────────────────┐
 client ── POST /docs ───▶│  FastAPI backend     │──enqueue──▶ Redis ──▶ arq worker
                          │  get_current_tenant  │                          │
                          │  validate MIME       │                          │
                          │  insert pending row  │                          │
                          │  save PDF to volume  │                          │
                          └──────────────────────┘                          │
                                                                            ▼
                          ┌──────────────────────┐       ┌──────────────────────────┐
                          │ document_storage vol │◀──────│ worker: parse→chunk→embed │
                          │ /app/storage/...     │       │ → Qdrant upsert          │
                          └──────────────────────┘       │ → status updates (FOR    │
                                                         │   UPDATE + idempotency)  │
                          ┌──────────────────────┐       └──────────────────────────┘
 client ── GET /search ──▶│  FastAPI backend     │──embed(q)──┐
                          │  tenant filter on    │            ▼
                          │  Qdrant + hydrate    │◀───────── Qdrant (documents_v1)
                          │  text from Postgres  │            │
                          └──────────────────────┘            │
                                       ▲                      ▼
                                       └── chunks table ◀── Postgres (tenants, documents, chunks)
```

Named volumes added in V1:

- `document_storage` mounted at `/app/storage` in backend **and** worker containers.
- `fastembed_cache` mounted at `/app/.cache/fastembed` in backend **and** worker. Saves redownloading BGE-M3's ~2 GB ONNX weights on every rebuild.

## Database schema (first migration)

```sql
-- Extensions already installed in V0 init.sql: vector, pg_trgm.

CREATE TABLE tenants (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  filename            text NOT NULL,
  mime_type           text NOT NULL,
  file_size_bytes     bigint NOT NULL CHECK (file_size_bytes >= 0),
  content_hash        text NOT NULL,                     -- sha256 hex of uploaded bytes
  status              varchar(16) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','parsing','chunking','embedding','ready','failed')),
  error_message       text,
  parse_duration_ms   integer,
  embed_duration_ms   integer,
  uploaded_at         timestamptz NOT NULL DEFAULT now(),
  parsed_at           timestamptz,
  metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_documents_tenant_id_id        ON documents (tenant_id, id);
CREATE INDEX ix_documents_tenant_id_status    ON documents (tenant_id, status);
CREATE INDEX ix_documents_tenant_content_hash ON documents (tenant_id, content_hash);
-- V1 keeps duplicate uploads legal: the index is the dedup surface, but the upload
-- endpoint does NOT reject (409) on a hash collision until V1.5.

CREATE TABLE chunks (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  tenant_id     uuid NOT NULL REFERENCES tenants(id)   ON DELETE CASCADE,
  chunk_index   integer NOT NULL,
  text          text NOT NULL,
  token_count   integer NOT NULL CHECK (token_count > 0),
  char_start    integer NOT NULL CHECK (char_start >= 0),
  char_end      integer NOT NULL CHECK (char_end > char_start),
  page_number   integer,                                 -- NULL if parser can't map it
  metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (document_id, chunk_index)
);
CREATE INDEX ix_chunks_tenant_document ON chunks (tenant_id, document_id);
```

Status is `VARCHAR(16) + CHECK` instead of a native Postgres enum so adding a new state in V2 is a simple migration without `ALTER TYPE`.

### JSONB shapes

Conventions, not enforced by Postgres. Documented in the ORM docstrings.

`documents.metadata`:

```jsonc
{
  "source": "upload",                   // "upload" | future: "gdrive" | "email" | ...
  "original_filename": "contrat.pdf",   // pre-sanitization
  "page_count": 12,                     // from Docling
  "docling_version": "2.x.y",
  "language_hint": null                 // detected in later phases
}
```

`chunks.metadata`:

```jsonc
{
  "section_path": ["Chapitre 2", "Article 3"], // markdown heading stack, may be []
  "block_type": "paragraph"                    // "paragraph" | "table" | "heading" | "list"
}
```

Migration generated via `scripts/migrate.sh revision --autogenerate -m "initial schema"`. The generated file must land on the host at `backend/app/db/migrations/versions/*.py` (verified with `git status`) before we ship the commit, because the worker image bakes migrations in at build time.

## Tenant isolation (V1, pre-RLS)

Three enforcement points, each with a single implementation.

**1. Request boundary (`app/tenancy/deps.py`):**

```python
async def get_current_tenant(request: Request) -> UUID:
    raw = request.headers.get("X-Tenant-ID")
    if not raw:
        raise HTTPException(401, "X-Tenant-ID header is required")
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(401, "X-Tenant-ID must be a valid UUID") from exc
```

Every tenant-touching route declares `tenant_id: UUID = Depends(get_current_tenant)`. The existing `TenantMiddleware` stays (sets `request.state.tenant_id` for logging); the dependency is what enforces.

**2. Database (`app/db/tenant_scope.py`):**

```python
def tenant_scoped(stmt: Select, tenant_id: UUID, model: type[TenantOwned]) -> Select:
    """Apply WHERE tenant_id = :tenant. Use for every tenant-owned read or write."""
    return stmt.where(model.tenant_id == tenant_id)
```

Combined with a `TenantOwned` mixin on the `Document` and `Chunk` ORM classes. Code review rule: any `select(Document|Chunk)` must be followed by a `tenant_scoped(...)` call. If ever violated, we write a ruff plugin. Not in V1.

**3. Vector search:** the Qdrant module exposes only `search_for_tenant(tenant_id, query_vector, top_k)`. No raw-search function exists, so the tenant filter cannot be forgotten by API shape.

RLS is V3 work; this design defers it consciously.

## Idempotent worker

`process_document(ctx, document_id)` begins with an atomic lock-and-check:

```python
async with async_session_maker() as sess, sess.begin():
    doc = (await sess.execute(
        select(Document)
          .where(Document.id == document_id)
          .with_for_update(skip_locked=True)
    )).scalar_one_or_none()

    if doc is None:
        logger.warning("document_not_found", document_id=document_id)
        return
    if doc.status not in ("pending", "failed"):
        logger.info("already_processing_or_done", document_id=document_id, status=doc.status)
        return  # idempotent exit

    doc.status = "parsing"
    # transaction commits here, releasing the row lock
```

Everything after that runs outside the lock. On any exception the outer handler sets `status='failed'`, `error_message=str(exc)[:2000]` (stack traces need the room), and logs the full traceback at ERROR. No auto-retry in V1 (`max_tries=1`). Retrying means flipping status back to `pending` manually.

## Libraries to add

| Library         | Pin              | Why                                                        |
|-----------------|------------------|------------------------------------------------------------|
| `arq`           | `>=0.26,<0.27`   | Async Redis queue, existing Redis service                  |
| `docling`       | `>=2.0`          | Primary PDF parser per CLAUDE.md                           |
| `sentence-transformers` | `>=3.0`  | BGE-M3 dense. FastEmbed path ruled out, see note below     |
| `qdrant-client` | `>=1.12`         | `AsyncQdrantClient` + `query_points`                       |
| `python-magic`  | `>=0.4`          | MIME from content bytes, not filename                      |
| `transformers`  | `>=4.44`         | Tokenizer for BGE-M3 (token-count math for chunker)        |

`python-magic` on Windows needs `python-magic-bin`; for the Linux container we pin vanilla `python-magic` and add `libmagic1` to the backend Dockerfile's apt list.

Gotchas from context7, still true:

- The embedding library is sync. Wrap with `asyncio.to_thread`.
- Qdrant `search()` is deprecated. Use `query_points()`.
- BGE-M3 is **1024-dim**. Assert at startup; fail loud if it's not.

### Embedding library: FastEmbed out, sentence-transformers in

The original design pinned FastEmbed for the "no Torch in runtime" benefit. Real investigation (commit 3) found that benefit doesn't exist in our stack and the FastEmbed path has blockers:

1. **Torch is already pulled in by Docling.** `docling>=2.0` installs `torch==2.11.0` and `torchvision==0.26.0` as hard transitive deps. Whether we use FastEmbed or sentence-transformers, the image carries Torch. The "slim runtime" argument is void.
2. **FastEmbed's built-in catalog does not include BGE-M3.** `TextEmbedding.list_supported_models()` lists only BGE English variants and `bge-small-zh-v1.5`. No `bge-m3`.
3. **`TextEmbedding.add_custom_model` does not work against the official `BAAI/bge-m3` repo.** The ONNX export lives in an `onnx/` subdirectory and FastEmbed's downloader silently skips subdirectories, then ONNX Runtime fails with `NO_SUCHFILE` on the expected flat path. `additional_files` explicitly listing the subdir contents does not fix this; FastEmbed validates final paths against its own layout assumptions.
4. **Community flat-layout ONNX exports (`aapot/bge-m3-onnx`) do load** but emit a non-standard output shape (FastEmbed's `CustomTextEmbedding._normalize` raises `AxisError: axis 1 is out of bounds for array of dimension 1`). Converting/maintaining our own ONNX export for FastEmbed compatibility is out of V1 scope and adds a long-term brittle dependency on community forks.

**Decision:** `sentence-transformers>=3.0`, `device="cpu"` for reproducibility, weights cached in the `embedding_cache` named volume. Expected ~3x slower CPU inference vs an ideal FastEmbed path, but batch embedding happens in the background worker (off request path) and query embedding is a single 50-100 ms call — not a V1 bottleneck.

**Revisit in V2** if CPU-path latency becomes the bottleneck. Options: ship a vetted in-house ONNX export of BGE-M3, switch to a FastEmbed-native multilingual model (`intfloat/multilingual-e5-large` is in the built-in catalog), or add a GPU embedding lane.

## Parsing (`app/parsers/pdf_docling.py`)

```python
converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(
            pipeline_options=PdfPipelineOptions(do_ocr=False, do_table_structure=True),
            backend=PyPdfiumDocumentBackend,
        ),
    },
)
```

Exports markdown via `result.document.export_to_markdown()`. Before the empty check, log at DEBUG the markdown length and its first 200 characters: makes diagnosing scanned-PDF regressions in production trivial. If the markdown is empty or whitespace-only, the job sets `status='failed'`, `error_message='no_text_extracted'`, and stops. OCR fallback is a V2 decision.

`page_number` per chunk is derived by walking Docling's `result.document` item stream and mapping character offsets in the markdown back to the originating page. If the mapping can't be computed cleanly we store `NULL`; the schema allows it.

## Chunking (`app/parsers/chunker.py`)

- Recursive delimiter-aware splitter.
- Delimiters, priority order: `\n\n`, `\n`, `. `, `。`, `! `, `? `.
- Target 512 tokens, 64 tokens overlap, skip chunks under 20 tokens.
- Token count via `AutoTokenizer.from_pretrained("BAAI/bge-m3")` loaded once and cached.
- Each chunk records `char_start`/`char_end` relative to the parsed markdown so we can trace chunks back to source offsets later.

From RAGFlow we take the delimiter-first-then-pack approach (`rag/app/naive.py::naive_merge`). We do not take their bullet detection, tree merge, or encoding fallback loop.

## Embeddings (`app/embeddings/bge_m3.py`)

```python
@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(
        model_name="BAAI/bge-m3",
        cache_dir="/app/.cache/fastembed",
        lazy_load=False,
    )

async def embed(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(lambda: [v.tolist() for v in _model().embed(texts)])

async def embed_query(text: str) -> list[float]:
    return (await embed([text]))[0]

VECTOR_SIZE = 1024  # asserted on first call
```

## Qdrant (`app/retrieval/qdrant_client.py`)

Bootstrap at FastAPI startup:

```python
async def ensure_collection() -> None:
    if not await client.collection_exists("documents_v1"):
        await client.create_collection("documents_v1",
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE))
        await client.create_payload_index("documents_v1", "tenant_id",   PayloadSchemaType.KEYWORD)
        await client.create_payload_index("documents_v1", "document_id", PayloadSchemaType.KEYWORD)
```

Only exposed search function:

```python
async def search_for_tenant(tenant_id: UUID, query_vec: list[float], top_k: int) -> list[ScoredPoint]:
    return (await client.query_points(
        collection_name="documents_v1",
        query=query_vec,
        query_filter=Filter(must=[FieldCondition(
            key="tenant_id", match=MatchValue(value=str(tenant_id))
        )]),
        limit=top_k,
    )).points
```

Qdrant point id = `chunks.id` (UUID string). Payload: `{tenant_id, document_id, chunk_id, chunk_index}`.

## arq worker (`app/jobs/worker.py`)

```python
class WorkerSettings:
    functions = [process_document]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = "rag:docs"
    max_jobs = 2
    # TODO: job_timeout=300s is conservative for small test PDFs. Raise once we
    # see real-world documents (OCR paths, 50+ page contracts) miss the deadline.
    job_timeout = 300
    max_tries = 1
    keep_result = 3600
    on_startup = startup    # warms BGE-M3, opens DB and Qdrant clients, stores in ctx
    on_shutdown = shutdown
```

Pipeline inside `process_document`, each step flipping status and emitting a structured JSON log with `{document_id, tenant_id, step, elapsed_ms, status}`:

1. Idempotent lock + flip to `parsing` (above).
2. Docling parse → markdown, set `parsed_at`, `metadata.page_count`, `parse_duration_ms`, flip to `chunking`.
3. Chunk → insert `chunks` rows, flip to `embedding`.
4. Embed in batches of 32 → upsert points to Qdrant, set `embed_duration_ms`, flip to `ready`.
5. On any exception: outer handler sets `status='failed'`, `error_message=str(exc)[:2000]`, logs full traceback at ERROR.

New compose service `worker`: reuses `backend/Dockerfile`, `CMD ["arq", "app.jobs.worker.WorkerSettings"]`, same `depends_on` as backend, shares `document_storage` and `fastembed_cache`.

## API endpoints

```
POST /api/v1/documents
  depends_on: tenant_id = Depends(get_current_tenant)
  multipart: file
  steps:
    1. Read first 4 KB → python-magic → assert mime == "application/pdf" (400 if not)
    2. Stream full bytes to temp path, compute sha256 (content_hash)
    3. Insert documents row (status='pending')
    4. Move file to /app/storage/documents/<tenant_id>/<doc_id>.pdf (mkdir -p lazily)
    5. Enqueue process_document(str(doc_id)) on queue "rag:docs"
  returns: 201 {id, status, filename, uploaded_at}

GET /api/v1/documents/{id}
  depends_on: tenant_id = Depends(get_current_tenant)
  enforces: row.tenant_id == tenant_id (else 404, never 403)
  returns: 200 {id, status, filename, uploaded_at, parsed_at, metadata, error_message?}

GET /api/v1/search
  depends_on: tenant_id = Depends(get_current_tenant)
  query: q (1..500 chars, required), top_k (int, 1..50, default 10)
  steps:
    1. embed_query(q)
    2. search_for_tenant(tenant_id, vec, top_k) → ScoredPoint list in relevance order
    3. Hydrate in ONE round trip: SELECT * FROM chunks WHERE id = ANY(:ids) AND tenant_id = :t
       (defense in depth, no N+1). Postgres row order is not preserved; re-sort in Python
       by the Qdrant score order before returning. No per-id loops.
    4. Return joined {chunk_id, document_id, chunk_index, text, score, page_number}
  returns: 200 {results: [...]}
```

## Tests

1. `tests/unit/test_chunker.py`: markdown with known token counts. Assert every chunk ≤ 512 tokens, overlap ≈ 64 tokens between consecutive chunks, < 20-token chunks skipped, a no-delimiter 2000-token paragraph still chunks via hard-split fallback.
2. `tests/unit/test_bge_m3.py`: `embed(["hello world"])` returns shape `[1][1024]`; same text twice yields cosine > 0.99.
3. `tests/unit/test_tenant_deps.py`: missing header → 401; non-UUID → 401; valid UUID → dependency returns the UUID.
4. `tests/integration/test_pipeline.py::test_end_to_end_single_tenant`: fresh tenant via pytest fixture (INSERT + DELETE on teardown). Upload `fr_sample.pdf`, poll `GET /documents/{id}` with 60 s timeout, then `GET /search?q=<phrase present in doc>`, assert `results[0].document_id == uploaded_doc_id` and `score > 0.4`.
5. `tests/integration/test_pipeline.py::test_tenant_isolation`: **the single most important multi-tenancy test.** Two fresh tenants A, B. Upload `fr_sample.pdf` as A, `fr_sample_2.pdf` as B. After both reach `ready`, tenant A issues a search whose top result against a merged index would be from B. Assert every returned `document_id == doc_a.id` (doc_b never appears). Repeat symmetrically from B. Both the Qdrant filter and the Postgres hydration query must hold; removing either filter individually must break the test.

Fixtures: two short French Wikipedia articles exported as PDF, committed at `tests/fixtures/fr_sample.pdf` and `tests/fixtures/fr_sample_2.pdf`, on distinct topics. Source URLs, accessed date, and CC BY-SA license for both documented in `tests/fixtures/README.md`.

## Commit plan

1. `feat(db): initial schema for tenants, documents, chunks` — models + Alembic migration + `TenantOwned` mixin + `tenant_scoped` helper. Commit body calls out explicitly that `ix_documents_tenant_content_hash` is a **dedup surface for V1.5**; V1 allows duplicate uploads through.
2. `feat(parsers): docling pdf parser + token-aware chunker` — parser module, chunker module, unit test for chunker.
3. `feat(embeddings): bge-m3 via fastembed with shared cache` — embedding module, unit test, `fastembed_cache` volume.
4. `feat(retrieval): qdrant collection bootstrap and tenant-filtered search` — qdrant module, startup hook.
5. `feat(jobs): arq worker for document processing pipeline` — worker module, idempotent `process_document`, compose `worker` service.
6. `feat(api): document upload and search endpoints with tenant enforcement` — routes, Pydantic schemas, `get_current_tenant` dependency, python-magic MIME check, integration tests (both `test_end_to_end_single_tenant` and `test_tenant_isolation`).
7. `test(fixtures): commit two french wikipedia pdf samples with license note` — `fr_sample.pdf`, `fr_sample_2.pdf`, `tests/fixtures/README.md` with sources and CC BY-SA license.
8. `docs: README and CLAUDE.md updates for V1 workflow` — new endpoints, new env vars, new ports (worker has no published port), new `worker` service.

## Verification (before `v0.2.0`)

Run from project root, in order:

1. `scripts/migrate.sh revision --autogenerate -m "initial schema"`. The file must appear on the host at `backend/app/db/migrations/versions/`.
2. `scripts/migrate.sh upgrade head` against a fresh DB must succeed.
3. `cd backend && uv run pytest -v && uv run ruff check . && uv run mypy app/`.
   **Hard gate:** `tests/integration/test_pipeline.py::test_tenant_isolation` must show PASSED in the pytest output. Skipped or errored is a blocker; do not tag until this one is explicitly green.
4. `cd frontend && npm run lint && npx tsc --noEmit && npm run build`. Frontend is unchanged in V1; this is just a regression guard.
5. `docker compose --env-file .env -f docker/docker-compose.yml down -v && docker compose --env-file .env -f docker/docker-compose.yml up -d --build`.
6. Wait until all services healthy (`docker compose ps`).
7. Curl the happy path:
   ```bash
   TENANT=$(uuidgen)
   psql -h localhost -U rag -d rag -c "INSERT INTO tenants (id, name) VALUES ('$TENANT','smoke');"
   curl -X POST http://localhost:8000/api/v1/documents \
        -H "X-Tenant-ID: $TENANT" \
        -F "file=@backend/tests/fixtures/fr_sample.pdf"
   # poll GET /api/v1/documents/{id} until status=ready
   curl "http://localhost:8000/api/v1/search?q=une+phrase+du+document&top_k=5" \
        -H "X-Tenant-ID: $TENANT"
   ```
8. `git status` is clean.
9. `git tag -a v0.2.0 -m "V1: single-document upload-parse-chunk-embed-search pipeline"` then `git push origin main --tags`.

## Non-goals (guard rails while coding)

No chat UI, no Pydantic AI agents, no reranker, no contextual retrieval, no sparse or multi-vector, no BM25, no RLS, no auth beyond the header dependency, no Arabic fixtures, no deletion, no reparsing, no S3, no frontend changes. If the thought "while I'm in here" appears, stop. Scope discipline is what made V0 good.
