"""Shared fixtures for integration tests.

Integration tests hit a running compose stack. The defaults auto-detect
which side of the network you're on:

- From the host: Postgres at `localhost:5432`, backend at
  `http://localhost:8000` (compose publishes both ports).
- From inside the backend container (`docker compose exec backend uv run
  pytest`): Postgres at `postgres:5432` (in-network hostname), backend
  at `http://localhost:8000` (uvicorn shares the container's loopback).

`/.dockerenv` is the Docker-installed marker file used to tell the two
apart. Override either default with `INTEGRATION_DATABASE_URL` /
`INTEGRATION_BACKEND_URL` if you need to point elsewhere.

Bring the stack up before `uv run pytest` — see
docs/design/v1-pipeline.md §Verification.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Tenant


def _default_database_url() -> str:
    if Path("/.dockerenv").exists():
        return "postgresql+asyncpg://rag:rag@postgres:5432/rag"
    return "postgresql+asyncpg://rag:rag@localhost:5432/rag"


DATABASE_URL = os.environ.get("INTEGRATION_DATABASE_URL", _default_database_url())
BACKEND_URL = os.environ.get("INTEGRATION_BACKEND_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
async def host_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def make_tenant(
    host_engine: AsyncEngine,
) -> AsyncIterator[Callable[[], Awaitable[UUID]]]:
    """Factory fixture: returns an async callable that inserts a fresh tenant
    row and remembers it for teardown. Cascading FKs clean up documents and
    chunks automatically.
    """
    sessionmaker = async_sessionmaker(host_engine, expire_on_commit=False)
    created: list[UUID] = []

    async def _make() -> UUID:
        tid = uuid4()
        async with sessionmaker() as sess, sess.begin():
            sess.add(Tenant(id=tid, name=f"integration-test-{tid}"))
        created.append(tid)
        return tid

    yield _make

    if created:
        async with sessionmaker() as sess, sess.begin():
            await sess.execute(delete(Tenant).where(Tenant.id.in_(created)))
