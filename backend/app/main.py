"""FastAPI application assembly."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.services import scheduler

#: Endpoints live under ``{base}/api`` so a deployment can be mounted at a path
#: prefix without every client hard-coding it.
API_PREFIX = f"{settings.normalized_api_base_path}/api"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start collection on boot, stop it on shutdown.

    Scheduling is opt-in via ``scheduler_enabled``. Baselines need 14 snapshots in
    the same market session, so every day the scheduler is off is a day the
    time-series detectors stay silent — but a dev machine running collectors against
    live rate limits is worse.
    """
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="RWA Spot & Perps Market Monitor",
        description=(
            "Tokenized real-world-asset market monitoring: scale, venue and issuer "
            "competition, cross-venue perpetuals, and demand-anomaly detection."
        ),
        version="0.1.0",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=f"{API_PREFIX}/redoc",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=API_PREFIX)
    return app


app = create_app()
