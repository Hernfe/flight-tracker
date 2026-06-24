"""Sentry wiring shared by the web app and the Arq worker.

A missing DSN is a no-op so local development stays quiet without any Sentry
account or network traffic.
"""

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.config import settings


def init_sentry(*, web: bool) -> None:
    """Initialise Sentry when a DSN is configured.

    The FastAPI/Starlette integrations are only attached on the web side; the
    worker has no ASGI app to instrument.
    """
    if not settings.sentry_dsn:
        return

    integrations = [AsyncioIntegration(), SqlalchemyIntegration()]
    if web:
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        integrations.extend([StarletteIntegration(), FastApiIntegration()])

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=getattr(settings, "environment", "dev"),
        traces_sample_rate=0.05,
        integrations=integrations,
    )
