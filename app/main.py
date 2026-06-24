from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_session
from app.core.observability import init_sentry
from app.modules.price_history.models import PriceHistory

init_sentry(web=True)

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/freshness")
async def freshness(
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Data-freshness probe, distinct from liveness.

    The newest price_history row stops advancing if the worker dies or a poll
    silently fails, so a stale (or absent) newest row signals an outage even
    while the process itself is happily serving /health.
    """
    newest = await session.scalar(select(func.max(PriceHistory.observed_at)))

    max_age = timedelta(minutes=settings.price_freshness_max_age_minutes)
    now = datetime.now(timezone.utc)
    if newest is not None and now - newest <= max_age:
        return JSONResponse({"status": "fresh", "newest": newest.isoformat()})

    detail = (
        "no price_history rows found"
        if newest is None
        else f"newest price_history row is older than {max_age}"
    )
    return JSONResponse(
        status_code=503,
        content={
            "type": "about:blank",
            "title": "Price data is stale",
            "status": 503,
            "detail": detail,
        },
        media_type="application/problem+json",
    )
