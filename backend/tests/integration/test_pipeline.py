"""Integration tests for the V1 document pipeline.

Requires the compose stack up: `docker compose --env-file .env -f
docker/docker-compose.yml up -d`. Backend listens on localhost:8000,
worker consumes from Redis, Postgres is on localhost:5432. Tests skip
if the French-Wikipedia PDF fixtures don't exist yet (they land in
commit 7).

Two tests:
    * `test_end_to_end_single_tenant` — upload, poll to 'ready', search,
      assert the top hit's document_id matches and scores are descending.
    * `test_tenant_isolation` — the single most important multi-tenancy
      test. Tenant A uploads sample_1, tenant B uploads sample_2; searches
      from A must never see B's document and vice versa. Passing requires
      both the Qdrant tenant filter AND the Postgres tenant filter to
      hold; removing either must make this test fail.

Polling caps at 60 seconds per the V1 design note.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from tests.integration.conftest import BACKEND_URL

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
SAMPLE_1 = FIXTURE_DIR / "fr_sample.pdf"
SAMPLE_2 = FIXTURE_DIR / "fr_sample_2.pdf"

PIPELINE_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 2.0

pytestmark = pytest.mark.skipif(
    not SAMPLE_1.exists() or not SAMPLE_2.exists(),
    reason="French Wikipedia PDF fixtures not yet committed (land in commit 7)",
)


async def _upload(client: httpx.AsyncClient, tenant_id: UUID, pdf: Path) -> str:
    with pdf.open("rb") as fh:
        r = await client.post(
            "/api/v1/documents",
            headers={"X-Tenant-ID": str(tenant_id)},
            files={"file": (pdf.name, fh, "application/pdf")},
        )
    assert r.status_code == 201, f"upload failed: {r.status_code} {r.text}"
    return r.json()["id"]


async def _wait_ready(
    client: httpx.AsyncClient,
    tenant_id: UUID,
    document_id: str,
    timeout_s: float = PIPELINE_TIMEOUT_S,
) -> None:
    deadline = time.monotonic() + timeout_s
    last_status = "unknown"
    while time.monotonic() < deadline:
        r = await client.get(
            f"/api/v1/documents/{document_id}",
            headers={"X-Tenant-ID": str(tenant_id)},
        )
        if r.status_code == 404:
            await asyncio.sleep(POLL_INTERVAL_S)
            continue
        body = r.json()
        last_status = body["status"]
        if last_status == "ready":
            return
        if last_status == "failed":
            pytest.fail(f"pipeline failed for {document_id}: {body.get('error_message')}")
        await asyncio.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"document {document_id} did not reach status=ready within {timeout_s}s "
        f"(last status: {last_status})"
    )


async def _search(
    client: httpx.AsyncClient,
    tenant_id: UUID,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    r = await client.get(
        "/api/v1/search",
        params={"q": query, "top_k": top_k},
        headers={"X-Tenant-ID": str(tenant_id)},
    )
    assert r.status_code == 200, r.text
    return r.json()["results"]


async def test_end_to_end_single_tenant(
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        doc_id = await _upload(client, tenant_id, SAMPLE_1)
        await _wait_ready(client, tenant_id, doc_id)

        results = await _search(client, tenant_id, query="le sujet principal", top_k=5)
        assert results, "search returned no results"
        assert results[0]["document_id"] == doc_id, (
            "top hit must come from the uploaded document"
        )

        scores = [hit["score"] for hit in results]
        assert scores == sorted(scores, reverse=True), (
            "results must be returned in descending Qdrant score order"
        )


async def test_tenant_isolation(
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        doc_a = await _upload(client, tenant_a, SAMPLE_1)
        doc_b = await _upload(client, tenant_b, SAMPLE_2)

        await _wait_ready(client, tenant_a, doc_a)
        await _wait_ready(client, tenant_b, doc_b)

        # Tenant A searches with a generic query that could plausibly match
        # either document. Every result MUST be doc_a.
        hits_a = await _search(client, tenant_a, query="le sujet principal", top_k=10)
        assert hits_a, "tenant A got no hits from their own document"
        for hit in hits_a:
            assert hit["document_id"] == doc_a, (
                f"tenant A saw foreign document_id={hit['document_id']}; "
                "tenant isolation leaked (Qdrant filter or Postgres filter)"
            )

        # Symmetric from tenant B.
        hits_b = await _search(client, tenant_b, query="le sujet principal", top_k=10)
        assert hits_b, "tenant B got no hits from their own document"
        for hit in hits_b:
            assert hit["document_id"] == doc_b, (
                f"tenant B saw foreign document_id={hit['document_id']}; "
                "tenant isolation leaked (Qdrant filter or Postgres filter)"
            )

        # And a cross-tenant GET is 404, never 403.
        r = await client.get(
            f"/api/v1/documents/{doc_b}",
            headers={"X-Tenant-ID": str(tenant_a)},
        )
        assert r.status_code == 404, (
            "cross-tenant GET must 404 (never 403, do not leak existence)"
        )
