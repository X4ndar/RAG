# RAG SaaS

A multi-tenant Retrieval-Augmented Generation platform for small and mid-sized businesses, with first-class support for French, Arabic (Darija), and English documents. Upload your contracts, emails, reports, invoices, and policies; query them through a chat interface. Built to be self-hosted on a VPS, multi-tenant from day one, and wired for future ERP integrations (Sage, Odoo, Dolibarr).

## Status

`v0.3.0` (V1.5a) shipped: BYOM (bring-your-own-model) configuration plumbing. Each tenant configures their own LLM provider through `/settings/llm-provider`; credentials are encrypted at rest with Fernet, the platform never ships a default provider. Six provider types: Anthropic, OpenAI, Google, Mistral, OpenAI-compatible custom, Ollama. A typed factory (`agent_for_tenant(tenant_id)`) is the single chokepoint between tenant configs and Pydantic AI Agents — V1.5b's chat UI plugs in on top of it. See [`docs/design/v1.5a-byom-config.md`](./docs/design/v1.5a-byom-config.md).

`v0.2.0` (V1) shipped: single-document RAG pipeline. Upload a PDF, the worker parses (Docling), chunks (token-aware), embeds (BGE-M3 via sentence-transformers, CPU), and indexes to Qdrant. Search returns top-k chunks scoped to the caller's tenant. Cross-tenant isolation is enforced at the Qdrant payload filter and again at the Postgres hydration query. See [`docs/design/v1-pipeline.md`](./docs/design/v1-pipeline.md).

V1.5b (chat UI, answer agent, master-key rotation tooling) and V2 (reranker, contextual retrieval, hybrid search, Arabic test fixtures, RLS, real auth) are deliberate non-goals at this point. Full release notes in [`CHANGELOG.md`](./CHANGELOG.md).

## Run locally (three commands)

```bash
bash scripts/setup.sh                                                      # clones RAGFlow, copies .env
# Generate a Fernet master key for at-rest API-key encryption and write
# it into .env. Required by V1.5a; the LLM endpoints return 503 without it.
python -c "from cryptography.fernet import Fernet; print('LLM_CONFIG_MASTER_KEY=' + Fernet.generate_key().decode())" >> .env
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

### V1.5a happy path (configure a provider)

```bash
TENANT=$(uuidgen)
docker compose --env-file .env -f docker/docker-compose.yml \
    exec postgres psql -U rag -d rag \
    -c "INSERT INTO tenants (id, name) VALUES ('$TENANT','byom-smoke');"

# Save a config (warning surfaces because no test was run yet).
curl -X POST http://localhost:8000/api/v1/admin/llm-config \
     -H "X-Tenant-ID: $TENANT" -H "Content-Type: application/json" \
     -d '{"provider":"anthropic","model_name":"claude-sonnet-4-5","api_key":"sk-ant-test-12345"}'

# Run a connection test against the (real) provider; result is {ok, latency_ms, error}.
curl -X POST http://localhost:8000/api/v1/admin/llm-config/test \
     -H "X-Tenant-ID: $TENANT" -H "Content-Type: application/json" \
     -d '{"provider":"anthropic","model_name":"claude-sonnet-4-5","api_key":"<your-real-key>"}'

# Read back the masked view (api_key_last4 only, no full key).
curl http://localhost:8000/api/v1/admin/llm-config -H "X-Tenant-ID: $TENANT"
```

The same flow is available with form UI at <http://localhost:3000/settings/llm-provider> (paste the tenant UUID into the Tenant ID field; it persists in localStorage).

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

# Full suite (unit + integration) inside the backend container.
# The integration conftest detects /.dockerenv and points at postgres:5432
# automatically, so no -e flags needed.
docker compose --env-file .env -f docker/docker-compose.yml exec \
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
