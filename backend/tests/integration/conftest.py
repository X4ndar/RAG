"""Shared fixtures for integration tests.

Integration tests hit a running compose stack from the host, so DB
access uses `localhost:5432` rather than the in-network `postgres:5432`
hostname. Bring the stack up before `uv run pytest` — see
docs/design/v1-pipeline.md §Verification.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Tenant

HOST_DATABASE_URL = "postgresql+asyncpg://rag:rag@localhost:5432/rag"
BACKEND_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
async def host_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(HOST_DATABASE_URL, pool_pre_ping=True)
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
