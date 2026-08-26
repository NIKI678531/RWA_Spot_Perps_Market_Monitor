"""Aggregates the feature routers. Add new route modules here."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    alert_queue,
    alerts,
    benchmark,
    candidates,
    context,
    dex,
    editions,
    governance,
    health,
    home,
    issuers,
    kpi,
    perps,
    quality,
    reports,
    review,
    scale,
    spot,
    tasks,
    themes,
    timeseries,
    underlying,
    underlyings,
)

api_router = APIRouter()

# Ordered as the product reads: shell, decision, work queue, research, market
# structure, then governance and the operational tail.
api_router.include_router(health.router)
api_router.include_router(context.router)
api_router.include_router(home.router)
api_router.include_router(kpi.router)

# Before ``alerts``: that router declares ``GET /alerts/{alert_id}`` with an ``int``
# path parameter, and FastAPI matches in registration order. Mounted the other way
# round, ``/alerts/queue`` is captured by the detail route and fails validation
# instead of falling through to the queue.
api_router.include_router(alert_queue.router)
api_router.include_router(alerts.router)
api_router.include_router(tasks.router)

api_router.include_router(underlyings.router)
# The pre-R1 singular path, kept answering so old links resolve. Mounted after the
# canonical one so the plural route is what the OpenAPI schema leads with.
api_router.include_router(underlying.router)
api_router.include_router(themes.router)
api_router.include_router(candidates.router)

api_router.include_router(scale.router)
api_router.include_router(spot.router)
api_router.include_router(dex.router)
api_router.include_router(issuers.router)
api_router.include_router(perps.router)
api_router.include_router(benchmark.router)
api_router.include_router(timeseries.router)

api_router.include_router(governance.router)
api_router.include_router(quality.router)
api_router.include_router(editions.router)
api_router.include_router(review.router)
api_router.include_router(reports.router)
