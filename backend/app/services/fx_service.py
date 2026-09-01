"""FX engine (foundation). Reads reference rates; falls back to 1.0 for same currency."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform import FxRate


async def convert(db: AsyncSession, *, amount_minor: int, base: str, quote: str) -> dict:
    if base == quote:
        return {"amount_minor": amount_minor, "rate": 1.0, "source": "identity"}
    res = await db.execute(
        select(FxRate)
        .where(FxRate.base_currency == base, FxRate.quote_currency == quote)
        .order_by(FxRate.as_of.desc())
        .limit(1)
    )
    rate_row = res.scalar_one_or_none()
    if rate_row is None:
        return {"amount_minor": None, "rate": None, "source": None, "error": "rate_unavailable"}
    rate = float(rate_row.rate)
    return {"amount_minor": int(amount_minor * rate), "rate": rate, "source": rate_row.source}
