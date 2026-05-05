# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow semver.

## [Unreleased]

Nothing yet.

## [v0.3.0] — 2026-05-05

V1.5a: BYOM (bring-your-own-model) configuration plumbing. Each tenant configures their own LLM provider; the platform never ships a default. Six provider types and a typed factory for V1.5b's chat UI to plug into.

### Added

- `tenant_llm_configs` table (second Alembic migration `ffa2b6ed122c`) with one row per tenant (UNIQUE on `tenant_id`), Fernet-encrypted `api_key_encrypted` (`bytea`), plaintext `api_key_last4` for UI display, persisted `test_status` / `test_error` / `tested_at`. Three named CHECK constraints (provider whitelist, test_status whitelist, conditional `api_key_encrypted IS NOT NULL AND length >= 80 AND length(last4) = 4` when provider is not `ollama`). One named constraint per logical invariant.
- `app/llm/encryption.py` — Fernet wrapper. `_load_fernet()` is the single chokepoint that reads `LLM_CONFIG_MASTER_KEY`; the only function that raises `MasterKeyMissingError`. A unit test enforces the chokepoint by inspecting the module source. One FastAPI exception handler in `app/main.py` maps the exception to **503**; endpoints don't catch it themselves.
- `app/llm/factory.py` — provider factory. `build_model(config)` is **pure synchronous** (no I/O, no async), dispatch via if-chain on the provider key. `agent_for_tenant(tenant_id)` is the single chokepoint that turns a saved config into a Pydantic AI `Agent`. `LLMNotConfiguredError` (→ 409) when the tenant has no row. `get_model_factory()` is the FastAPI dependency tests override; production code reads no env-var swap flag. `PROVIDER_TIMEOUTS_S` table (5 s ollama, 15 s cloud) co-located with the factory.
- `app/llm/sanitise.py::safe_provider_message()` — strips verbatim API keys, Authorization Bearer tokens, `api_key=` query strings, and `'Authorization': '...'` dict reprs from any error message before it reaches the UI. Called on **every** error path of the test endpoint.
- `POST /api/v1/admin/llm-config` (create/replace), `GET /api/v1/admin/llm-config` (last-4 view), `DELETE /api/v1/admin/llm-config` (idempotent). 201 responses include a `warning` field that the UI renders as a yellow banner when the saved row's `test_status != 'passed'`.
- `POST /api/v1/admin/llm-config/test` — synchronous live probe against the supplied config with provider-specific timeout. Returns `{ok, latency_ms, error}`; 200 even when `ok=false` (failed test isn't an HTTP error).
- `/settings/llm-provider` Next.js admin form — server shell + client form. Conditional fields by provider, masked-key chip showing last 4, "Test connection" button with inline pass/fail, save-after-pass UX with error/warning banners. Tenant ID input persists to localStorage as a V1.5a-only dev affordance.
- 39 new tests: 8 encryption (round-trip, tamper-detect, missing/malformed key, chokepoint invariant, helpers), 13 factory dispatch (pure-sync invariant, provider→class mapping, base_url propagation, timeout-table coverage), 7 sanitiser unit, 7 sanitiser endpoint-level (per-except-branch), 4 secret-hygiene exercises against the live endpoint via DI, plus 10 admin CRUD integration tests. Total V1.5a unit+integration: 45 tests.

### Changed

- **Pydantic AI dependency:** `pydantic-ai-slim[anthropic,openai,google,mistral]>=1.85,<2`. The umbrella `pydantic-ai` 1.0/1.1 series referenced anthropic SDK symbols (`UserLocation`) that have since been removed; 1.85+ tracks current SDKs. The `<2` cap is a deliberate "decide explicitly when 2.x lands."
- **Ollama routes through `OpenAIChatModel`** internally (Pydantic AI 1.90 ships no dedicated `OllamaModel`). The user-facing form still presents Ollama as a discrete option.
- **CLAUDE.md provider configuration section rewritten.** The earlier "named constants in `app/llm/providers.py`" + "default to Ollama in dev, Claude in prod" paragraph contradicted the BYOM mandate; replaced with the factory-based pattern. Logfire bullet noted as deferred (transitive OpenTelemetry conflict).
- **Logfire pytest plugin disabled** via `-p no:logfire` in `addopts` — auto-loaded transitive dep with an OpenTelemetry version conflict that surfaces during test discovery.
- **Backend Dockerfile** unchanged in V1.5a but the image must be rebuilt (`docker compose build backend && docker compose build worker`) to pick up the new Python deps (`cryptography`, `pydantic-ai-slim`).

### Deferred (explicit non-goals for v0.3.0)

- Chat UI / answer agent / streaming — V1.5b
- Master-key rotation tooling (`LLM_CONFIG_MASTER_KEY_FALLBACK` + re-encrypt-on-save) — V1.5b
- Multiple configs per tenant (per-role: chat vs enrichment vs router) — V2
- Per-request provider override — V2
- Cohere and Groq providers — V1.5b or later, when a tenant asks
- Usage metering / cost tracking / quota enforcement — out of V1.5a scope
- Provider-level rate-limit handling beyond pass-through — V2
- Model parameter overrides (temperature, top_p, max_tokens) on the saved config — defaults set per-agent at call time
- Auth roles inside a tenant — V2 auth layer
- Real secrets-management story (Docker secrets, Vault, AWS SM) for the master key — operator-side decision; the env-var pattern is dev-grade

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

[Unreleased]: https://github.com/X4ndar/RAG/compare/v0.3.0...HEAD
[v0.3.0]: https://github.com/X4ndar/RAG/compare/v0.2.0...v0.3.0
[v0.2.0]: https://github.com/X4ndar/RAG/compare/v0.1.0...v0.2.0
[v0.1.0]: https://github.com/X4ndar/RAG/releases/tag/v0.1.0
