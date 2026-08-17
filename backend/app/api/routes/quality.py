"""What the numbers on every other page are worth.

This page exists because ``Not verified`` and ``0`` are different answers, and the
difference is invisible once a figure reaches a chart. Anything reported here as a
failed fetch is rendered elsewhere as a grey placeholder, never as a zero bar.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DatasetDep, SessionDep
from app.models.enums import MappingStatus
from app.models.operations import UnderlyingMap
from app.schemas.common import Meta
from app.schemas.market import DataQuality, SourceHealth
from app.services.normalize.quality import Pair, screen
from app.services.report.dataset import ReportDataset

router = APIRouter(tags=["quality"])


@router.get("/data-quality", response_model=DataQuality)
def data_quality(data: DatasetDep, session: SessionDep) -> DataQuality:
    buckets: dict[tuple[str, str], list[Any]] = {}
    for entry in data.fetch_log:
        buckets.setdefault((entry.source_id, entry.status.value), []).append(entry)

    sources = []
    for (source_id, status), entries in sorted(buckets.items()):
        durations = [e.duration_ms for e in entries if e.duration_ms is not None]
        records = [e.record_count for e in entries if e.record_count is not None]
        sources.append(
            SourceHealth(
                source_id=source_id,
                status=status,
                attempts=len(entries),
                last_attempt_ts=max(e.snapshot_ts for e in entries),
                # Null, not 0: "never got far enough to count" is not "counted none".
                records=sum(records) if records else None,
                avg_duration_ms=(
                    round(sum(durations) / len(durations)) if durations else None
                ),
                sample_error=next(
                    (e.error_message for e in entries if e.error_message), None
                ),
            )
        )

    pairs = data.scoped_pairs
    flagged = sum(
        1 for p in pairs if p.snapshot.is_quality_anomaly or p.snapshot.is_quality_stale
    )
    unverified = sum(1 for p in pairs if p.snapshot.raw_vol_24h is None)

    pending = session.execute(
        select(func.count())
        .select_from(UnderlyingMap)
        .where(UnderlyingMap.status == MappingStatus.PENDING_REVIEW)
    ).scalar_one()

    return DataQuality(
        meta=Meta(
            as_of=data.as_of,
            note=(
                "A not_verified source means a fetch failed. Everything it feeds shows "
                "as Not verified elsewhere rather than as 0, and pending_mappings "
                "counts symbols nobody has confirmed — GOLD, GOLDJM and GLDMINE are "
                "three different underlyings, so an unconfirmed guess is worse than a "
                "gap."
            ),
            row_count=len(sources),
        ),
        sources=sources,
        pair_count=len(pairs),
        flagged_pairs=flagged,
        unverified_pairs=unverified,
        pending_mappings=int(pending),
        divergent_venues=_divergent(data),
    )


def _divergent(data: ReportDataset) -> list[str]:
    """Venues whose adjusted turnover falls an order of magnitude below raw.

    Reference case: one venue reported about $29.3mn raw against about $216 adjusted,
    17 of its 19 pairs flagged. Quoting either figure alone misleads, so the venue is
    named here and both numbers are shown on the venue page.
    """
    by_venue: dict[str, list[Pair]] = {}
    for pair in data.scoped_pairs:
        by_venue.setdefault(pair.snapshot.venue_id, []).append(
            Pair(
                pair_id=f"{pair.snapshot.asset_id}@{pair.snapshot.venue_id}",
                volume_usd=pair.snapshot.raw_vol_24h,
                is_quality_anomaly=pair.snapshot.is_quality_anomaly,
                is_quality_stale=pair.snapshot.is_quality_stale,
            )
        )

    names = {v.venue_id: v.name for v in data.venues}
    return sorted(
        names.get(venue_id, venue_id)
        for venue_id, pairs in by_venue.items()
        if screen(pairs).is_materially_divergent
    )
