"""APScheduler jobs: webhook retries (interval) and daily report generation (cron)."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.database import AsyncSessionLocal
from app.services import alert_service, report_generation, webhook_service

logger = logging.getLogger("cloudpay.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _retry_job():
    async with AsyncSessionLocal() as db:
        try:
            n = await webhook_service.process_due_retries(db)
            if n:
                logger.info("processed %s webhook retries", n)
        except Exception as exc:
            logger.error("webhook retry job failed: %s", exc)


async def _daily_report_job():
    async with AsyncSessionLocal() as db:
        try:
            n = await report_generation.generate_for_all_tenants(db)
            logger.info("generated daily reports for %s tenants", n)
        except Exception as exc:
            logger.error("daily report job failed: %s", exc)


async def _alert_eval_job():
    async with AsyncSessionLocal() as db:
        try:
            n = await alert_service.evaluate_all(db)
            logger.debug("evaluated provider health alerts for %s tenants", n)
        except Exception as exc:
            logger.error("alert evaluation job failed: %s", exc)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(_retry_job, IntervalTrigger(seconds=30), id="webhook_retries",
                       replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(_daily_report_job, CronTrigger(hour=8, minute=0), id="daily_reports",
                       replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.add_job(_alert_eval_job, IntervalTrigger(seconds=60), id="provider_alerts",
                       replace_existing=True, max_instances=1, coalesce=True)
    _scheduler.start()
    logger.info("scheduler started (webhook retries 30s, alerts 60s, daily reports 08:00 UTC)")
