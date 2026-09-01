"""System: health, monitoring, audit log, and regulated-capability boundaries."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, resolve_tenant_id
from app.models.platform import AuditLog
from app.providers.registry import list_providers
from app.services import ai_voice_service, kyc_service, monitoring_service, vda_service

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    db_status = await monitoring_service.db_health(db)
    overall = "ok" if db_status["status"] == "up" else "degraded"
    return {
        "status": overall,
        "environment": settings.app_env,
        "service": "cloudpay-api",
        "database": db_status,
    }


@router.get("/monitoring/services")
async def monitoring_services(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    db_status = await monitoring_service.db_health(db)
    services = [
        {"name": "API", "status": "up"},
        {"name": "PostgreSQL", "status": db_status["status"], "latency_ms": db_status.get("latency_ms")},
        {"name": "Payment Engine", "status": "up"},
        {"name": "Provider: Mock Sandbox", "status": "up"},
    ]
    # Reflect any registered external provider plugins (beyond the built-in mock).
    for cap in list_providers():
        if cap["key"] == "mock":
            continue
        services.append({
            "name": f"Provider: {cap['display_name']}",
            "status": "up" if cap.get("configured") else "unconfigured",
            "mode": cap.get("mode"),
            "test_mode": cap.get("test_mode"),
        })
    return {
        "services": services,
        "boundaries": {
            "kyc_aml": kyc_service.provider_status(),
            "vda": vda_service.boundary_status(),
            "ai_voice": ai_voice_service.boundary_status(),
        },
    }


@router.get("/audit")
async def list_audit(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                     user=Depends(get_current_user)):
    q = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)
    if not user.is_superadmin:
        q = q.where(AuditLog.tenant_id == user.tenant_id)
    elif tenant_id:
        tid = resolve_tenant_id(user, tenant_id)
        q = q.where(AuditLog.tenant_id == tid)
    res = await db.execute(q)
    logs = res.scalars().all()
    return [{"id": str(l.id), "action": l.action, "resource_type": l.resource_type,
             "resource_id": l.resource_id, "actor_email": l.actor_email, "changes": l.changes,
             "created_at": l.created_at.isoformat()} for l in logs]
