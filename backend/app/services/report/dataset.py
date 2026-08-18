"""The snapshot a report is rendered from.

Loaded once and passed to every sheet builder, so all 22 sheets describe the same
moment. Building sheets straight off the ORM would let a collector land mid-render
and produce a workbook whose venue ranking disagrees with its own pair detail.

Each fact table is read at *its own* latest ``snapshot_ts``, not at one global one.
Collectors run on different cadences (15 minutes for headline, 6 hours for the long
tail), so a single timestamp would silently blank whichever table had not been
written at that instant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Iterable, Literal, Sequence, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.core.metrics import MetricScope, ScopedValue, safe_sum
from app.models.alerts import Alert, AlertEvidence
from app.models.dimensions import (
    DimAsset,
    DimBenchmark,
    DimIssuer,
    DimPerpContract,
    DimPool,
    DimTheme,
    DimUnderlying,
    DimVenue,
)
from app.models.enums import IN_SCOPE_TIERS
from app.models.facts import (
    FactAssetSnapshot,
    FactCategorySnapshot,
    FactPairSnapshot,
    FactPerpContractSnapshot,
    FactPerpVenueSnapshot,
    FactPoolSnapshot,
    FactUnderlyingReference,
)
from app.models.operations import FetchLog

T = TypeVar("T")

#: How much of an aggregate was observed. ``not_verified`` is not zero.
Coverage = Literal["complete", "partial", "not_verified"]


@dataclass(frozen=True, slots=True)
class AssetRow:
    """A tokenized asset with its dimension context and latest observation."""

    asset: DimAsset
    issuer: DimIssuer | None
    underlying: DimUnderlying | None
    snapshot: FactAssetSnapshot | None

    @property
    def in_scope(self) -> bool:
        """Whether this asset may enter a ranking, rollup or alert."""
        return self.asset.rwa_tier in IN_SCOPE_TIERS


@dataclass(frozen=True, slots=True)
class PairRow:
    snapshot: FactPairSnapshot
    asset: DimAsset
    venue: DimVenue | None

    @property
    def in_scope(self) -> bool:
        return self.asset.rwa_tier in IN_SCOPE_TIERS

    @property
    def venue_name(self) -> str:
        return self.venue.name if self.venue else self.snapshot.venue_id


@dataclass(frozen=True, slots=True)
class PoolRow:
    snapshot: FactPoolSnapshot
    pool: DimPool
    base_asset: DimAsset | None

    @property
    def in_scope(self) -> bool:
        """Whether this pool may enter a ranking, rollup or alert.

        A pool with no mapped base asset is not implicitly in scope. GeckoTerminal
        search returns whatever matched the query string, so an unmapped pool is an
        unidentified one, and counting it as tokenized-asset liquidity would inflate
        the DEX total with pairs nobody has confirmed are RWA.
        """
        return (
            self.base_asset is not None and self.base_asset.rwa_tier in IN_SCOPE_TIERS
        )


@dataclass(frozen=True, slots=True)
class PerpRow:
    snapshot: FactPerpContractSnapshot
    contract: DimPerpContract | None

    @property
    def in_scope(self) -> bool:
        """Whether this contract may enter a ranking, rollup or alert.

        Scope is carried by the mapped underlying, not by a tier column: a perpetual
        has no ``rwa_tier`` of its own, and the collectors deliberately ingest whole
        exchanges (Hyperliquid's BTC book arrives alongside its HIP-3 equity DEXs).
        Resolving to a ``dim_underlying`` row *is* the RWA test — crypto-native
        symbols never resolve, so they never reach a rollup.
        """
        return self.contract is not None and self.contract.underlying_id is not None

    @property
    def symbol(self) -> str:
        if self.contract:
            return self.contract.symbol
        # Fall back to the id's last segment: a contract observed before its
        # dimension row was written is still a real observation.
        return self.snapshot.contract_id.rsplit(":", 1)[-1]

    @property
    def exchange(self) -> str:
        return self.contract.exchange if self.contract else "unknown"

    @property
    def perp_dex(self) -> str:
        return (self.contract.perp_dex or "") if self.contract else ""


@dataclass(frozen=True, slots=True)
class ReportDataset:
    """Everything the 22 sheets read from."""

    as_of: datetime
    assets: tuple[AssetRow, ...]
    pairs: tuple[PairRow, ...]
    categories: tuple[FactCategorySnapshot, ...]
    underlyings: tuple[DimUnderlying, ...]
    themes: tuple[DimTheme, ...]
    benchmarks: tuple[DimBenchmark, ...]
    issuers: tuple[DimIssuer, ...]
    venues: tuple[DimVenue, ...]
    pools: tuple[PoolRow, ...]
    perp_venues: tuple[FactPerpVenueSnapshot, ...]
    #: TradFi prices for the underlyings, when a reference source is configured.
    #: Empty is the normal state without one, and an empty benchmark column is
    #: correct in that case — it is not a market in which nothing has a price.
    references: tuple[FactUnderlyingReference, ...]
    perp_contracts: tuple[PerpRow, ...]
    alerts: tuple[Alert, ...]
    evidence: tuple[AlertEvidence, ...]
    fetch_log: tuple[FetchLog, ...]

    @property
    def scoped_assets(self) -> tuple[AssetRow, ...]:
        """Assets eligible for rankings. ``NON_RWA`` is benchmark-only."""
        return tuple(a for a in self.assets if a.in_scope)

    @property
    def scoped_pairs(self) -> tuple[PairRow, ...]:
        return tuple(p for p in self.pairs if p.in_scope)

    @property
    def scoped_pools(self) -> tuple[PoolRow, ...]:
        """Pools whose base asset is an in-scope tokenized asset."""
        return tuple(p for p in self.pools if p.in_scope)

    @property
    def scoped_perp_contracts(self) -> tuple[PerpRow, ...]:
        """Contracts that resolve to a real-world underlying.

        The unscoped ``perp_contracts`` still exists for coverage reporting — knowing
        how much of an exchange we read is a data-quality fact — but no headline,
        ranking or alert reads it.
        """
        return tuple(p for p in self.perp_contracts if p.in_scope)


def latest_ts(
    session: Session,
    column: InstrumentedAttribute[datetime],
    as_of: datetime | None,
) -> datetime | None:
    """The newest snapshot at or before ``as_of``, or ``None`` if there is none."""
    stmt = select(func.max(column))
    if as_of is not None:
        stmt = stmt.where(column <= as_of)
    return session.execute(stmt).scalar()


def load(session: Session, as_of: datetime | None = None) -> ReportDataset:
    """Read one coherent snapshot of the warehouse."""
    asset_ts = latest_ts(session, FactAssetSnapshot.snapshot_ts, as_of)
    pair_ts = latest_ts(session, FactPairSnapshot.snapshot_ts, as_of)
    category_ts = latest_ts(session, FactCategorySnapshot.snapshot_ts, as_of)
    pool_ts = latest_ts(session, FactPoolSnapshot.snapshot_ts, as_of)
    perp_venue_ts = latest_ts(session, FactPerpVenueSnapshot.snapshot_ts, as_of)
    reference_ts = latest_ts(session, FactUnderlyingReference.snapshot_ts, as_of)
    perp_ts = latest_ts(session, FactPerpContractSnapshot.snapshot_ts, as_of)

    observed = [
        ts
        for ts in (
            asset_ts,
            pair_ts,
            category_ts,
            pool_ts,
            perp_venue_ts,
            perp_ts,
            reference_ts,
        )
        if ts is not None
    ]
    # An empty warehouse still produces a workbook — 22 sheets of headers. That is a
    # legible "nothing collected yet", where a crash on the first scheduled run is
    # not.
    resolved_as_of = as_of or (max(observed) if observed else _epoch())

    assets = _load_assets(session, asset_ts)
    pairs = _load_pairs(session, pair_ts)
    pools = _load_pools(session, pool_ts)
    perp_contracts = _load_perp_contracts(session, perp_ts)

    return ReportDataset(
        as_of=resolved_as_of,
        assets=assets,
        pairs=pairs,
        categories=_at(session, FactCategorySnapshot, category_ts),
        underlyings=_all(session, DimUnderlying, DimUnderlying.underlying_id),
        themes=_all(session, DimTheme, DimTheme.theme_id),
        benchmarks=_all(session, DimBenchmark, DimBenchmark.benchmark_id),
        issuers=_all(session, DimIssuer, DimIssuer.issuer_id),
        venues=_all(session, DimVenue, DimVenue.venue_id),
        pools=pools,
        perp_venues=_at(session, FactPerpVenueSnapshot, perp_venue_ts),
        references=_at(session, FactUnderlyingReference, reference_ts),
        perp_contracts=perp_contracts,
        alerts=_load_alerts(session),
        evidence=_load_evidence(session),
        fetch_log=_load_fetch_log(session, as_of),
    )


def _epoch() -> datetime:
    from datetime import timezone

    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _all(session: Session, model: type[T], order_by: Any) -> tuple[T, ...]:
    return tuple(session.execute(select(model).order_by(order_by)).scalars().all())


def _at(session: Session, model: type[T], ts: datetime | None) -> tuple[T, ...]:
    if ts is None:
        return ()
    stmt = select(model).where(model.snapshot_ts == ts)  # type: ignore[attr-defined]
    return tuple(session.execute(stmt).scalars().all())


def _load_assets(session: Session, ts: datetime | None) -> tuple[AssetRow, ...]:
    stmt = (
        select(DimAsset, DimIssuer, DimUnderlying, FactAssetSnapshot)
        .outerjoin(DimIssuer, DimAsset.issuer_id == DimIssuer.issuer_id)
        .outerjoin(DimUnderlying, DimAsset.underlying_id == DimUnderlying.underlying_id)
        .outerjoin(
            FactAssetSnapshot,
            (FactAssetSnapshot.asset_id == DimAsset.asset_id)
            & (FactAssetSnapshot.snapshot_ts == ts),
        )
        .order_by(DimAsset.asset_id)
    )
    return tuple(
        AssetRow(asset=asset, issuer=issuer, underlying=underlying, snapshot=snapshot)
        for asset, issuer, underlying, snapshot in session.execute(stmt).all()
    )


def _load_pairs(session: Session, ts: datetime | None) -> tuple[PairRow, ...]:
    if ts is None:
        return ()
    stmt = (
        select(FactPairSnapshot, DimAsset, DimVenue)
        .join(DimAsset, FactPairSnapshot.asset_id == DimAsset.asset_id)
        .outerjoin(DimVenue, FactPairSnapshot.venue_id == DimVenue.venue_id)
        .where(FactPairSnapshot.snapshot_ts == ts)
        .order_by(FactPairSnapshot.asset_id, FactPairSnapshot.venue_id)
    )
    return tuple(
        PairRow(snapshot=snapshot, asset=asset, venue=venue)
        for snapshot, asset, venue in session.execute(stmt).all()
    )


def _load_pools(session: Session, ts: datetime | None) -> tuple[PoolRow, ...]:
    if ts is None:
        return ()
    stmt = (
        select(FactPoolSnapshot, DimPool, DimAsset)
        .join(DimPool, FactPoolSnapshot.pool_id == DimPool.pool_id)
        .outerjoin(DimAsset, DimPool.base_asset_id == DimAsset.asset_id)
        .where(FactPoolSnapshot.snapshot_ts == ts)
        .order_by(DimPool.network, DimPool.dex, DimPool.pool_id)
    )
    return tuple(
        PoolRow(snapshot=snapshot, pool=pool, base_asset=asset)
        for snapshot, pool, asset in session.execute(stmt).all()
    )


def _load_perp_contracts(session: Session, ts: datetime | None) -> tuple[PerpRow, ...]:
    if ts is None:
        return ()
    stmt = (
        select(FactPerpContractSnapshot, DimPerpContract)
        .outerjoin(
            DimPerpContract,
            FactPerpContractSnapshot.contract_id == DimPerpContract.contract_id,
        )
        .where(FactPerpContractSnapshot.snapshot_ts == ts)
        .order_by(FactPerpContractSnapshot.contract_id)
    )
    return tuple(
        PerpRow(snapshot=snapshot, contract=contract)
        for snapshot, contract in session.execute(stmt).all()
    )


def _load_alerts(session: Session) -> tuple[Alert, ...]:
    """Open alerts, worst first. Resolved ones are history, not a to-do list."""
    # Both MySQL and SQLite sort NULLs last under DESC, which is what we want: an
    # unscored alert belongs below the scored ones, not above them.
    stmt = (
        select(Alert)
        .where(Alert.resolved_ts.is_(None))
        .order_by(Alert.score.desc(), Alert.last_seen_ts.desc())
    )
    return tuple(session.execute(stmt).scalars().all())


def _load_evidence(session: Session) -> tuple[AlertEvidence, ...]:
    stmt = (
        select(AlertEvidence)
        .join(Alert, AlertEvidence.alert_id == Alert.id)
        .where(Alert.resolved_ts.is_(None))
        .order_by(AlertEvidence.alert_id, AlertEvidence.snapshot_ts.desc())
    )
    return tuple(session.execute(stmt).scalars().all())


def _load_fetch_log(session: Session, as_of: datetime | None) -> tuple[FetchLog, ...]:
    stmt = select(FetchLog)
    if as_of is not None:
        stmt = stmt.where(FetchLog.snapshot_ts <= as_of)
    stmt = stmt.order_by(FetchLog.snapshot_ts.desc(), FetchLog.source_id).limit(2000)
    return tuple(session.execute(stmt).scalars().all())


# --- aggregation -----------------------------------------------------------


def scoped(amount: Decimal | None, scope: MetricScope) -> ScopedValue:
    """Wrap a nullable money column, preserving *missing* as unverified."""
    if amount is None:
        return ScopedValue(amount=None, scope=scope, verified=False)
    return ScopedValue(amount=amount, scope=scope, verified=True)


def group_sum(
    items: Iterable[T],
    key: Callable[[T], str | None],
    value: Callable[[T], Decimal | None],
    scope: MetricScope,
) -> dict[str, ScopedValue]:
    """Sum one metric per group, through ``safe_sum``.

    Not ``func.sum`` in SQL: SQL's SUM skips NULLs and returns a complete-looking
    number, which erases the difference between a venue with no turnover and a venue
    we failed to observe. ``safe_sum`` marks the partial case partial.
    """
    buckets: dict[str, list[ScopedValue]] = {}
    for item in items:
        group = key(item)
        if group is None:
            continue
        buckets.setdefault(group, []).append(scoped(value(item), scope))
    return {group: safe_sum(values) for group, values in buckets.items()}


def coverage(value: ScopedValue) -> Coverage:
    """How much of a total was actually observed.

    A partial total is still worth showing — it is a floor on the real figure — but
    it must not be labelled the same as a complete one. The same three tokens are
    used by the API and the workbook so the two cannot drift apart.
    """
    if value.amount is None:
        return "not_verified"
    return "complete" if value.verified else "partial"


def age_minutes(observed_ts: datetime | None, as_of: datetime) -> int | None:
    """How stale an observation is, in minutes, or ``None`` if it is undated.

    Lives here because the reference price is the one figure in this system whose
    age changes its meaning: the underlying market is shut for most of the hours we
    collect, so a basis quoted without the gap reads every weekend as a mispricing.
    The API and the workbook must therefore age it identically.

    Compared naive on purpose. SQLite and MySQL both store ``DATETIME`` without an
    offset, so one side of this subtraction comes back tz-aware and the other does
    not; normalising here beats raising in the middle of a request.
    """
    if observed_ts is None:
        return None
    delta = as_of.replace(tzinfo=None) - observed_ts.replace(tzinfo=None)
    return max(0, int(delta.total_seconds() // 60))


def amount_of(values: dict[str, ScopedValue], key: str) -> Decimal | None:
    entry = values.get(key)
    return entry.amount if entry else None


def sort_by_amount(keys: Sequence[str], values: dict[str, ScopedValue]) -> list[str]:
    """Rank keys by observed amount, with unobserved ones last.

    Unobserved entities sort to the bottom rather than to zero's position: they are
    unknown, and a reader scanning from the top should not meet them among the idle.
    """
    return sorted(
        keys,
        key=lambda k: (
            amount_of(values, k) is None,
            -(amount_of(values, k) or Decimal(0)),
            k,
        ),
    )


class UnderlyingAggregates:
    """Per-underlying totals, one dict per metric scope.

    Kept as separate dicts rather than one row object so that no code path can add
    a spot figure to a perpetual one by reaching for the wrong attribute of a shared
    record.

    Lives here rather than in a report module because the workbook and the API both
    answer "is anyone buying the S&P 500?" and must answer it identically.
    """

    __slots__ = ("market_cap", "spot_raw", "spot_adjusted", "perp_volume", "perp_oi")

    def __init__(self, data: ReportDataset) -> None:
        underlying_of = {
            a.asset.asset_id: a.asset.underlying_id for a in data.scoped_assets
        }
        self.market_cap = group_sum(
            data.scoped_assets,
            lambda a: a.asset.underlying_id,
            lambda a: a.snapshot.market_cap if a.snapshot else None,
            MetricScope.SPOT_MARKET_CAP,
        )
        self.spot_raw = group_sum(
            data.scoped_pairs,
            lambda p: underlying_of.get(p.snapshot.asset_id),
            lambda p: p.snapshot.raw_vol_24h,
            MetricScope.SPOT_VOLUME,
        )
        self.spot_adjusted = group_sum(
            data.scoped_pairs,
            lambda p: underlying_of.get(p.snapshot.asset_id),
            lambda p: p.snapshot.adjusted_vol_24h,
            MetricScope.SPOT_VOLUME,
        )
        self.perp_volume = group_sum(
            data.scoped_perp_contracts,
            lambda r: r.contract.underlying_id if r.contract else None,
            lambda r: r.snapshot.vol_24h,
            MetricScope.PERP_VOLUME,
        )
        self.perp_oi = group_sum(
            data.scoped_perp_contracts,
            lambda r: r.contract.underlying_id if r.contract else None,
            lambda r: r.snapshot.oi_usd,
            MetricScope.PERP_OI,
        )
