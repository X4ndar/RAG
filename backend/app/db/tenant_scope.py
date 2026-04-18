"""Tenant-scoping helper for SQLAlchemy queries.

The single sanctioned way to query tables that inherit `TenantOwned`. Missing
filter = bug. If a code review ever misses one, write a ruff plugin; for now
the convention is enforced by call-site grep.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select

from app.db.models import TenantOwned


def tenant_scoped[T: TenantOwned](
    stmt: Select[tuple[T]],
    tenant_id: UUID,
    model: type[T],
) -> Select[tuple[T]]:
    """Return the statement with `WHERE tenant_id = :tenant` appended.

    Args:
        stmt: Select statement targeting a `TenantOwned` model.
        tenant_id: UUID of the current tenant, resolved from the FastAPI
            dependency `get_current_tenant`.
        model: The ORM class being selected. Required so callers surface the
            type explicitly; prevents accidental scoping against a join.

    Returns:
        The same statement with an additional `WHERE` clause.
    """
    return stmt.where(model.tenant_id == tenant_id)
