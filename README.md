# RAG SaaS

A multi-tenant Retrieval-Augmented Generation platform for small and mid-sized businesses, with first-class support for French, Arabic (Darija), and English documents. Upload your contracts, emails, reports, invoices, and policies; query them through a chat interface. Built to be self-hosted on a VPS, multi-tenant from day one, and wired for future ERP integrations (Sage, Odoo, Dolibarr).

## Status

`v0.2.0` (V1) shipped: single-document RAG pipeline. Upload a PDF, the worker parses (Docling), chunks (token-aware), embeds (BGE-M3 via sentence-transformers, CPU), and indexes to Qdrant. Search returns top-k chunks scoped to the caller's tenant. Cross-tenant isolation is enforced at the Qdrant payload filter and again at the Postgres hydration query. See [`docs/design/v1-pipeline.md`](./docs/design/v1-pipeline.md) for the full design and [`CHANGELOG.md`](./CHANGELOG.md) for what's in each release.

V1.5 (chat UI, agents, duplicate-upload 409) and V2 (reranker, contextual retrieval, hybrid search, Arabic test fixtures, RLS) are deliberate non-goals at this point.

## Run locally (three commands)

```bash
bash scripts/setup.sh                                                      # clones RAGFlow, copies .env
# edit .env if you want non-default ports or LLM provider, then:
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

The `--build` is needed on first run because the backend image carries Docling (~3.5 GB once downloaded) and the worker reuses the same Dockerfile under a separate image tag.

### Smoke checks (after services report healthy)

```bash
curl http://localhost:8000/health                                          # {"status":"ok"}
curl http://localhost:6333/readyz                                          # all shards are ready
docker compose --env-file .env -f docker/docker-compose.yml exec redis redis-cli ping
docker compose --env-file .env -f docker/docker-compose.yml \
    exec postgres psql -U rag -d rag -c "SELECT extname FROM pg_extension;"  # includes vector
```

### V1 happy path

```bash
TENANT=$(uuidgen)

# tenant rows are created directly in V1; the auth-aware tenant endpoint lands in V1.5
docker compose --env-file .env -f docker/docker-compose.yml \
    exec postgres psql -U rag -d rag \
    -c "INSERT INTO tenants (id, name) VALUES ('$TENANT','smoke');"

curl -X POST http://localhost:8000/api/v1/documents \
     -H "X-Tenant-ID: $TENANT" \
     -F "file=@backend/tests/fixtures/fr_sample.pdf"

# poll until "status":"ready" — pipeline takes ~140 s on CPU for the 8-page sample
curl http://localhost:8000/api/v1/documents/<id> -H "X-Tenant-ID: $TENANT"

curl "http://localhost:8000/api/v1/search?q=le+sujet+principal&top_k=5" \
     -H "X-Tenant-ID: $TENANT"
```

## Migrations

Always run Alembic inside the backend container — the in-repo `DATABASE_URL` targets the `postgres` hostname on the compose network, so host-side `alembic` resolves to nothing. The wrapper handles this:

```bash
scripts/migrate.sh                                 # upgrade head (default)
scripts/migrate.sh current                         # show applied revision
scripts/migrate.sh history                         # list revisions
scripts/migrate.sh revision --autogenerate -m "…"  # create a new migration
scripts/migrate.sh downgrade -1                    # roll back one
```

The script auto-starts the `backend` service if it isn't running.

## Tests

Unit tests run host-side OR container-side. Integration tests need the running compose stack and run cleanest from inside the backend container (the Windows host's MSYS layer chokes on enough subprocess spawns; the container has no such limit).

```bash
# Unit only, host-side
cd backend && uv run --extra dev pytest tests/unit tests/test_health.py

# Full suite (unit + integration) inside the backend container
docker compose --env-file .env -f docker/docker-compose.yml exec \
    -e INTEGRATION_DATABASE_URL=postgresql+asyncpg://rag:rag@postgres:5432/rag \
    -e INTEGRATION_BACKEND_URL=http://localhost:8000 \
    backend uv run --extra dev pytest --no-cov
```

The cross-tenant isolation gate is `tests/integration/test_pipeline.py::test_tenant_isolation`. Before tagging a release, that test must show PASSED in the pytest output (not skipped, not errored).

## Ports

| Service  | Default host port | Container port |
| -------- | ----------------- | -------------- |
| Frontend | 3000              | 3000           |
| Backend  | 8000              | 8000           |
| Worker   | (no port)         | —              |
| Postgres | 5432              | 5432           |
| Qdrant   | 6333 / 6334       | 6333 / 6334    |
| Redis    | 6379              | 6379           |

Override any host port via `.env` (`FRONTEND_HOST_PORT`, `BACKEND_HOST_PORT`, …) when something on your machine already holds the default.

## Project layout

```
backend/   FastAPI app, arq worker, Docling/BGE-M3/Qdrant clients, Alembic migrations
frontend/  Next.js 16 (App Router, Tailwind v4, shadcn, i18next)
docker/    Compose files (dev + prod override) and Postgres init SQL
docs/      Design notes (docs/design/) and ADRs (docs/adr/)
scripts/   setup.sh, migrate.sh
reference/ ragflow/ clone for reading (gitignored)
```

## Full engineering contract

Every decision, convention, and constraint is in [`claude.md`](./claude.md). Read it before changing anything. Read [`docs/design/v1-pipeline.md`](./docs/design/v1-pipeline.md) before touching the V1 surface.
