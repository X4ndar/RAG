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
- LLM providers: BYOM (bring your own model). Every tenant configures their own provider: an external API (Anthropic, OpenAI, Google, Mistral, or any OpenAI-compatible endpoint) or a self-hosted endpoint (Ollama, vLLM, LM Studio, text-generation-inference). We never host LLMs, never resell API capacity, never hardcode a provider. If a tenant has not configured a provider, LLM-dependent features (chat, contextual retrieval, extraction) are unavailable for that tenant. See "LLM provider strategy (BYOM)" section below.
- Embeddings: BGE-M3 (dense + sparse + multi-vector in one model, 100+ languages). Used directly via sentence-transformers or FastEmbed. Not wrapped in Pydantic AI. Runs on our infrastructure, not tenant-configured. Embeddings are a product primitive, not a customer choice.
- Reranker: `bge-reranker-v2-m3` or `jina-reranker-v2-base-multilingual`. Used directly. Same reasoning as embeddings: our infrastructure, not tenant-configured.
- OCR: PaddleOCR (same as RAGFlow, proven on Arabic)
- Document parsing: Docling as primary, Unstructured.io as fallback for edge cases
- Contextual Retrieval technique (Anthropic, 2024): enrich chunks with LLM-generated context before embedding. The enrichment call goes through Pydantic AI and uses the tenant's configured provider.

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

3. **Contextual Retrieval baked in.** Chunks are enriched with LLM-generated context before embedding. The enrichment uses the tenant's configured LLM provider. If the tenant has no provider configured, chunks are embedded without enrichment (degraded but functional retrieval).

4. **Connectors are pluggable.** ERP integrations, email sync, Google Drive, and other sources all implement a common `Connector` interface. Adding a new source does not require changing the retrieval or chat code.

5. **Parsing is modular.** Different document types have different parsers and chunkers. Invoices, contracts, emails, and generic PDFs each get their own template. Based on RAGFlow's template pattern but our own implementation.

6. **Agent-based query routing via Pydantic AI.** A Pydantic AI agent decides whether a query needs semantic retrieval, structured lookup, an ERP tool call, or a combination. Tools are defined as typed Python functions registered with the agent. Not every query goes through RAG.

7. **French and Arabic are first-class.** Never hardcode UI strings. All user-facing text goes through the i18n layer. Right-to-left layouts work in Arabic. Test data includes real French contracts and Arabic documents.

## LLM provider strategy (BYOM)

The product is bring-your-own-model. Every tenant configures their own LLM provider through the admin UI. The platform never provides a default provider, never hosts LLMs, never resells inference capacity.

**Supported provider types:**

- External APIs via their official endpoints: Anthropic, OpenAI, Google (Gemini), Mistral, Cohere, Groq
- Any OpenAI-compatible endpoint: self-hosted Ollama, vLLM, LM Studio, text-generation-inference, LiteLLM proxy, OpenRouter, Together, Fireworks
- The "OpenAI-compatible custom" option is the catch-all for anything speaking the OpenAI chat completions API

**Tenant configuration:**

Stored in a `tenant_llm_configs` table. One row per tenant (or one per role if a tenant wants different models for chat vs enrichment, future phase). Fields include provider type, endpoint URL, model name, API key (encrypted at rest), and optional per-role overrides.

The admin UI presents a configuration form with:
- Provider dropdown (Anthropic, OpenAI, Google, Mistral, OpenAI-compatible custom, Ollama)
- Conditional fields based on provider choice (URL, key, model name)
- A "test connection" button that sends a trivial prompt and displays the response. No feature depending on LLMs is enabled until this test passes.

**Implementation:**

A provider factory reads the tenant's config and returns a configured Pydantic AI `Agent` or `Model` for each request. No agent, tool, or service hardcodes a provider string. The factory is the single chokepoint where "which model do I call" is resolved.

```python
# Sketch, not final API
async def agent_for_tenant(tenant_id: UUID, role: AgentRole) -> Agent:
    config = await load_tenant_llm_config(tenant_id, role)
    if config is None:
        raise LLMNotConfiguredError(tenant_id, role)
    return build_agent(config, role)
```

**Encryption:**

API keys are encrypted at the application layer using a key derivation function bound to a server-side master key (loaded from env var at startup). Never stored in plaintext. Never logged. Never returned from the API in full (admin UI shows last 4 characters only).

**Failure modes:**

- No provider configured: feature returns 409 with a message pointing to the settings page. No silent fallback to a default.
- Configured provider returns an error: surface the provider's error to the user with a generic wrapper. The user's key and URL are their responsibility.
- Rate limits or quota: pass through to the user. We don't queue or retry against the tenant's account without their explicit policy.

**What this implies for deployment:**

The product can run fully air-gapped. A tenant on a GPU-equipped server can configure `http://ollama:11434/v1` as their OpenAI-compatible endpoint and never make an outbound internet call. This is a deliberate product position, especially for Moroccan SME clients with data residency concerns, legal/healthcare verticals, or offline requirements.

**What is NOT tenant-configured:**

Embeddings, reranking, OCR, and document parsing all run on our infrastructure with our chosen models. These are product primitives, not customer choices. Changing them would change retrieval quality in ways tenants can't evaluate, and supporting arbitrary embedding models complicates the vector store schema. The BYOM choice is specifically about the generation and agent layer.

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

**Provider configuration (BYOM, V1.5a+):**

- The provider is read from the **per-tenant** `tenant_llm_configs` row, not from any platform-level constant or env var. There is no `app/llm/providers.py` with `CLAUDE_SONNET` / `OLLAMA_QWEN` constants and there is no platform-default provider; that earlier paragraph predated the BYOM section above and contradicted it.
- All agents are constructed via `app/llm/factory.py::agent_for_tenant(tenant_id)`. This is the single chokepoint where "which model do I call" is resolved. Agents and tools never instantiate Pydantic AI `Model` or `Provider` classes directly.
- `agent_for_tenant` raises `LLMNotConfiguredError` when the tenant has no row; the API layer maps this to 409. Missing master key → `MasterKeyMissingError` → 503 (single FastAPI handler in `app.main`).
- Logfire is **not** wired up in V1.5a (its OpenTelemetry transitive deps conflict with our pinned versions; pytest disables the plugin via `-p no:logfire`). Re-evaluate when V2 needs production tracing.

**Testing agents:**

- Pydantic AI's `TestModel` returns canned responses without a real LLM. The supported way to inject it is `app.dependency_overrides[get_model_factory] = lambda: stub_factory` against the in-process FastAPI app. Production code reads no env-var swap flag; dependency injection is the only swap path.
- Worker-style call sites (anything that imports `build_model` directly) test via `monkeypatch.setattr(app.llm.factory, "build_model", stub)`.

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
- Do not hardcode any LLM provider, model name, API key, or endpoint URL in the codebase. Every LLM call resolves its provider through the tenant config factory. No exceptions, including for development scripts and tests (tests use a mock provider or Pydantic AI's TestModel).
- Do not ship a default LLM provider or platform-owned API key. The product is BYOM. If a tenant has not configured a provider, LLM-dependent features are disabled for that tenant, not served from a shared pool.
- Do not log API keys, even partially, outside the database layer. Mask them before any log line, error message, or response body leaves the provider factory.
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

# One-shot setup
bash scripts/setup.sh                                          # clones ragflow, copies .env

# Full stack (dev)
docker compose --env-file .env -f docker/docker-compose.yml up --build

# Migrations (always container-side)
scripts/migrate.sh                                              # upgrade head
scripts/migrate.sh revision --autogenerate -m "add foo"

# Backend dev (host-side imports for editor/IDE)
cd backend && uv sync --extra dev

# Frontend dev (npm, not pnpm)
cd frontend && npm install && npm run dev

# Tests inside the backend container (avoids cygwin process limits on Windows).
# Integration conftest auto-detects /.dockerenv and chooses postgres:5432
# vs localhost:5432, so the -e INTEGRATION_* flags are NOT needed.
docker compose --env-file .env -f docker/docker-compose.yml exec \
    backend uv run --extra dev pytest --no-cov

# V1.5a smoke — configure provider, test, save
TENANT=$(uuidgen)
psql -h localhost -U rag -d rag -c "INSERT INTO tenants (id, name) VALUES ('$TENANT','byom');"
curl -X POST http://localhost:8000/api/v1/admin/llm-config \
     -H "X-Tenant-ID: $TENANT" -H "Content-Type: application/json" \
     -d '{"provider":"anthropic","model_name":"claude-sonnet-4-5","api_key":"sk-ant-test-12345"}'
curl -X POST http://localhost:8000/api/v1/admin/llm-config/test \
     -H "X-Tenant-ID: $TENANT" -H "Content-Type: application/json" \
     -d '{"provider":"anthropic","model_name":"claude-sonnet-4-5","api_key":"sk-ant-test-12345"}'
curl http://localhost:8000/api/v1/admin/llm-config -H "X-Tenant-ID: $TENANT"

# V1 smoke — upload, poll, search
TENANT=$(uuidgen)
psql -h localhost -U rag -d rag -c "INSERT INTO tenants (id, name) VALUES ('$TENANT','smoke');"
curl -X POST http://localhost:8000/api/v1/documents \
     -H "X-Tenant-ID: $TENANT" \
     -F "file=@backend/tests/fixtures/fr_sample.pdf"
# poll: curl http://localhost:8000/api/v1/documents/<id> -H "X-Tenant-ID: $TENANT"
curl "http://localhost:8000/api/v1/search?q=le+sujet+principal&top_k=5" \
     -H "X-Tenant-ID: $TENANT"
```

## Current phase

v0.3.0 (V1.5a) shipped: BYOM configuration plumbing. Tenants configure their own LLM provider through `/settings/llm-provider`; credentials encrypted at rest with Fernet (master key from `LLM_CONFIG_MASTER_KEY`); `app/llm/factory.py::agent_for_tenant(tenant_id)` is the single chokepoint that turns a saved config into a Pydantic AI `Agent`. Six providers: Anthropic, OpenAI, Google, Mistral, OpenAI-compatible custom, Ollama. POST `/api/v1/admin/llm-config/test` runs a 5-15 s connection probe and returns a UI-safe pass/fail. See [`docs/design/v1.5a-byom-config.md`](./docs/design/v1.5a-byom-config.md).

V1.5b (next phase): chat UI, answer agent, master-key rotation tooling. Do not build these yet.

v0.2.0 (V1) shipped: single-document RAG pipeline. POST a PDF, the worker parses it (Docling, no OCR), chunks it (token-aware, BGE-M3 tokenizer), embeds chunks (BGE-M3 dense via sentence-transformers, CPU), upserts to Qdrant (collection `documents_v1`, 1024-dim cosine, payload-indexed on `tenant_id` + `document_id`). GET /api/v1/search returns top-k chunks scoped to the caller's tenant. Cross-tenant isolation proven by mutation-tested integration test. See [`docs/design/v1-pipeline.md`](./docs/design/v1-pipeline.md).

V1.5 (next phase): chat UI, Pydantic AI agents, duplicate-upload 409. Do not build these yet.

## V1 deviations and lessons (learned the hard way)

These are the things future Claude Code sessions need to know before touching the V1 surface. They are not contradictions of the rules above, just facts about the current implementation that surprised the V1 build.

- **Embeddings: sentence-transformers, not FastEmbed.** CLAUDE.md permits either. We tried FastEmbed first and pivoted. Reasons documented in [`docs/design/v1-pipeline.md`](./docs/design/v1-pipeline.md): (1) Docling already pulls Torch in as a transitive dep, so the "no Torch in runtime" win is void; (2) FastEmbed's built-in catalog has no `bge-m3`; (3) `add_custom_model` against `BAAI/bge-m3` fails (ONNX in subdir, downloader skips); (4) community flat-layout exports load but emit a non-standard shape FastEmbed can't normalize. Re-evaluate in V2 if CPU latency becomes the bottleneck.
- **Embedding cache volume is `embedding_cache`**, not `fastembed_cache`. Renamed during the swap; provider-agnostic. Mounted at `/app/.cache/embeddings` on backend AND worker. Setting: `EMBEDDING_CACHE_DIR`. Hardcoding the old name will not match the live volume.
- **CPU embedding takes ~3-4 s per chunk.** An 8-page Wikipedia PDF (~30 chunks) processes end-to-end in ~140 s; two in parallel via `arq max_jobs=2` finish in ~200 s. The integration polling timeout is **240 s**, not the 60 s the design originally proposed. Don't budget downstream features against the optimistic original number.
- **Integration tests run inside the backend container.** The Windows host hits a Cygwin process-table exhaustion (`TP_NUM_C_BUFS too small`) after enough subprocess spawns, so we run pytest via `docker compose exec backend uv run --extra dev pytest`. The conftest auto-detects `/.dockerenv` and switches its default DB host between `postgres:5432` (in-container) and `localhost:5432` (host); `INTEGRATION_DATABASE_URL` and `INTEGRATION_BACKEND_URL` are still honoured as overrides for non-default setups.
- **Migrations always run inside the container.** [`scripts/migrate.sh`](./scripts/migrate.sh) wraps `docker compose exec backend alembic`. Host-side `alembic` resolves the `postgres:5432` hostname against nothing.
- **Backend image carries X11/XCB libs** (`libxcb1`, `libxext6`, `libsm6`, `libxrender1`, `libgl1`, `libglib2.0-0`). Docling's image-processing deps (Pillow, qpdf) need them even when we render headless. A future image-slimming pass will be tempted to remove them; don't, without re-running `tests/integration/test_pipeline.py` against the slimmed image.
- **Tenant isolation is two layers in V1.** Qdrant payload filter (primary) plus Postgres `tenant_scoped()` on hydration (defense-in-depth). The integration test asserts both `len(hits) == top_k` and `hit["document_id"] == doc.id`, so removing either filter alone now breaks the test. RLS lands in V3.
- **`worker` and `backend` are separate compose-built images** (`rag-saas-backend:latest` and `rag-saas-worker:latest`). Same Dockerfile, separate tags. After `docker compose build backend` the worker still uses its own image; rebuild it with `docker compose build worker` if the Dockerfile changed. `docker compose up -d --force-recreate backend worker` after rebuilds.

## V1.5a deviations and lessons

- **Pydantic AI is `pydantic-ai-slim[anthropic,openai,google,mistral]>=1.85,<2`.** The umbrella `pydantic-ai` 1.0/1.1 series referenced anthropic SDK symbols (`UserLocation`) that have since been removed; 1.85+ tracks current SDKs. Cap is `<2` because the 2.x line will be a separate breaking-change decision for V1.5b.
- **Ollama routes through `OpenAIChatModel`**, not a dedicated `OllamaModel`. As of pydantic-ai-slim 1.90 there is no `OllamaModel` class — Ollama is OpenAI-compatible by contract, so `factory.build_model` dispatches `ollama` to `OpenAIChatModel + OpenAIProvider(base_url=...)`. The user-facing form still shows "Ollama" as a discrete provider; routing is internal.
- **Logfire pytest plugin is disabled** via `-p no:logfire` in `pyproject.toml`'s addopts. It auto-loads from a transitive Pydantic AI dep and has an OpenTelemetry version conflict that surfaces during test discovery. We don't use Logfire in tests.
- **`MasterKeyMissingError` is a single chokepoint.** `app/llm/encryption.py::_load_fernet()` is the only function that raises it; one FastAPI exception handler in `app.main` maps to 503. Endpoints don't catch it themselves. A unit test in `tests/unit/test_encryption.py::test_master_key_missing_chokepoint_is_load_fernet` enforces this invariant by inspecting the encryption module's source.
- **Test mocking is dependency injection only.** `app/llm/factory.py::get_model_factory` is the FastAPI dep; tests override via `app.dependency_overrides[get_model_factory]`. There is no env-var swap flag in production code (`tests/integration/test_secret_hygiene_endpoint.py::test_only_dependency_injection_can_swap_the_factory` greps the route module to enforce this).
- **V1.5a integration tests run in-process via `httpx.ASGITransport`**, not against `localhost:8000`. The reason is that `app.dependency_overrides` only takes effect on the same process that owns the FastAPI `app`. V1's worker-pipeline tests still hit the live container because they exercise an out-of-process worker; both styles coexist.

Do not build features from later phases until V1.5b brainstorm starts.