"""First-hours measurement for newly listed wrappers (home page Q3).

The hourly series cannot answer "how is the new product doing". A wrapper listed at
14:20 has no 24h figure until the following afternoon, and by then the launch is
over — so the three windows are measured from the listing moment rather than from the
top of an hour, and each is written once and closed.

Why a trailing-24h turnover figure is the right number here, despite being a rolling
one everywhere else: the asset did not exist before ``listed_at``, so at
``listed_at + 1h`` the trailing 24 hours contains exactly the first hour of trading
and nothing else. The same holds at 6h and at 24h. All three windows are at most 24
hours long, which is what makes the rolling figure cumulative rather than a mix of two
periods. It stops being true past 24h, which is why there is no 48h window.

The one caveat is stated on the row rather than argued away: ``listed_at`` is the
first moment *we observed* the wrapper, not an announced listing date. A token that
traded for a day before any source we poll indexed it will carry some of that trading
inside its 24h figure. ``FirstListingRow.is_measured`` on the API side is where a
reader is told which of the two they are looking at.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.sessions import classify_session
from app.models.dimensions import DimAsset, DimPool
from app.models.enums import RwaTier
from app.models.facts import (
    FactAssetSnapshot,
    FactLaunchWindowSnapshot,
    FactPairSnapshot,
    FactPoolSnapshot,
)

logger = logging.getLogger(__name__)

#: The three windows, as (label, length). Ordered shortest first so a single pass
#: closes them in the order they elapse.
WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("1h", timedelta(hours=1)),
    ("6h", timedelta(hours=6)),
    ("24h", timedelta(hours=24)),
)

#: How far back to look for listings. The longest window is 24h; the margin covers a
#: job that did not run — a missed 24h window is still worth writing late, and the
#: row carries its own ``closed_at`` so lateness is visible rather than implied.
LOOKBACK = timedelta(days=3)

#: Tiers in scope. ``NON_RWA`` is reference-only and never enters a rollup (rule 11).
IN_SCOPE = (RwaTier.CORE_RWA, RwaTier.RWA_ADJACENT, RwaTier.SYNTHETIC)


@dataclass(frozen=True, slots=True)
class _Spot:
    """What one asset's pair rows said at a window close."""

    raw: Decimal | None
    adjusted: Decimal | None
    venue_count: int | None


def record_windows(session: Session, now: datetime) -> int:
    """Close every launch window that has elapsed and is not yet recorded.

    Returns how many rows were written. Existing rows are never touched: a window
    already carrying a ``closed_at`` is final, and a correction would be a new
    snapshot at a later ``snapshot_ts`` (fact tables are append-only).
    """
    listings = _recent_listings(session, now)
    if not listings:
        return 0

    recorded = _already_closed(session, [asset_id for asset_id, _ in listings])
    written = 0

    for asset_id, listed_at in listings:
        for label, length in WINDOWS:
            closes_at = listed_at + length
            if closes_at > now or (asset_id, label) in recorded:
                continue
            session.add(_measure(session, asset_id, label, listed_at, closes_at, now))
            written += 1

    if written:
        logger.info("closed %d launch windows", written)
    return written


def _recent_listings(session: Session, now: datetime) -> list[tuple[str, datetime]]:
    """In-scope wrappers first observed recently enough for a window to be open."""
    stmt = (
        select(DimAsset.asset_id, DimAsset.created_at)
        .where(DimAsset.rwa_tier.in_(IN_SCOPE))
        .where(DimAsset.created_at >= now - LOOKBACK)
        .order_by(DimAsset.created_at)
    )
    return [(str(a), b) for a, b in session.execute(stmt).all()]


def _already_closed(session: Session, asset_ids: list[str]) -> set[tuple[str, str]]:
    if not asset_ids:
        return set()
    stmt = select(
        FactLaunchWindowSnapshot.asset_id, FactLaunchWindowSnapshot.window
    ).where(
        FactLaunchWindowSnapshot.asset_id.in_(asset_ids),
        FactLaunchWindowSnapshot.closed_at.isnot(None),
    )
    return {(str(a), str(w)) for a, w in session.execute(stmt).all()}


def _measure(
    session: Session,
    asset_id: str,
    window: str,
    listed_at: datetime,
    closes_at: datetime,
    now: datetime,
) -> FactLaunchWindowSnapshot:
    """Build one closed window row from the newest observation at or before close."""
    spot = _spot(session, asset_id, closes_at)
    asset_row = _asset_row(session, asset_id, closes_at)
    liquidity, trades = _pools(session, asset_id, closes_at)

    return FactLaunchWindowSnapshot(
        asset_id=asset_id,
        window=window,
        # The observation instant, which is the window close rather than the moment
        # the job ran. Two runs of the same window must produce the same row.
        snapshot_ts=closes_at,
        listed_at=listed_at,
        closed_at=now,
        raw_volume=spot.raw,
        adjusted_volume=spot.adjusted,
        market_cap=asset_row,
        liquidity_usd=liquidity,
        trade_count=trades,
        venue_count=spot.venue_count,
        market_session=classify_session(closes_at),
        is_carried_forward=False,
    )


def _spot(session: Session, asset_id: str, at: datetime) -> _Spot:
    """Raw and adjusted turnover across venues, from the newest pair snapshot.

    Summed over observed rows only. A venue that failed to answer contributes
    nothing, not a zero, and if no venue answered at all both figures stay null —
    ``Not verified`` is not ``0``, least of all on a launch nobody has measured yet.
    """
    newest = session.execute(
        select(func.max(FactPairSnapshot.snapshot_ts))
        .where(FactPairSnapshot.asset_id == asset_id)
        .where(FactPairSnapshot.snapshot_ts <= at)
    ).scalar_one_or_none()
    if newest is None:
        return _Spot(raw=None, adjusted=None, venue_count=None)

    rows = session.execute(
        select(
            FactPairSnapshot.venue_id,
            FactPairSnapshot.raw_vol_24h,
            FactPairSnapshot.adjusted_vol_24h,
        )
        .where(FactPairSnapshot.asset_id == asset_id)
        .where(FactPairSnapshot.snapshot_ts == newest)
    ).all()

    raw = [v for _, v, _ in rows if v is not None]
    adjusted = [v for _, _, v in rows if v is not None]
    return _Spot(
        raw=sum(raw, Decimal(0)) if raw else None,
        adjusted=sum(adjusted, Decimal(0)) if adjusted else None,
        # Venues that reported turnover, not venues that have a row. A pair we could
        # not read is not evidence of a listing.
        venue_count=len({venue for venue, v, _ in rows if v is not None}) or None,
    )


def _asset_row(session: Session, asset_id: str, at: datetime) -> Decimal | None:
    """SPOT_MARKET_CAP at window close. A stock figure — never added to turnover."""
    stmt = (
        select(FactAssetSnapshot.market_cap)
        .where(FactAssetSnapshot.asset_id == asset_id)
        .where(FactAssetSnapshot.snapshot_ts <= at)
        .order_by(FactAssetSnapshot.snapshot_ts.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def _pools(
    session: Session, asset_id: str, at: datetime
) -> tuple[Decimal | None, int | None]:
    """DEX_LIQUIDITY and trade count at window close, across this asset's pools.

    A launch with turnover and no depth is a different event from one with both, so
    liquidity travels beside the volume rather than being folded into a single
    "activity" figure — they are two scopes and do not add.
    """
    pool_ids = [
        str(p)
        for p in session.execute(
            select(DimPool.pool_id).where(DimPool.base_asset_id == asset_id)
        ).scalars()
    ]
    if not pool_ids:
        return None, None

    newest = session.execute(
        select(func.max(FactPoolSnapshot.snapshot_ts))
        .where(FactPoolSnapshot.pool_id.in_(pool_ids))
        .where(FactPoolSnapshot.snapshot_ts <= at)
    ).scalar_one_or_none()
    if newest is None:
        return None, None

    rows = session.execute(
        select(FactPoolSnapshot.reserve_usd, FactPoolSnapshot.tx_count_24h)
        .where(FactPoolSnapshot.pool_id.in_(pool_ids))
        .where(FactPoolSnapshot.snapshot_ts == newest)
    ).all()

    reserves = [r for r, _ in rows if r is not None]
    counts = [c for _, c in rows if c is not None]
    return (
        sum(reserves, Decimal(0)) if reserves else None,
        sum(counts) if counts else None,
    )


__all__ = ["IN_SCOPE", "LOOKBACK", "WINDOWS", "record_windows"]
