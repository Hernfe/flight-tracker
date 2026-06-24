"""The /health/freshness probe reflects the newest price_history row.

503 when there is nothing recent (dead worker / silent poll failure), 200 once
a fresh row lands. Liveness (/health) stays 200 throughout.
"""

from datetime import datetime, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.db import get_session
from app.main import app
from app.modules.price_history.models import PriceHistory


@pytest_asyncio.fixture
async def client(session_factory):
    async def _override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)


async def test_freshness_503_on_empty_history(client, test_engine):
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE TABLE price_history RESTART IDENTITY")

    resp = await client.get("/health/freshness")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 503
    assert {"type", "title", "detail"} <= body.keys()


async def test_freshness_200_after_recent_row(client, session_factory):
    async with session_factory() as session:
        session.add(
            PriceHistory(
                origin="HEL",
                destination="JFK",
                departure_date=datetime.now(timezone.utc).date(),
                return_date=None,
                cabin="economy",
                stops=0,
                carrier="AY",
                source="duffel",
                cheapest_price_cents=42000,
                mean_top_n_price_cents=43000,
                n_offers=3,
                currency="EUR",
                observed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    resp = await client.get("/health/freshness")
    assert resp.status_code == 200
    assert resp.json()["status"] == "fresh"


async def test_health_liveness_always_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
