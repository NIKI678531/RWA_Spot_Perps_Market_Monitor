"""The tokenized wrapper beside the security it wraps.

Every other endpoint reports what the token did. This one answers whether what the
token did was *right*: a wrapper trading 4% above its share is either an arbitrage or
a broken oracle, and neither is visible from the token's own price series.

Three things this endpoint refuses to hide:

* **The reference is usually stale.** RWA tokens trade continuously and the NYSE does
  not, so over a weekend ``reference_price_ts`` is two days behind ``as_of``. The age
  is returned in minutes rather than left for the reader to work out, because a basis
  quoted without it turns every closed market into an apparent mispricing.
* **A 1:1 wrapper is an assumption.** The basis is a price ratio, and it only means
  what it appears to mean if one token is one share. A wrapper on a different ratio
  produces a large, stable basis that is an artefact of the units — which is why the
  raw prices are returned alongside and the note says so.
* **No source configured is not a market without prices.** With no reference feed the
  rows are empty and ``unavailable_reason`` says why.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter

from app.api.deps import DatasetDep, Limit
from app.models.facts import FactUnderlyingReference
from app.schemas.common import Meta
from app.schemas.market import BenchmarkList, BenchmarkRow
from app.services.ingest.alpaca import AlpacaCollector
from app.services.report.dataset import AssetRow, ReportDataset, age_minutes

router = APIRouter(tags=["benchmark"])

_NOTE = (
    "The token price and the reference price sit side by side; basis is their ratio "
    "and is meaningful only for a 1:1 wrapper — a token representing a fraction of a "
    "share shows a large, steady basis that is a unit artefact, not a mispricing. "
    "reference_age_minutes is not a fault: the underlying market is shut most of the "
    "hours this system collects, and a basis read without it turns every weekend "
    "into a dislocation."
)

_UNCONFIGURED = (
    "No TradFi reference source is configured, so there is nothing to compare "
    "tokenized prices against. Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY; the "
    "hourly job picks the source up on its own from there. This is a missing "
    "observation, not a market in which nothing has a price."
)


@router.get("/benchmark", response_model=BenchmarkList)
def benchmark(data: DatasetDep, limit: Limit = 200) -> BenchmarkList:
    references = {r.underlying_id: r for r in data.references}

    rows: list[BenchmarkRow] = []
    for asset in data.scoped_assets:
        underlying_id = asset.asset.underlying_id
        if underlying_id is None:
            continue
        reference = references.get(underlying_id)
        if reference is None:
            # No quote for this underlying. Emitting a row of nulls would pad the
            # table with entries that look observed and are not.
            continue
        rows.append(_row(asset, underlying_id, reference, data.as_of))

    # Widest gap first: a basis is only interesting when it is large, and a table
    # sorted alphabetically buries the one row worth looking at.
    rows.sort(key=lambda r: (r.basis is None, -abs(r.basis or 0.0), r.symbol))

    return BenchmarkList(
        meta=Meta(
            as_of=data.as_of,
            # Deliberately empty: a price belongs to none of the five metric
            # families, and nothing on this endpoint is ever summed.
            scopes=[],
            note=_NOTE,
            row_count=len(rows),
        ),
        rows=rows[:limit],
        unavailable_reason=_reason(data),
    )


def _row(
    asset: AssetRow,
    underlying_id: str,
    reference: FactUnderlyingReference,
    as_of: datetime,
) -> BenchmarkRow:
    token_price = asset.snapshot.price_usd if asset.snapshot else None
    return BenchmarkRow(
        underlying_id=underlying_id,
        underlying_name=asset.underlying.name if asset.underlying else underlying_id,
        asset_id=asset.asset.asset_id,
        symbol=asset.asset.symbol,
        issuer_id=asset.asset.issuer_id,
        token_price=token_price,
        reference_price=reference.price,
        reference_price_ts=reference.price_ts,
        reference_age_minutes=age_minutes(reference.price_ts, as_of),
        feed=reference.feed,
        basis=_basis(token_price, reference.price),
        token_change_24h=asset.snapshot.change_24h if asset.snapshot else None,
        reference_change_24h=reference.change_24h,
        market_session=reference.market_session,
    )


def _basis(token: Decimal | None, reference: Decimal | None) -> float | None:
    """Token price over reference price, minus one.

    Null rather than zero when either side is missing: a basis of zero says the two
    agreed, which is a much stronger claim than not having looked.
    """
    if token is None or reference is None or reference == 0:
        return None
    return float(token / reference - 1)


def _reason(data: ReportDataset) -> str | None:
    """Why the table is empty, when it is empty for a reason we can name."""
    if data.references:
        return None
    if not AlpacaCollector.is_configured():
        return _UNCONFIGURED
    # Configured but nothing collected: the source is scheduled and either has not
    # run yet or failed. The data-quality page carries the fetch outcome that says
    # which, and duplicating a guess at it here would be a second opinion.
    return (
        "A reference source is configured but has written no prices yet. See the "
        "data-quality page for its last fetch outcome."
    )
