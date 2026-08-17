"""APScheduler job registration and the jobs themselves.

Cadences follow ARCHITECTURE.md §12, in Hong Kong time:

===============  ==============================================================
every 15 min     headline snapshot (Binance TradFi ticker, Hyperliquid ctxs)
every 1 hour     spot Top 50 + GeckoTerminal pools + Hyperliquid perp DEXs
every 6 hours    long-tail spot, category totals, issuer product counts
daily 03:00      baseline recompute
daily 08:00      generate xlsx + docx
===============  ==============================================================

Three properties hold for every job here:

* **One ``snapshot_ts`` per pass.** Every fact row a job writes carries the same
  instant, so a chart never interleaves two half-collections. Stamping per collector
  would make "the 09:15 snapshot" mean different things on different tables.
* **A failing collector does not abort the pass.** Its outcome is logged as
  ``NOT_VERIFIED`` and the others still run. One source going down must not cost the
  snapshot the sources that are up.
* **Nothing is written to local disk.** Reports go to the database or object storage;
  production K8s provides no PersistentVolumeClaim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.config import settings
from app.core.metrics import MetricScope
from app.core.sessions import MarketSession
from app.db.session import SessionLocal
from app.models.enums import EntityType, FetchStatus
from app.models.facts import FactAssetSnapshot, FactPerpContractSnapshot
from app.models.operations import BaselineSnapshot
from app.services.analytics.baseline import compute_baseline
from app.services.anomaly.engine import build_default_engine
from app.services.ingest import registry
from app.services.ingest.base import Collector, FetchResult, record_fetches
from app.services.ingest.binance import BinanceCollector
from app.services.ingest.coingecko import CoinGeckoCollector
from app.services.ingest.geckoterminal import GeckoTerminalCollector
from app.services.ingest.hyperliquid import HyperliquidCollector
from app.services.normalize import seed as reference_seed
from app.services.report import service as report_service

logger = logging.getLogger(__name__)

#: How far back the baseline job looks. Thirty days of a 15-minute cadence is well
#: over the 14 same-session observations a baseline needs, with room for the days a
#: source was down.
BASELINE_WINDOW_DAYS = 30

_scheduler: BackgroundScheduler | None = None


@dataclass(frozen=True, slots=True)
class _BaselineSeries:
    """One series the baseline job rebuilds, as columns rather than a model.

    The metric scope is carried explicitly. Nothing about a column named ``vol_24h``
    says whether it is a flow or a stock, and a baseline stored under the wrong scope
    would later be compared against a number of a different kind.
    """

    entity_type: EntityType
    scope: MetricScope
    key: InstrumentedAttribute[str]
    value: InstrumentedAttribute[Decimal | None]
    market_session: InstrumentedAttribute[MarketSession]
    snapshot_ts: InstrumentedAttribute[datetime]
    carried_forward: InstrumentedAttribute[bool]


BASELINE_SERIES: tuple[_BaselineSeries, ...] = (
    _BaselineSeries(
        entity_type=EntityType.ASSET,
        scope=MetricScope.SPOT_VOLUME,
        key=FactAssetSnapshot.asset_id,
        value=FactAssetSnapshot.vol_24h,
        market_session=FactAssetSnapshot.market_session,
        snapshot_ts=FactAssetSnapshot.snapshot_ts,
        carried_forward=FactAssetSnapshot.is_carried_forward,
    ),
    _BaselineSeries(
        entity_type=EntityType.ASSET,
        scope=MetricScope.SPOT_MARKET_CAP,
        key=FactAssetSnapshot.asset_id,
        value=FactAssetSnapshot.market_cap,
        market_session=FactAssetSnapshot.market_session,
        snapshot_ts=FactAssetSnapshot.snapshot_ts,
        carried_forward=FactAssetSnapshot.is_carried_forward,
    ),
    _BaselineSeries(
        entity_type=EntityType.PERP_CONTRACT,
        scope=MetricScope.PERP_VOLUME,
        key=FactPerpContractSnapshot.contract_id,
        value=FactPerpContractSnapshot.vol_24h,
        market_session=FactPerpContractSnapshot.market_session,
        snapshot_ts=FactPerpContractSnapshot.snapshot_ts,
        carried_forward=FactPerpContractSnapshot.is_carried_forward,
    ),
    _BaselineSeries(
        entity_type=EntityType.PERP_CONTRACT,
        scope=MetricScope.PERP_OI,
        key=FactPerpContractSnapshot.contract_id,
        value=FactPerpContractSnapshot.oi_usd,
        market_session=FactPerpContractSnapshot.market_session,
        snapshot_ts=FactPerpContractSnapshot.snapshot_ts,
        carried_forward=FactPerpContractSnapshot.is_carried_forward,
    ),
)


@dataclass(frozen=True, slots=True)
class PassResult:
    """What one collection pass did, for the log line and for tests."""

    snapshot_ts: datetime
    fetches: int
    failures: int
    alerts_created: int = 0
    alerts_suppressed: int = 0


def now_utc() -> datetime:
    """The snapshot instant, truncated to the minute.

    Truncated so that the several fact tables a pass writes share an exactly equal
    ``snapshot_ts``. They are joined on equality, and microsecond drift between two
    collectors in the same pass would silently split one snapshot into two.
    """
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


# --- jobs -----------------------------------------------------------------


def bootstrap() -> None:
    """Seed the reference rows every other job depends on.

    ``fetch_log.source_id`` is a foreign key into ``source_registry``, so a collector
    cannot even record a failure before this runs. Underlyings matter for a subtler
    reason: ``underlying_map`` only accepts a stripped symbol whose underlying already
    exists, so an unseeded database maps nothing and the demand view stays empty.
    """
    with SessionLocal() as session:
        registry.seed(session)
        added = reference_seed.seed(session)
        session.commit()
    if added:
        logger.info("seeded %d reference underlyings", added)


def headline_snapshot() -> PassResult:
    """Every 15 minutes: the two fastest-moving perpetual sources, then detection."""
    return run_pass(
        [HyperliquidCollector(), BinanceCollector()], detect=True, label="headline"
    )


def hourly_snapshot() -> PassResult:
    """Hourly: spot Top 50, DEX pools, and the perp DEX roster."""
    return run_pass(
        [
            CoinGeckoCollector(ticker_depth=50),
            GeckoTerminalCollector(symbol_depth=40),
            HyperliquidCollector(),
        ],
        detect=True,
        label="hourly",
    )


def long_tail_snapshot() -> PassResult:
    """Every six hours: the tail CoinGecko's Top 50 pass never reaches.

    Detection is off here. This pass exists to complete coverage, and its deeper
    ticker sweep takes long enough that its rows are not comparable, snapshot for
    snapshot, with the 15-minute cadence the detectors are calibrated on.
    """
    return run_pass(
        [
            CoinGeckoCollector(ticker_depth=150),
            GeckoTerminalCollector(symbol_depth=120),
        ],
        detect=False,
        label="long_tail",
    )


def daily_report() -> None:
    """Build both formats from one warehouse read and persist them."""
    with SessionLocal() as session:
        artifacts = report_service.generate(session)
        session.commit()
        logger.info(
            "generated %s", ", ".join(a.filename for a in artifacts) or "nothing"
        )


def recompute_baselines() -> int:
    """Rebuild the stored baselines. Returns how many rows were written.

    Baselines are persisted rather than computed on read so an alert fired last
    Tuesday can be re-examined against the baseline that actually produced it, not
    against whatever the window says today.
    """
    with SessionLocal() as session:
        written = _write_baselines(session, now_utc())
        session.commit()
    logger.info("wrote %d baseline rows", written)
    return written


def run_pass(
    collectors: Sequence[Collector], *, detect: bool, label: str
) -> PassResult:
    """Run collectors against one snapshot instant, then optionally detect."""
    snapshot_ts = now_utc()
    failures = 0
    fetches = 0

    with SessionLocal() as session:
        for collector in collectors:
            try:
                results = collector.collect(session, snapshot_ts)
                # Inside the try with the collect itself: a constraint violation on
                # flush is as much this collector's failure as a timeout is, and
                # letting it escape here would cost the pass every collector after it.
                record_fetches(session, snapshot_ts, results)
                session.commit()
            except Exception as error:  # noqa: BLE001 - isolation is the point
                # A crashing collector costs its own rows and nothing else. The
                # rollback is scoped to this collector's uncommitted work.
                logger.exception("collector %s failed", collector.source_id)
                session.rollback()
                failures += 1
                _record_collector_failure(session, snapshot_ts, collector, error)
                continue

            fetches += len(results)
            failures += sum(1 for r in results if not r.ok)

        result = PassResult(snapshot_ts=snapshot_ts, fetches=fetches, failures=failures)
        if detect:
            result = _detect(session, snapshot_ts, result)

    logger.info(
        "%s pass at %s: %d fetches, %d failures, %d alerts",
        label,
        snapshot_ts.isoformat(),
        result.fetches,
        result.failures,
        result.alerts_created,
    )
    return result


def _record_collector_failure(
    session: Session,
    snapshot_ts: datetime,
    collector: Collector,
    error: BaseException,
) -> None:
    """Write the ``NOT_VERIFIED`` row the crashed collector never reached.

    A collector that raises before returning its results leaves no trace in
    ``fetch_log``, which makes a source that broke indistinguishable from a source
    that was never scheduled — and those need opposite responses. The exception is
    only in the application log, and the data-quality page does not read that.

    Failing to record the failure must not itself abort the pass, so this swallows its
    own errors after logging them.
    """
    try:
        record_fetches(
            session,
            snapshot_ts,
            [
                FetchResult(
                    source_id=collector.source_id,
                    endpoint="collect",
                    status=FetchStatus.NOT_VERIFIED,
                    error=f"{type(error).__name__}: {error}"[:2000],
                )
            ],
        )
        session.commit()
    except Exception:  # noqa: BLE001 - the pass matters more than the bookkeeping
        logger.exception(
            "could not record the failure of collector %s", collector.source_id
        )
        session.rollback()


def _detect(session: Session, snapshot_ts: datetime, result: PassResult) -> PassResult:
    """Run the cross-sectional detectors over what the pass just wrote."""
    try:
        engine = build_default_engine(session, snapshot_ts)
        outcome = engine.run(session, snapshot_ts)
        session.commit()
    except Exception:  # noqa: BLE001 - collection already succeeded; keep it
        logger.exception("detection failed at %s", snapshot_ts.isoformat())
        session.rollback()
        return result

    return PassResult(
        snapshot_ts=result.snapshot_ts,
        fetches=result.fetches,
        failures=result.failures,
        alerts_created=outcome.alert_count,
        alerts_suppressed=len(outcome.suppressed),
    )


def _write_baselines(session: Session, snapshot_ts: datetime) -> int:
    """Compute median/MAD per (entity, metric, market_session) over the window.

    Carried-forward rows are excluded. Repeating yesterday's value and then measuring
    the variance of the result manufactures a stability the market never had, and
    every alert would then be scored against it.
    """
    cutoff = snapshot_ts - timedelta(days=BASELINE_WINDOW_DAYS)
    written = 0

    for series in BASELINE_SERIES:
        grouped: dict[tuple[str, MarketSession], list[float]] = {}
        for entity_id, market_session, value in _observations(session, series, cutoff):
            grouped.setdefault((entity_id, market_session), []).append(value)

        for (entity_id, market_session), values in grouped.items():
            baseline = compute_baseline(values, market_session)
            if baseline is None:
                continue
            session.merge(
                BaselineSnapshot(
                    entity_type=series.entity_type,
                    entity_id=entity_id,
                    metric_scope=series.scope,
                    market_session=market_session,
                    snapshot_ts=snapshot_ts,
                    median=Decimal(str(round(baseline.median, 8))),
                    mad=Decimal(str(round(baseline.mad, 8))),
                    sample_size=baseline.sample_size,
                    window_days=BASELINE_WINDOW_DAYS,
                    is_alertable=baseline.is_alertable,
                )
            )
            written += 1
    return written


def _observations(
    session: Session, series: _BaselineSeries, cutoff: datetime
) -> list[tuple[str, MarketSession, float]]:
    """Observed values in the window, as (entity_id, session, value) triples.

    Nulls are dropped rather than read as zero: a failed fetch is a missing
    observation, and folding it in as a zero drags every median it touches downward.
    """
    stmt = (
        select(series.key, series.market_session, series.value)
        .where(series.snapshot_ts >= cutoff)
        .where(series.carried_forward.is_(False))
        .where(series.value.is_not(None))
    )
    return [
        (str(entity_id), market_session, float(value))
        for entity_id, market_session, value in session.execute(stmt).all()
    ]


# --- lifecycle -------------------------------------------------------------


def build_scheduler() -> BackgroundScheduler:
    """Assemble the scheduler without starting it, so tests can inspect the jobs."""
    scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)

    scheduler.add_job(
        headline_snapshot,
        IntervalTrigger(minutes=15),
        id="headline_snapshot",
        # A slow pass must not stack up behind itself: two concurrent passes would
        # write two snapshot instants for what is one observation of the market.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        hourly_snapshot,
        CronTrigger(minute=5),
        id="hourly_snapshot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        long_tail_snapshot,
        CronTrigger(hour="0,6,12,18", minute=20),
        id="long_tail_snapshot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        recompute_baselines,
        CronTrigger(hour=3, minute=0),
        id="recompute_baselines",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        daily_report,
        CronTrigger.from_crontab(settings.daily_report_cron),
        id="daily_report",
        max_instances=1,
        coalesce=True,
    )
    return scheduler


def start() -> BackgroundScheduler | None:
    """Start collection if it is enabled. Returns the scheduler, or None if off.

    Scheduling is opt-in. Baselines need 14 same-session snapshots, so every day the
    scheduler is off is a day the time-series detectors stay silent — but a developer
    machine hammering live rate limits is the worse failure, and a throttled source
    costs the production instance too.
    """
    global _scheduler
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled; no collection jobs registered")
        return None
    if _scheduler is not None:
        return _scheduler

    bootstrap()
    _scheduler = build_scheduler()
    _scheduler.start()
    logger.info(
        "scheduler started (%s): %s",
        settings.scheduler_timezone,
        ", ".join(job.id for job in _scheduler.get_jobs()),
    )
    return _scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
