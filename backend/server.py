"""CloudPay API entrypoint. WEB-ONLY multi-tenant payment orchestration platform."""
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.deps import get_current_user, resolve_tenant_id
from app.models.payment import Payment
from app.models.tenant import Tenant
from app.routers import auth, config as config_router, finance, iam, payments, system, tenants
from app.seed import seed
from app.services import turnover_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("cloudpay")

app = FastAPI(title="CloudPay API", version="1.0.0")

_origins = [settings.frontend_url]
if settings.cors_origins and settings.cors_origins != "*":
    _origins = list({*_origins, *[o.strip() for o in settings.cors_origins.split(",")]})

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, tenants.router, iam.router, config_router.router,
          payments.router, finance.router, system.router):
    app.include_router(r)


@app.get("/api/")
async def root():
    return {"service": "CloudPay", "status": "ok", "environment": settings.app_env}


@app.get("/api/dashboard/summary")
async def dashboard_summary(tenant_id: str | None = None, db: AsyncSession = Depends(get_db),
                            user=Depends(get_current_user)):
    tid = resolve_tenant_id(user, tenant_id)
    turnover = await turnover_engine.summarize(db, tenant_id=tid)
    total = await db.execute(select(func.count(Payment.id)).where(Payment.tenant_id == tid))
    succeeded = await db.execute(select(func.count(Payment.id)).where(
        Payment.tenant_id == tid, Payment.status.in_(["succeeded", "captured"])))
    failed = await db.execute(select(func.count(Payment.id)).where(
        Payment.tenant_id == tid, Payment.status == "failed"))
    tenant_count = await db.execute(select(func.count(Tenant.id)))
    total_c = total.scalar() or 0
    succ_c = succeeded.scalar() or 0
    return {
        "turnover": turnover,
        "payments_total": total_c,
        "payments_succeeded": succ_c,
        "payments_failed": failed.scalar() or 0,
        "success_rate": round((succ_c / total_c) * 100, 1) if total_c else 0.0,
        "tenant_count": tenant_count.scalar() or 0,
    }


@app.on_event("startup")
async def on_startup():
    async with AsyncSessionLocal() as db:
        try:
            await seed(db)
            logger.info("CloudPay seed complete (env=%s)", settings.app_env)
        except Exception as exc:  # pragma: no cover
            logger.exception("Seed failed: %s", exc)
