"""Aggregates the feature routers. Add new route modules here."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    alerts,
    dex,
    health,
    issuers,
    kpi,
    perps,
    quality,
    reports,
    scale,
    spot,
    themes,
    timeseries,
    underlying,
)

api_router = APIRouter()

# Ordered as the UI reads: health, headline, then scale, competition, perpetuals,
# demand, alerts, and the operational tail.
api_router.include_router(health.router)
api_router.include_router(kpi.router)
api_router.include_router(scale.router)
api_router.include_router(spot.router)
api_router.include_router(dex.router)
api_router.include_router(issuers.router)
api_router.include_router(perps.router)
api_router.include_router(themes.router)
api_router.include_router(alerts.router)
api_router.include_router(underlying.router)
api_router.include_router(timeseries.router)
api_router.include_router(quality.router)
api_router.include_router(reports.router)
