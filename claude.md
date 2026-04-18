# CLAUDE.md

This file is the source of truth for Claude Code working on this project. Read it fully before taking any action. Re-read it at the start of every new session.

## Project

A multi-tenant RAG SaaS platform for small and mid-sized businesses, with first-class support for French, Arabic (Darija), and English documents. The product lets a business upload its documents (contracts, emails, reports, invoices, policies) and query them through a chat interface. Multi-tenant from day one. Self-hostable on a VPS. The target first market is Moroccan SMEs, with ERP integrations (Sage, Odoo, Dolibarr) planned for later phases.

This is a product being built to sell, not a demo and not an internal tool. Every architectural decision must hold up under commercial use.

## Your role

You are the primary engineer on this project. You write code, not suggestions. You follow this document strictly. When something here conflicts with your general training, this document wins.

When in doubt about a design decision, ask before implementing. Do not invent architecture choices that are not in this file.

## Mandatory first actions (every session)

Run these before doing anything else. Do not skip.

1. List available skills in `/mnt/skills/` and read any `SKILL.md` files relevant to the current task before starting it. See "Skills policy" below.
2. List available Claude Code plugins and use them when they apply. See "Plugins policy" below.
3. Verify the RAGFlow reference repo exists at `reference/ragflow/`. If not, clone it:
   ```bash
   mkdir -p reference
   git clone --depth 1 https://github.com/infiniflow/ragflow.git reference/ragflow
   ```
4. Confirm you understand the current task. If the user's request is ambiguous, ask one clarifying question before coding.

## Skills policy (MANDATORY)

Using skills is not optional. Before any non-trivial task, check `/mnt/skills/` for relevant `SKILL.md` files and read them with the `view` tool. Skills contain condensed best practices that beat general reasoning.

Common mappings for this project:

- Writing any frontend component, page, or UI mockup: read `frontend-design/SKILL.md` FIRST
- Exporting data to Word documents: read `docx/SKILL.md`
- Exporting data to Excel: read `xlsx/SKILL.md`
- Generating PDF reports for users: read `pdf/SKILL.md`
- Generating presentations: read `pptx/SKILL.md`
- Reading user-uploaded files during development or tests: read `file-reading/SKILL.md`
- Writing any user-facing marketing or onboarding copy: read `humanizer/SKILL.md`
- Creating a new project-specific skill: read `skill-creator/SKILL.md`

Rule: if a `SKILL.md` exists for what you are about to do, read it before you write any code for that task. Not after.

## Plugins policy (MANDATORY)

At the start of each session, list and consider available Claude Code plugins. Use them when they apply instead of writing equivalent logic from scratch. Plugins to actively look for and use when relevant:

- Git plugins for commit message generation, branch management, PR descriptions
- Database plugins for migration management
- Docker plugins for container builds and compose orchestration
- Testing plugins for test scaffolding
- Linting and formatting plugins

If a plugin can do the task faster and more reliably than hand-written code, use the plugin. Document in the commit message which plugin was used.

## RAGFlow: inspiration, not copy

RAGFlow is Apache 2.0, cloned into `reference/ragflow/`. It exists for reading only. It must be in `.gitignore`. Never import from it, never copy files out of it.

Before implementing a feature that RAGFlow also solves, read the relevant RAGFlow module to understand their approach, then implement your own cleaner version in our stack. Study the thinking, not the code.

Modules worth studying:

- `deepdoc/` - document parsing for complex PDFs with tables, layouts, and OCR fallbacks. This is where RAGFlow invested the most engineering effort. Read it before writing any parser.
- `rag/app/` - template-based chunking strategies for different document types. The insight that invoices, contracts, and emails need different chunkers is important.
- `agent/` - tool calling architecture and canvas model.
- `api/apps/` - multi-tenancy scaffolding, dataset and assistant management endpoints.
- Their retrieval pipeline: multi-recall plus fused reranking. Sequence and weights matter.

Our stack and architecture differ from theirs in several places on purpose (see next section). Do not propagate their Flask, MySQL, or Elasticsearch choices into our code.

## Stack (non-negotiable)

**Backend**
- Python 3.12+
- FastAPI (async, not Flask)
- SQLAlchemy 2.x async with asyncpg
- Pydantic v2 for schemas
- Postgres 16 as the structured store, with pgvector and tsvector (for BM25-style full-text search)
- Qdrant for vector storage with multi-vector support (ColPali-ready)
- Redis for caching and task queues
- Celery or Arq for background jobs (parsing, embedding)

**AI layer**
- Agent orchestration: Pydantic AI. All agents, tool calls, and LLM-driven logic go through it. See "Pydantic AI usage" section below.
- LLM providers via Pydantic AI: Anthropic Claude in production, Ollama running Qwen2.5 for development and cost-sensitive paths. Model selection is a config switch, not a code change.
- Embeddings: BGE-M3 (dense + sparse + multi-vector in one model, 100+ languages). Used directly via sentence-transformers or FastEmbed. Not wrapped in Pydantic AI.
- Reranker: `bge-reranker-v2-m3` or `jina-reranker-v2-base-multilingual`. Used directly.
- OCR: PaddleOCR (same as RAGFlow, proven on Arabic)
- Document parsing: Docling as primary, Unstructured.io as fallback for edge cases
- Contextual Retrieval technique (Anthropic, 2024): enrich chunks with LLM-generated context before embedding. The enrichment call goes through Pydantic AI.

**Frontend**
- Next.js 14+ (App Router)
- TypeScript strict mode
- Tailwind CSS
- shadcn/ui component library
- i18next for French, Arabic (with RTL), English
- React Query for data fetching

**Infrastructure**
- Docker and Docker Compose for local and VPS deployment
- Target deployment: single VPS initially (OVH or similar), horizontal-ready architecture
- Nginx or Caddy as reverse proxy
- Environment variables for all secrets, never hardcoded

## Architecture principles

1. **Multi-tenant from day one.** Every table has a `tenant_id` column. Postgres Row-Level Security enforces isolation at the database layer, not the application layer. Middleware sets the current tenant on every request.

2. **Hybrid search is the default.** Pure vector search is not acceptable. Every retrieval goes: BGE-M3 dense + sparse -> Postgres full-text (BM25-style) -> Reciprocal Rank Fusion -> reranker -> top-k. This is one pipeline, not multiple code paths.

3. **Contextual Retrieval baked in.** Chunks are enriched with LLM-generated context before embedding. Use Ollama for this in dev, Claude Haiku in production for cost.

4. **Connectors are pluggable.** ERP integrations, email sync, Google Drive, and other sources all implement a common `Connector` interface. Adding a new source does not require changing the retrieval or chat code.

5. **Parsing is modular.** Different document types have different parsers and chunkers. Invoices, contracts, emails, and generic PDFs each get their own template. Based on RAGFlow's template pattern but our own implementation.

6. **Agent-based query routing via Pydantic AI.** A Pydantic AI agent decides whether a query needs semantic retrieval, structured lookup, an ERP tool call, or a combination. Tools are defined as typed Python functions registered with the agent. Not every query goes through RAG.

7. **French and Arabic are first-class.** Never hardcode UI strings. All user-facing text goes through the i18n layer. Right-to-left layouts work in Arabic. Test data includes real French contracts and Arabic documents.

## Pydantic AI usage

Pydantic AI is the agent orchestration layer. It sits between raw LLM calls and business logic. Use it for anything that involves tool calling, structured outputs, multi-step reasoning, or streaming chat.

**Use Pydantic AI for:**

- The query router agent that decides RAG vs SQL vs ERP lookup
- The chat agent that holds conversation state and calls retrieval tools
- The contextual retrieval enrichment agent (adds context to chunks before embedding)
- Structured extraction from documents (invoice fields, contract clauses) when LLM extraction is needed
- Any future agent that exposes ERP connectors as tools
- Streaming chat responses to the frontend

**Do NOT use Pydantic AI for:**

- Direct embedding calls (use BGE-M3 directly, no agent wrapper)
- Reranker calls (direct inference)
- Simple one-shot prompts with no tools and no structured output where the Anthropic SDK is equivalent and lighter (rare, prefer Pydantic AI for consistency)
- Vector search (that's Qdrant, not an LLM operation)

**Patterns to follow:**

- Define agents in `app/agents/` with one agent per file. Name them by role: `router.py`, `chat.py`, `enrichment.py`, `extractor.py`.
- Define tools as typed async functions. Register them with the agent via decorators. Each tool gets a docstring because Pydantic AI surfaces it to the LLM.
- Use `RunContext` with typed dependencies for anything a tool needs (database session, current tenant, vector client). Never pull globals from inside a tool.
- Return Pydantic models from agents, not raw strings. Validate everything at the boundary.
- For chat, use the streaming API and forward tokens to the frontend via Server-Sent Events or WebSocket.

**Provider configuration:**

- Configure models in `app/llm/providers.py` as named constants: `CLAUDE_SONNET`, `CLAUDE_HAIKU`, `OLLAMA_QWEN`. Agents reference these constants, not raw strings.
- Read the active provider from environment variables. Default to Ollama in development, Claude in production.
- Set up Logfire (Pydantic's observability tool) in non-dev environments for tracing agent runs. Free tier is fine for v0.

**Testing agents:**

- Pydantic AI has a `TestModel` that returns canned responses without hitting a real LLM. Use it in unit tests.
- For integration tests, use Ollama with a small model so tests don't cost money and don't require internet.

## Project layout

```
project-root/
  backend/
    app/
      parsers/          # Document parsing, per type. Inspired by RAGFlow deepdoc.
      embeddings/       # BGE-M3 wrapper, batch embedding service
      retrieval/        # Hybrid search, RRF, reranking
      agents/           # Pydantic AI agents (router, extractor, chat). Tool definitions.
      connectors/       # Source integrations (ERPs, email, Drive) exposed as Pydantic AI tools
      api/              # FastAPI routes
      models/           # Pydantic schemas (request/response)
      db/               # SQLAlchemy models, Alembic migrations
      tenancy/          # Multi-tenant middleware, RLS helpers
      llm/              # Pydantic AI model configs, provider setup, shared prompts
      core/             # Config, logging, exceptions
    tests/
    pyproject.toml
    Dockerfile
  frontend/
    src/
      app/              # Next.js app router pages
      components/       # UI components
      lib/              # API client, utilities
      locales/          # fr/, ar/, en/ translation files
      hooks/
    package.json
    Dockerfile
  reference/
    ragflow/            # Cloned for reading only. Gitignored.
  docker/
    docker-compose.yml
    docker-compose.prod.yml
  docs/
    design/             # Design docs per feature
    adr/                # Architecture decision records
  scripts/
    setup.sh
  CLAUDE.md             # This file
  README.md
  .gitignore
  .env.example
```

## Coding conventions

**Python**
- Black formatter, ruff linter, mypy in strict mode
- Type hints on every function signature
- Async everywhere. No sync DB calls in request handlers.
- Docstrings on public functions (Google style)
- Small modules. If a file passes 300 lines, split it.
- Custom exceptions, never bare `except:`

**TypeScript**
- Strict mode. No `any`. No `@ts-ignore`.
- Functional components only
- Server components by default, client components only when needed
- Co-locate component styles with the component

**General**
- No em dashes anywhere. Use commas, periods, or parentheses.
- Sentence case for UI labels, not Title Case
- Error messages must be specific and actionable
- Commit messages in English, imperative mood: "Add invoice parser" not "Added invoice parser"
- Branch names: `feat/invoice-parser`, `fix/arabic-rtl-chat`, `chore/upgrade-fastapi`

## Testing

- Pytest for backend. Aim for 80% coverage on parsers, retrieval, and agents.
- Playwright for frontend end-to-end tests.
- Every parser must have tests against real sample documents in `tests/fixtures/` including French contracts and Arabic invoices.
- Never ship a feature without testing French and Arabic input paths.
- Integration tests spin up the full Docker Compose stack.

## Security and compliance

- Tenant isolation via Postgres RLS, enforced at the database, not the code
- All secrets in environment variables, never in code, never in git
- API rate limiting per tenant
- Audit log for document access and mutations
- Encrypted storage at rest for uploaded files
- HTTPS only in production
- Data residency aware: clients may require Moroccan or EU hosting

## What NOT to do

- Do not use Flask. We use FastAPI.
- Do not use MySQL. We use Postgres.
- Do not use Elasticsearch. We use Qdrant + Postgres FTS.
- Do not use LangChain or LlamaIndex as the agent framework. We use Pydantic AI.
- Do not call the Anthropic or Ollama SDKs directly for anything that could be a Pydantic AI agent or tool. Consistency matters for observability and testing.
- Do not copy RAGFlow code verbatim. Read, understand, implement your own.
- Do not skip types.
- Do not write sync database code in request handlers.
- Do not hardcode UI strings.
- Do not commit `.env`, `node_modules/`, `__pycache__/`, or `reference/ragflow/`.
- Do not add dependencies without justifying them in the PR.
- Do not skip reading `SKILL.md` files before starting relevant tasks.
- Do not invent architecture decisions that contradict this document.

## Workflow for any new feature

1. Read the relevant sections of this file
2. Check if a skill applies. If yes, read the `SKILL.md`.
3. Check if a plugin applies. If yes, use it.
4. Read the relevant RAGFlow module in `reference/ragflow/` for inspiration
5. Write a short design note in `docs/design/` if the feature is non-trivial
6. Implement backend first, with tests
7. Implement frontend
8. Test with real French and Arabic documents
9. Commit with a clear message
10. Update the README if deployment or setup changed

## When stuck

- Re-read this file
- Check RAGFlow's implementation in `reference/ragflow/` for reference
- Read the SKILL.md for any relevant skill
- Ask the user one specific question rather than guessing

## Quick reference commands

```bash
# Clone RAGFlow reference (run once)
git clone --depth 1 https://github.com/infiniflow/ragflow.git reference/ragflow

# Backend dev
cd backend
uv sync
uv run uvicorn app.main:app --reload

# Frontend dev
cd frontend
npm install
npm run dev

# Full stack local
docker compose -f docker/docker-compose.yml up

# Run tests
cd backend && uv run pytest
cd frontend && npm test
```

## Current phase

v0: Project bootstrap, repo structure, Docker compose skeleton, database schema, auth, basic document upload and parsing.

Do not build features from later phases until v0 is solid.