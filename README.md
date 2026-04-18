# RAG SaaS

A multi-tenant Retrieval-Augmented Generation platform for small and mid-sized businesses, with first-class support for French, Arabic (Darija), and English documents. Upload your contracts, emails, reports, invoices, and policies; query them through a chat interface. Built to be self-hosted on a VPS, multi-tenant from day one, and wired for future ERP integrations (Sage, Odoo, Dolibarr).

## Status

v0 scaffold. Nothing but infrastructure: Postgres 16 with pgvector, Qdrant, Redis, an empty FastAPI service with a `/health` endpoint, and an empty Next.js 16 page. Features land in subsequent phases.

## Run locally (three commands)

```bash
bash scripts/setup.sh                                    # clones RAGFlow reference, creates .env
# edit .env as needed, then:
docker compose -f docker/docker-compose.yml up --build
```

Smoke checks:

- Backend: `curl http://localhost:8000/health` -> `{"status":"ok"}`
- Frontend: open http://localhost:3000
- Postgres: `docker compose -f docker/docker-compose.yml exec postgres psql -U rag -d rag -c "SELECT extname FROM pg_extension;"` includes `vector`
- Qdrant: `curl http://localhost:6333/readyz`
- Redis: `docker compose -f docker/docker-compose.yml exec redis redis-cli ping`

## Ports

| Service  | Port      |
| -------- | --------- |
| Frontend | 3000      |
| Backend  | 8000      |
| Postgres | 5432      |
| Qdrant   | 6333/6334 |
| Redis    | 6379      |

## Project layout

```
backend/   FastAPI app (SQLAlchemy async, Pydantic v2, Alembic)
frontend/  Next.js 16 (App Router, Tailwind v4, shadcn, i18next)
docker/    Compose files and Postgres init SQL
docs/      Design notes and ADRs
scripts/   setup.sh
reference/ ragflow/ clone for reading (gitignored)
```

## Full engineering contract

Every decision, convention, and constraint is in [`claude.md`](./claude.md). Read it before changing anything.
