"""Monitoring service: health checks for dependencies."""
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def db_health(db: AsyncSession) -> dict:
    start = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {"status": "up", "latency_ms": latency_ms}
    except Exception as exc:  # pragma: no cover
        return {"status": "down", "error": str(exc)}
