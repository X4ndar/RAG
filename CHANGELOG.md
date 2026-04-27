# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver.

## [Unreleased]

Nothing yet.

## [v0.2.0] — 2026-04-27

V1 ships: a single-document end-to-end RAG pipeline with cross-tenant isolation gated by a mutation-tested integration test.

### Added

- Database schema: `tenants`, `documents`, `chunks`. `VARCHAR(16) + CHECK` for the document status enum so adding a state in V2 is a plain data migration. Composite indexes on `(tenant_id, …)` everywhere; `(tenant_id, content_hash)` is the dedup surface for V1.5. First Alembic migration `081eef6a8820_initial_schema.py`.
- `TenantOwned` ORM mixin and `tenant_scoped(stmt, tenant_id, model)` helper; the only sanctioned way to query tenant-owned tables. PEP 695 type parameter on the helper.
- Docling PDF parser (no-OCR fast path, `do_table_structure=True`, pypdfium backend) and a token-aware delimiter-first chunker (default 512 tokens, 64 overlap, drop chunks <20 tokens). DEBUG-logs the markdown length and first 200 chars before the empty-text check so scanned-PDF regressions are visible.
- BGE-M3 dense embedding service (1024-dim, cosine, CPU-pinned for determinism). `warm_up()` runs in `asyncio.to_thread`; `embed()` wraps only the inference call. Shared model cache lives in the `embedding_cache` named volume.
- Qdrant integration: `documents_v1` collection auto-bootstrapped at FastAPI startup with payload indexes on `tenant_id` and `document_id`. `search_for_tenant()` is the only exported search function — no raw `query_points` wrapper escapes the module. Module-level singleton `AsyncQdrantClient`.
- arq worker (`app/jobs/worker.py`): idempotent `process_document` using `SELECT FOR UPDATE SKIP LOCKED` to claim a row, releasing the lock before any heavy work. Failure handler opens a fresh session for the `status='failed'` write so a rollback can't leave the row stuck. `parse_duration_ms` and `embed_duration_ms` measured tightly around the steps. Embedding batched at 32 chunks per call. Qdrant point IDs are the `chunks.id` UUIDs (not freshly generated) so commit 6's hydration is single-round-trip.
- arq producer (`app/jobs/enqueue.py`): lazy module-level `ArqRedis` pool, opened on first enqueue, closed in FastAPI's lifespan shutdown.
- `POST /api/v1/documents` (201) — MIME-sniffs the first 4 KB with `python-magic` (rejects non-PDF as 400), stream-hashes the full file with sha256 over 1 MB chunks, inserts the row before saving the file under `<tenant>/<doc_id>.pdf`, then enqueues. Lazy `mkdir(parents=True, exist_ok=True)` for the tenant directory is race-safe.
- `GET /api/v1/documents/{id}` — 404 for both missing and foreign-tenant rows; never 403 (no existence leak).
- `GET /api/v1/search` — embeds the query, calls `search_for_tenant`, hydrates with a single `WHERE id = ANY(:ids) AND tenant_id = :t` query (defense in depth and no N+1), then re-sorts the rows in Python by Qdrant's scored-point order.
- `get_current_tenant` FastAPI dependency (401 on missing or non-UUID `X-Tenant-ID`); reusable `CurrentTenant = Annotated[UUID, Depends(...)]` alias keeps route signatures free of runtime function calls.
- `worker` compose service (separate image tag from `backend`, same Dockerfile). Both services depend on Postgres, Qdrant, and Redis under `condition: service_healthy`. Shared `document_storage` and `embedding_cache` named volumes.
- Dev bind mount on `../backend → /app` (with anonymous volume on `/app/.venv`); prod compose override strips it via `volumes: !override`.
- Two real French Wikipedia PDFs (`fr_sample.pdf` Couscous, `fr_sample_2.pdf` Casablanca) at `backend/tests/fixtures/`, with `tests/fixtures/README.md` documenting source URLs, accessed date, CC BY-SA 4.0 attribution, and a refresh recipe. Topics deliberately disjoint so cross-tenant leaks are obvious.
- Unit tests: `test_chunker.py` (max-tokens cap, overlap band, min-tokens drop, no-delimiter hard-split), `test_bge_m3.py` (shape, determinism, dim-assertion safety), `test_tenant_deps.py` (missing/invalid/valid header, case-insensitive lookup).
- Integration tests: `test_end_to_end_single_tenant` (upload → poll → search, descending scores) and `test_tenant_isolation` (two tenants, two docs, both directions; **mutation-tested** — removing either the Qdrant filter or the Postgres hydration filter makes it fail).
- `CHANGELOG.md` (this file).

### Changed

- **Embedding library: sentence-transformers, not FastEmbed.** The original V1 design pinned FastEmbed for the "no Torch in runtime" benefit. Investigation showed that benefit is void (Docling pulls Torch in transitively), `BAAI/bge-m3` is not in FastEmbed's built-in catalog, `add_custom_model` against the official repo fails (ONNX in subdir, downloader skips), and community flat-layout exports load but emit a non-standard shape FastEmbed can't normalize. Sentence-transformers is the explicit alternate CLAUDE.md permits. Re-evaluate in V2 if CPU latency becomes the bottleneck.
- **Embedding cache volume name:** `fastembed_cache` → `embedding_cache` (and `FASTEMBED_CACHE_DIR` → `EMBEDDING_CACHE_DIR`); provider-agnostic naming for the same purpose.
- **Integration polling timeout:** the design originally proposed 60 s assuming "small test PDFs". With the committed Wikipedia fixtures (~8 pages) and CPU embedding (~3-4 s per chunk), real end-to-end pipeline time is ~140 s per document. Bumped to 240 s; `test_tenant_isolation` waits on both pipelines via `asyncio.gather` so the deadline is shared, not additive.
- **Integration tests use env-driven URLs** (`INTEGRATION_DATABASE_URL`, `INTEGRATION_BACKEND_URL`) so the same code runs from the host (defaults to `localhost`) and from inside the backend container (set the in-network forms via `docker compose exec -e ...`).
- **Backend Dockerfile** now installs `libxcb1`, `libxext6`, `libsm6`, `libxrender1`, `libgl1`, `libglib2.0-0`. Docling's image-processing deps need them even when we render headless. A future image-slimming pass must not remove them silently.
- Test count strengthening: `test_tenant_isolation` now asserts `len(hits) == top_k` in addition to per-hit `document_id` checks. The count check catches leak-through (Qdrant filter missing, Postgres drops the foreign hits, count goes short); the per-hit check catches outright leak (Postgres filter missing). Verified by mutation: removing each filter in turn now breaks the test.

### Deferred (explicit non-goals for v0.2.0)

- Chat UI — V1.5
- Pydantic AI agents (router, chat, enrichment, extractor) — V1.5
- Reranker (`bge-reranker-v2-m3` or `jina-reranker-v2-base-multilingual`) — V2
- Contextual Retrieval — V2
- Sparse and multi-vector embeddings; hybrid search with Postgres FTS + RRF — V2
- Auth beyond the `X-Tenant-ID` dependency — V2
- Postgres Row-Level Security — V3, after query patterns settle
- Arabic test fixtures — V2, after a tokenization strategy is chosen
- Document deletion, reparsing, content-hash-based duplicate rejection (409) — V1.5+
- S3-compatible storage — out of V1 scope; local volume only
- Frontend changes (Next.js app stays as the V0 placeholder)

## [v0.1.0] — 2026-04-18

V0 scaffold: FastAPI + Next.js 16 + Postgres 16 (pgvector) + Qdrant + Redis in Docker Compose. No features; `docker compose up` yields healthy infra plus `/health` and an empty Next.js page. RAGFlow reference cloned and gitignored. Migration workflow scoped to the backend container via `scripts/migrate.sh`.

[Unreleased]: https://github.com/X4ndar/RAG/compare/v0.2.0...HEAD
[v0.2.0]: https://github.com/X4ndar/RAG/compare/v0.1.0...v0.2.0
[v0.1.0]: https://github.com/X4ndar/RAG/releases/tag/v0.1.0
