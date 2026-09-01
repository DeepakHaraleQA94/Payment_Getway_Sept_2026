"""Audit logging helper: append-only trail for financial and admin mutations."""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    tenant_id=None,
    actor_id: str | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    changes: dict | None = None,
) -> None:
    entry = AuditLog(
        created_at=datetime.now(timezone.utc),
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=ip_address,
        changes=changes or {},
    )
    db.add(entry)
    # Flush so it participates in the caller's transaction; caller commits.
    await db.flush()
