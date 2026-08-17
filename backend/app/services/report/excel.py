"""The 22-sheet xlsx export.

Sheet order follows the source workbook (ARCHITECTURE.md §A.2) so a reader who knows
the manual version finds everything where it was, with three additions —
``16_HL_HIP3_Contracts``, ``17_Liquidity_Quality``, ``18_Theme_Demand`` — and an
``rwa_tier`` column wherever assets are listed.

Two rules shape every sheet here:

* No sheet sums across metric scopes. Where a sheet shows spot and perpetual figures
  together they sit in separate columns with no total, and ``22_Scope_Notes``
  restates why.
* A missing observation renders as ``Not verified``. Aggregates carry a coverage
  column so a partial total is never mistaken for a complete one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.core.metrics import MetricScope, ScopedValue, safe_sum
from app.services.analytics import concentration
from app.services.normalize.quality import Pair, screen
from app.services.report.dataset import (
    PairRow,
    ReportDataset,
    UnderlyingAggregates,
    amount_of,
    coverage,
    group_sum,
    load,
    scoped,
    sort_by_amount,
)
from app.services.report.workbook import SheetSpec, render

SPOT_CAP = MetricScope.SPOT_MARKET_CAP.value
SPOT_VOL = MetricScope.SPOT_VOLUME.value
DEX_LIQ = MetricScope.DEX_LIQUIDITY.value
PERP_VOL = MetricScope.PERP_VOLUME.value
PERP_OI = MetricScope.PERP_OI.value

#: Shorthand used by the grouping calls below, which read badly at full length.
SPOT = MetricScope.SPOT_VOLUME


def build_xlsx(session: Session, as_of: datetime | None = None) -> bytes:
    """Render the workbook for one snapshot."""
    return render(build_sheets(load(session, as_of)))


def build_sheets(data: ReportDataset) -> list[SheetSpec]:
    sheets = [
        _asset_master(data),
        _category_scale(data),
        _underlying_master(data),
        _underlying_demand(data),
        _spot_pairs(data),
        _venue_ranking(data),
        _venue_concentration(data),
        _issuer_ranking(data),
        _issuer_coverage(data),
        _dex_pools(data),
        _dex_liquidity(data),
        _perp_venues(data),
        _perp_contracts(data),
        _perp_vs_spot(data),
        _benchmark_compare(data),
        _hl_hip3_contracts(data),
        _liquidity_quality(data),
        _theme_demand(data),
        _alerts(data),
        _alert_evidence(data),
        _data_quality(data),
    ]
    sheets.append(_scope_notes(data, sheets))
    return sheets


# --- 01-05: scale and detail ----------------------------------------------


def _asset_master(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            row.asset.asset_id,
            row.asset.symbol,
            row.asset.name,
            row.asset.chain,
            row.asset.rwa_tier,
            row.issuer.name if row.issuer else None,
            row.asset.underlying_id,
            row.underlying.name if row.underlying else None,
            row.underlying.asset_class if row.underlying else None,
            row.snapshot.price_usd if row.snapshot else None,
            row.snapshot.market_cap if row.snapshot else None,
            row.snapshot.vol_24h if row.snapshot else None,
            row.snapshot.change_24h if row.snapshot else None,
            row.in_scope,
        ]
        for row in data.assets
    ]
    return SheetSpec(
        name="01_Asset_Master",
        headers=[
            "asset_id",
            "symbol",
            "name",
            "chain",
            "rwa_tier",
            "issuer",
            "underlying_id",
            "underlying_name",
            "asset_class",
            "price_usd",
            "market_cap",
            "vol_24h",
            "change_24h_pct",
            "in_scope",
        ],
        rows=rows,
        note=(
            "market_cap and vol_24h are different metric scopes and are never "
            "totalled together. in_scope=No marks NON_RWA rows, kept for benchmark "
            "reference only and excluded from every ranking in this workbook."
        ),
        scopes=[SPOT_CAP, SPOT_VOL],
    )


def _category_scale(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            snapshot.category_id,
            snapshot.asset_count,
            snapshot.market_cap,
            snapshot.vol_24h,
            snapshot.is_additive,
        ]
        for snapshot in sorted(data.categories, key=lambda c: c.category_id)
    ]
    return SheetSpec(
        name="02_Category_Scale",
        headers=[
            "category_id",
            "asset_count",
            "market_cap",
            "vol_24h",
            "is_additive",
        ],
        rows=rows,
        note=(
            "The five source categories overlap by construction: the same coin can "
            "sit in Tokenized Stock, xStocks and Ondo at once. Only the row with "
            "is_additive=Yes (the deduplicated union) is a valid total. Adding the "
            "others produces roughly 2.7x the real figure."
        ),
        scopes=[SPOT_CAP, SPOT_VOL],
    )


def _underlying_master(data: ReportDataset) -> SheetSpec:
    themes = {t.theme_id: t for t in data.themes}
    benchmarks = {b.benchmark_id: b for b in data.benchmarks}
    wrappers: dict[str, int] = {}
    for asset in data.assets:
        if asset.asset.underlying_id:
            wrappers[asset.asset.underlying_id] = (
                wrappers.get(asset.asset.underlying_id, 0) + 1
            )

    rows = [
        [
            u.underlying_id,
            u.name,
            u.asset_class,
            u.region,
            u.isin,
            u.is_pre_ipo,
            themes[u.theme_id].name_en if u.theme_id in themes else None,
            benchmarks[u.benchmark_id].name if u.benchmark_id in benchmarks else None,
            wrappers.get(u.underlying_id, 0),
        ]
        for u in data.underlyings
    ]
    return SheetSpec(
        name="03_Underlying_Master",
        headers=[
            "underlying_id",
            "name",
            "asset_class",
            "region",
            "isin",
            "is_pre_ipo",
            "theme",
            "benchmark",
            "wrapper_count",
        ],
        rows=rows,
        note=(
            "wrapper_count is how many tokenized wrappers reference this underlying. "
            "SPY alone appears as SPYB, SPYx and SPY-ON across three issuers; without "
            "this table those are three unrelated rows."
        ),
    )


def _underlying_demand(data: ReportDataset) -> SheetSpec:
    demand = _underlying_aggregates(data)
    names = {u.underlying_id: u.name for u in data.underlyings}
    classes = {u.underlying_id: u.asset_class for u in data.underlyings}
    keys = sort_by_amount(list(demand.spot_adjusted), demand.spot_adjusted)

    rows = [
        [
            key,
            names.get(key),
            classes.get(key),
            amount_of(demand.market_cap, key),
            amount_of(demand.spot_raw, key),
            amount_of(demand.spot_adjusted, key),
            coverage(demand.spot_adjusted[key]),
            amount_of(demand.perp_volume, key),
            amount_of(demand.perp_oi, key),
        ]
        for key in keys
    ]
    return SheetSpec(
        name="04_Underlying_Demand",
        headers=[
            "underlying_id",
            "name",
            "asset_class",
            "spot_market_cap",
            "spot_vol_raw",
            "spot_vol_adjusted",
            "spot_vol_coverage",
            "perp_vol_24h",
            "perp_oi_usd",
        ],
        rows=rows,
        note=(
            "Four metric scopes side by side, deliberately with no total column. "
            "spot_vol_raw and spot_vol_adjusted are both reported: the gap between "
            "them is a finding, not a rounding difference."
        ),
        scopes=[SPOT_CAP, SPOT_VOL, PERP_VOL, PERP_OI],
    )


def _spot_pairs(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            row.snapshot.asset_id,
            row.asset.symbol,
            row.asset.rwa_tier,
            row.venue_name,
            row.venue.venue_type if row.venue else None,
            row.venue.chain if row.venue else None,
            row.snapshot.raw_vol_24h,
            row.snapshot.adjusted_vol_24h,
            row.snapshot.price_usd,
            row.snapshot.spread_pct,
            row.snapshot.trust_score,
            row.snapshot.is_quality_anomaly,
            row.snapshot.is_quality_stale,
        ]
        for row in data.pairs
    ]
    return SheetSpec(
        name="05_Spot_Pairs",
        headers=[
            "asset_id",
            "symbol",
            "rwa_tier",
            "venue",
            "venue_type",
            "chain",
            "raw_vol_24h",
            "adjusted_vol_24h",
            "price_usd",
            "spread_pct",
            "trust_score",
            "is_quality_anomaly",
            "is_quality_stale",
        ],
        rows=rows,
        note=(
            "is_quality_anomaly and is_quality_stale are the data provider's "
            "assessment of the quote, not this system's demand alerts. A flagged "
            "pair keeps its raw volume and is dropped from the adjusted figure."
        ),
        scopes=[SPOT_VOL],
    )


# --- 06-09: venue and issuer competition ----------------------------------


def _venue_ranking(data: ReportDataset) -> SheetSpec:
    pairs = data.scoped_pairs
    raw = group_sum(
        pairs, lambda p: p.snapshot.venue_id, lambda p: p.snapshot.raw_vol_24h, SPOT
    )
    adjusted = group_sum(
        pairs,
        lambda p: p.snapshot.venue_id,
        lambda p: p.snapshot.adjusted_vol_24h,
        SPOT,
    )
    venues = {v.venue_id: v for v in data.venues}
    pair_counts: dict[str, int] = {}
    underlyings: dict[str, set[str]] = {}
    for pair in pairs:
        vid = pair.snapshot.venue_id
        pair_counts[vid] = pair_counts.get(vid, 0) + 1
        if pair.asset.underlying_id:
            underlyings.setdefault(vid, set()).add(pair.asset.underlying_id)

    total = sum(
        (v.amount for v in adjusted.values() if v.amount is not None), start=Decimal(0)
    )
    keys = sort_by_amount(list(adjusted), adjusted)
    rows = []
    for rank, key in enumerate(keys, start=1):
        value = amount_of(adjusted, key)
        rows.append(
            [
                rank,
                venues[key].name if key in venues else key,
                venues[key].venue_type if key in venues else None,
                venues[key].chain if key in venues else None,
                amount_of(raw, key),
                value,
                coverage(adjusted[key]),
                _ratio(value, total),
                pair_counts.get(key, 0),
                len(underlyings.get(key, set())),
            ]
        )
    return SheetSpec(
        name="06_Venue_Ranking",
        headers=[
            "rank",
            "venue",
            "venue_type",
            "chain",
            "raw_vol_24h",
            "adjusted_vol_24h",
            "coverage",
            "share_of_adjusted",
            "pair_count",
            "underlying_count",
        ],
        rows=rows,
        note=(
            "Ranked on adjusted turnover. Ranking on raw would put a venue whose "
            "quotes are almost entirely flagged near the top; see 17_Liquidity_"
            "Quality. NON_RWA assets are excluded from these totals."
        ),
        scopes=[SPOT_VOL],
    )


def _venue_concentration(data: ReportDataset) -> SheetSpec:
    by_type: dict[str, list[PairRow]] = {"ALL": list(data.scoped_pairs)}
    for pair in data.scoped_pairs:
        if pair.venue:
            by_type.setdefault(pair.venue.venue_type.value, []).append(pair)

    rows = []
    for segment, pairs in sorted(by_type.items()):
        adjusted = group_sum(
            pairs,
            lambda p: p.snapshot.venue_id,
            lambda p: p.snapshot.adjusted_vol_24h,
            SPOT,
        )
        if not adjusted:
            continue
        keys = list(adjusted)
        result = concentration.compute([adjusted[k] for k in keys], keys)
        rows.append(
            [
                segment,
                len(keys),
                result.total.amount,
                coverage(result.total),
                float(result.hhi),
                _pct(result.top_n_share(1).value),
                _pct(result.top_n_share(3).value),
                _pct(result.top_n_share(5).value),
                result.is_concentrated,
                result.unverified_count,
            ]
        )
    return SheetSpec(
        name="07_Venue_Concentration",
        headers=[
            "segment",
            "venue_count",
            "adjusted_vol_total",
            "coverage",
            "hhi",
            "top1_share",
            "top3_share",
            "top5_share",
            "is_concentrated",
            "unverified_venues",
        ],
        rows=rows,
        note=(
            "HHI is on the 0-10000 scale competition authorities use; above 2500 is "
            "concentrated. A ranking alone cannot distinguish a market whose leader "
            "holds 30% from one whose leader holds 85%."
        ),
        scopes=[SPOT_VOL],
    )


def _issuer_ranking(data: ReportDataset) -> SheetSpec:
    assets = data.scoped_assets
    issuer_of = {a.asset.asset_id: a.asset.issuer_id for a in assets}
    names = {i.issuer_id: i.name for i in data.issuers}

    market_cap = group_sum(
        assets,
        lambda a: a.asset.issuer_id,
        lambda a: a.snapshot.market_cap if a.snapshot else None,
        MetricScope.SPOT_MARKET_CAP,
    )
    adjusted = group_sum(
        data.scoped_pairs,
        lambda p: issuer_of.get(p.snapshot.asset_id),
        lambda p: p.snapshot.adjusted_vol_24h,
        SPOT,
    )
    counts: dict[str, int] = {}
    for asset in assets:
        if asset.asset.issuer_id:
            counts[asset.asset.issuer_id] = counts.get(asset.asset.issuer_id, 0) + 1

    keys = sort_by_amount(list(set(market_cap) | set(adjusted)), adjusted)
    rows = [
        [
            rank,
            names.get(key, key),
            counts.get(key, 0),
            amount_of(market_cap, key),
            amount_of(adjusted, key),
            coverage(adjusted[key]) if key in adjusted else "not_verified",
        ]
        for rank, key in enumerate(keys, start=1)
    ]
    return SheetSpec(
        name="08_Issuer_Ranking",
        headers=[
            "rank",
            "issuer",
            "indexed_asset_count",
            "spot_market_cap",
            "adjusted_vol_24h",
            "coverage",
        ],
        rows=rows,
        note="Market cap and turnover are separate scopes; there is no total column.",
        scopes=[SPOT_CAP, SPOT_VOL],
    )


def _issuer_coverage(data: ReportDataset) -> SheetSpec:
    counts: dict[str, int] = {}
    for asset in data.assets:
        if asset.asset.issuer_id:
            counts[asset.asset.issuer_id] = counts.get(asset.asset.issuer_id, 0) + 1

    rows = []
    for issuer in data.issuers:
        indexed = counts.get(issuer.issuer_id, 0)
        official = issuer.official_product_count
        rows.append(
            [
                issuer.name,
                official,
                indexed,
                _ratio(Decimal(indexed), Decimal(official)) if official else None,
                issuer.official_url,
                issuer.legal_structure_note,
            ]
        )
    return SheetSpec(
        name="09_Issuer_Coverage",
        headers=[
            "issuer",
            "official_product_count",
            "indexed_asset_count",
            "index_coverage",
            "official_url",
            "legal_structure_note",
        ],
        rows=rows,
        note=(
            "The issuer's own product count is the denominator, not the aggregator's. "
            "xStocks publishes about 640 products against roughly 113 indexed, so "
            "using the indexed count as the market size understates it about 5.7x."
        ),
    )


# --- 10-11: DEX liquidity --------------------------------------------------


def _dex_pools(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            row.pool.pool_id,
            row.pool.network,
            row.pool.dex,
            row.base_asset.symbol if row.base_asset else None,
            row.pool.quote_token,
            row.pool.is_canonical_quote,
            row.snapshot.reserve_usd,
            row.snapshot.vol_24h,
            row.snapshot.buys_24h,
            row.snapshot.sells_24h,
            _buy_ratio(row.snapshot.buys_24h, row.snapshot.sells_24h),
        ]
        for row in data.scoped_pools
    ]
    return SheetSpec(
        name="10_DEX_Pools",
        headers=[
            "pool_id",
            "network",
            "dex",
            "base_symbol",
            "quote_token",
            "is_canonical_quote",
            "reserve_usd",
            "vol_24h",
            "buys_24h",
            "sells_24h",
            "buy_ratio",
        ],
        rows=rows,
        note=(
            "buy_ratio is the only direction-bearing figure in the workbook. "
            "Turnover says somebody traded; this says whether they were buying."
        ),
        scopes=[DEX_LIQ, SPOT_VOL],
    )


def _dex_liquidity(data: ReportDataset) -> SheetSpec:
    def key(row: Any) -> str:
        return f"{row.pool.network} / {row.pool.dex}"

    pools = data.scoped_pools
    reserves = group_sum(
        pools, key, lambda r: r.snapshot.reserve_usd, MetricScope.DEX_LIQUIDITY
    )
    volumes = group_sum(pools, key, lambda r: r.snapshot.vol_24h, SPOT)
    counts: dict[str, int] = {}
    for row in pools:
        counts[key(row)] = counts.get(key(row), 0) + 1

    rows = [
        [
            group,
            counts.get(group, 0),
            amount_of(reserves, group),
            coverage(reserves[group]),
            amount_of(volumes, group),
            _ratio(amount_of(volumes, group), amount_of(reserves, group)),
        ]
        for group in sort_by_amount(list(reserves), reserves)
    ]
    return SheetSpec(
        name="11_DEX_Liquidity",
        headers=[
            "network_dex",
            "pool_count",
            "reserve_usd",
            "coverage",
            "vol_24h",
            "vol_over_reserve",
        ],
        rows=rows,
        note=(
            "reserve_usd is a stock and vol_24h is a flow. They share a currency and "
            "nothing else; vol_over_reserve is a turnover rate, not a sum."
        ),
        scopes=[DEX_LIQ, SPOT_VOL],
    )


# --- 12-16: perpetuals -----------------------------------------------------


def _perp_venues(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            snapshot.exchange,
            snapshot.perp_dex or "core",
            snapshot.segment,
            snapshot.vol_24h,
            snapshot.open_interest_usd,
            snapshot.symbol_count,
            snapshot.oi_symbol_count,
        ]
        for snapshot in sorted(
            data.perp_venues,
            key=lambda s: (s.exchange, s.perp_dex, s.segment),
        )
    ]
    return SheetSpec(
        name="12_Perp_Venues",
        headers=[
            "exchange",
            "perp_dex",
            "segment",
            "vol_24h",
            "open_interest_usd",
            "symbol_count",
            "oi_symbol_count",
        ],
        rows=rows,
        note=(
            "vol_24h is a flow and open_interest_usd is a stock. Charting them on one "
            "axis invites a comparison that does not exist. Where oi_symbol_count is "
            "below symbol_count, open_interest_usd covers only that many contracts and "
            "is a floor: the source charges one request per symbol for it."
        ),
        scopes=[PERP_VOL, PERP_OI],
    )


def _perp_contracts(data: ReportDataset) -> SheetSpec:
    contracts = data.scoped_perp_contracts
    volumes = group_sum(
        contracts,
        lambda r: r.snapshot.contract_id,
        lambda r: r.snapshot.vol_24h,
        MetricScope.PERP_VOLUME,
    )
    keys = sort_by_amount([r.snapshot.contract_id for r in contracts], volumes)
    by_id = {r.snapshot.contract_id: r for r in contracts}

    rows = []
    for rank, key in enumerate(keys, start=1):
        row = by_id[key]
        rows.append(
            [
                rank,
                row.exchange,
                row.perp_dex or "core",
                row.symbol,
                row.contract.source_underlying_type if row.contract else None,
                row.contract.analysis_group if row.contract else None,
                row.contract.underlying_id if row.contract else None,
                row.snapshot.vol_24h,
                row.snapshot.oi_units,
                row.snapshot.oi_usd,
                row.snapshot.funding_rate,
                row.snapshot.mark_price,
                row.snapshot.index_price,
            ]
        )
    return SheetSpec(
        name="13_Perp_Contracts",
        headers=[
            "rank",
            "exchange",
            "perp_dex",
            "symbol",
            "source_underlying_type",
            "analysis_group",
            "underlying_id",
            "vol_24h",
            "oi_units",
            "oi_usd",
            "funding_rate",
            "mark_price",
            "index_price",
        ],
        rows=rows,
        note=(
            "source_underlying_type is the exchange's own label, kept verbatim — "
            "Binance classifies some ETFs and leveraged ETPs as EQUITY. "
            "analysis_group is ours, stored alongside rather than instead of it. "
            "oi_usd is derived as oi_units x mark_price and both inputs are kept."
        ),
        scopes=[PERP_VOL, PERP_OI],
    )


def _perp_vs_spot(data: ReportDataset) -> SheetSpec:
    demand = _underlying_aggregates(data)
    names = {u.underlying_id: u.name for u in data.underlyings}
    keys = sort_by_amount(
        list(set(demand.spot_adjusted) | set(demand.perp_volume)), demand.perp_volume
    )

    rows = [
        [
            key,
            names.get(key),
            amount_of(demand.spot_adjusted, key),
            amount_of(demand.perp_volume, key),
            _ratio(
                amount_of(demand.perp_volume, key), amount_of(demand.spot_adjusted, key)
            ),
            amount_of(demand.market_cap, key),
            amount_of(demand.perp_oi, key),
        ]
        for key in keys
    ]
    return SheetSpec(
        name="14_Perp_vs_Spot",
        headers=[
            "underlying_id",
            "name",
            "spot_vol_adjusted",
            "perp_vol_24h",
            "perp_over_spot",
            "spot_market_cap",
            "perp_oi_usd",
        ],
        rows=rows,
        note=(
            "perp_over_spot is a comparison, not an aggregate: the two columns are "
            "different scopes and are never added. On the baseline dataset perpetual "
            "turnover ran about 1.7x spot, so looking at spot alone understates "
            "demand."
        ),
        scopes=[SPOT_VOL, PERP_VOL, SPOT_CAP, PERP_OI],
    )


def _benchmark_compare(data: ReportDataset) -> SheetSpec:
    demand = _underlying_aggregates(data)
    by_benchmark: dict[str, list[Any]] = {}
    for underlying in data.underlyings:
        if underlying.benchmark_id:
            by_benchmark.setdefault(underlying.benchmark_id, []).append(underlying)

    names = {b.benchmark_id: b.name for b in data.benchmarks}
    rows = []
    for benchmark_id, members in sorted(by_benchmark.items()):
        for member in members:
            rows.append(
                [
                    benchmark_id,
                    names.get(benchmark_id, benchmark_id),
                    member.underlying_id,
                    member.name,
                    member.asset_class,
                    amount_of(demand.spot_adjusted, member.underlying_id),
                    amount_of(demand.perp_volume, member.underlying_id),
                ]
            )
    return SheetSpec(
        name="15_Benchmark_Compare",
        headers=[
            "benchmark_id",
            "benchmark",
            "underlying_id",
            "underlying",
            "asset_class",
            "spot_vol_adjusted",
            "perp_vol_24h",
        ],
        rows=rows,
        note=(
            "A benchmark groups instruments with the same economic exposure — the SPY "
            "ETF and the S&P 500 index answer one question through two instruments. "
            "The grouping exists for side-by-side display and is never summed."
        ),
        scopes=[SPOT_VOL, PERP_VOL],
    )


def _hl_hip3_contracts(data: ReportDataset) -> SheetSpec:
    rows = []
    for row in data.perp_contracts:
        if not row.snapshot.contract_id.startswith("HL:"):
            continue
        perp_dex = row.perp_dex or row.snapshot.contract_id.split(":")[1]
        is_hip3 = perp_dex not in {"", "core"}
        rows.append(
            [
                perp_dex or "core",
                is_hip3,
                row.symbol,
                row.snapshot.vol_24h,
                row.snapshot.oi_usd,
                row.snapshot.oi_units,
                row.snapshot.funding_rate,
                row.snapshot.mark_price,
                row.snapshot.index_price,
                row.contract.underlying_id if row.contract else None,
                row.in_scope,
            ]
        )
    rows.sort(key=lambda r: (r[0], str(r[2])))
    return SheetSpec(
        name="16_HL_HIP3_Contracts",
        headers=[
            "perp_dex",
            "is_hip3",
            "symbol",
            "vol_24h",
            "oi_usd",
            "oi_units",
            "funding_rate",
            "mark_price",
            "index_price",
            "underlying_id",
            "in_scope",
        ],
        rows=rows,
        note=(
            "HIP-3 lets anyone deploy an independent perp DEX under one exchange. "
            "Aggregators list a Top 25 and cannot see a permissionless deployment at "
            "all, so this sheet is enumerated from the exchange directly. The "
            "enumeration is complete on purpose — a deployment we cannot yet classify "
            "is the thing worth seeing — so filter on in_scope before totalling: the "
            "other sheets already do, and the exchange's own BTC book is in here."
        ),
        scopes=[PERP_VOL, PERP_OI],
    )


# --- 17-18: quality and themes --------------------------------------------


def _liquidity_quality(data: ReportDataset) -> SheetSpec:
    by_venue: dict[str, list[PairRow]] = {}
    for pair in data.scoped_pairs:
        by_venue.setdefault(pair.snapshot.venue_id, []).append(pair)

    venues = {v.venue_id: v for v in data.venues}
    rows = []
    for venue_id, pairs in by_venue.items():
        result = screen(
            Pair(
                pair_id=f"{p.snapshot.asset_id}@{venue_id}",
                volume_usd=p.snapshot.raw_vol_24h,
                is_quality_anomaly=p.snapshot.is_quality_anomaly,
                is_quality_stale=p.snapshot.is_quality_stale,
            )
            for p in pairs
        )
        rows.append(
            [
                venues[venue_id].name if venue_id in venues else venue_id,
                venues[venue_id].venue_type if venue_id in venues else None,
                result.total_pairs,
                result.flagged_pairs,
                result.unverified_pairs,
                round(result.flagged_share, 4),
                result.raw.amount,
                result.adjusted.amount,
                coverage(result.adjusted),
                result.is_materially_divergent,
            ]
        )
    rows.sort(key=lambda r: (not r[9], -(float(r[6] or 0))))
    return SheetSpec(
        name="17_Liquidity_Quality",
        headers=[
            "venue",
            "venue_type",
            "pair_count",
            "flagged_pairs",
            "unverified_pairs",
            "flagged_share",
            "raw_vol_24h",
            "adjusted_vol_24h",
            "coverage",
            "materially_divergent",
        ],
        rows=rows,
        note=(
            "Sorted so materially divergent venues come first. Reference case: one "
            "venue reported about $29.3mn raw against about $216 adjusted, with 17 of "
            "19 pairs flagged. Publishing either figure alone misleads."
        ),
        scopes=[SPOT_VOL],
    )


def _theme_demand(data: ReportDataset) -> SheetSpec:
    demand = _underlying_aggregates(data)
    theme_of = {u.underlying_id: u.theme_id for u in data.underlyings}
    names = {t.theme_id: t for t in data.themes}

    spot: dict[str, list[Decimal | None]] = {}
    perp: dict[str, list[Decimal | None]] = {}
    members: dict[str, int] = {}
    for underlying_id, theme_id in theme_of.items():
        if not theme_id:
            continue
        members[theme_id] = members.get(theme_id, 0) + 1
        spot.setdefault(theme_id, []).append(
            amount_of(demand.spot_adjusted, underlying_id)
        )
        perp.setdefault(theme_id, []).append(
            amount_of(demand.perp_volume, underlying_id)
        )

    spot_totals = {
        theme: _total(values, MetricScope.SPOT_VOLUME) for theme, values in spot.items()
    }
    perp_totals = {
        theme: _total(values, MetricScope.PERP_VOLUME) for theme, values in perp.items()
    }
    rows = [
        [
            theme,
            names[theme].name_zh if theme in names else None,
            names[theme].name_en if theme in names else None,
            members.get(theme, 0),
            amount_of(spot_totals, theme),
            coverage(spot_totals[theme]),
            amount_of(perp_totals, theme),
        ]
        for theme in sort_by_amount(list(spot_totals), spot_totals)
    ]
    return SheetSpec(
        name="18_Theme_Demand",
        headers=[
            "theme_id",
            "name_zh",
            "name_en",
            "underlying_count",
            "spot_vol_adjusted",
            "coverage",
            "perp_vol_24h",
        ],
        rows=rows,
        note=(
            "Themes cut across issuers and venues, which is the level a product "
            "decision is actually made at. Demand on the baseline dataset clustered "
            "in what retail cannot otherwise buy — pre-IPO, memory semiconductors, "
            "commodities — rather than in blue chips."
        ),
        scopes=[SPOT_VOL, PERP_VOL],
    )


# --- 19-22: alerts, quality, notes ----------------------------------------


def _alerts(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            alert.id,
            alert.detector,
            alert.family,
            alert.severity,
            float(alert.score) if alert.score is not None else None,
            alert.status,
            alert.entity_type,
            alert.entity_id,
            alert.metric_scope,
            alert.market_session,
            alert.headline_zh,
            alert.headline_en,
            alert.first_seen_ts,
            alert.last_seen_ts,
            alert.occurrence_count,
        ]
        for alert in data.alerts
    ]
    return SheetSpec(
        name="19_Alerts",
        headers=[
            "alert_id",
            "detector",
            "family",
            "severity",
            "score",
            "status",
            "entity_type",
            "entity_id",
            "metric_scope",
            "market_session",
            "headline_zh",
            "headline_en",
            "first_seen_ts",
            "last_seen_ts",
            "occurrence_count",
        ],
        rows=rows,
        note=(
            "TENTATIVE means the condition fired on a single snapshot; CONFIRMED "
            "means it survived a second one. Every alert here clears a $50,000 "
            "absolute floor, because a $500 to $5,000 move is +900% and commercially "
            "meaningless."
        ),
    )


def _alert_evidence(data: ReportDataset) -> SheetSpec:
    rows = [
        [
            row.alert_id,
            row.rule_name,
            row.snapshot_ts,
            row.observed_value,
            row.baseline_median,
            row.baseline_mad,
            float(row.robust_z) if row.robust_z is not None else None,
            row.sample_size,
            row.market_session,
            row.peer_count,
            row.extra_json,
        ]
        for row in data.evidence
    ]
    return SheetSpec(
        name="20_Alert_Evidence",
        headers=[
            "alert_id",
            "rule_name",
            "snapshot_ts",
            "observed_value",
            "baseline_median",
            "baseline_mad",
            "robust_z",
            "sample_size",
            "market_session",
            "peer_count",
            "extra_json",
        ],
        rows=rows,
        note=(
            "Baselines are median and MAD, not mean and standard deviation: the top "
            "10 contracts carry 78.2% of one venue's turnover, and a mean is simply "
            "the spike. Baselines are also stratified by market_session — comparing a "
            "Monday open against a weekend average manufactures alarms."
        ),
    )


def _data_quality(data: ReportDataset) -> SheetSpec:
    buckets: dict[tuple[str, str], list[Any]] = {}
    for entry in data.fetch_log:
        buckets.setdefault((entry.source_id, entry.status.value), []).append(entry)

    rows = []
    for (source_id, status), entries in sorted(buckets.items()):
        durations = [e.duration_ms for e in entries if e.duration_ms is not None]
        records = [e.record_count for e in entries if e.record_count is not None]
        rows.append(
            [
                source_id,
                status,
                len(entries),
                max(e.snapshot_ts for e in entries),
                sum(records) if records else None,
                round(sum(durations) / len(durations)) if durations else None,
                next(
                    (e.error_message for e in entries if e.error_message),
                    None,
                ),
            ]
        )
    return SheetSpec(
        name="21_Data_Quality",
        headers=[
            "source_id",
            "status",
            "attempts",
            "last_attempt_ts",
            "records",
            "avg_duration_ms",
            "sample_error",
        ],
        rows=rows,
        note=(
            "A not_verified row means a fetch failed, which is a missing observation "
            "and not a zero. Anything it feeds shows as Not verified elsewhere in "
            "this workbook rather than as 0."
        ),
    )


def _scope_notes(data: ReportDataset, sheets: Sequence[SheetSpec]) -> SheetSpec:
    rows: list[list[Any]] = [
        ["_as_of", data.as_of, "The snapshot this workbook describes."],
        [
            "_scopes",
            ", ".join(s.value for s in MetricScope),
            "Five metric families. Figures in different families are never added, "
            "in this workbook or anywhere else.",
        ],
    ]
    rows.extend(
        [sheet.name, ", ".join(sheet.scopes), sheet.note]
        for sheet in sheets
        if sheet.note or sheet.scopes
    )
    return SheetSpec(
        name="22_Scope_Notes",
        headers=["sheet", "metric_scopes", "note"],
        rows=rows,
        note="",
    )


# --- shared computation ----------------------------------------------------


def _underlying_aggregates(data: ReportDataset) -> UnderlyingAggregates:
    return UnderlyingAggregates(data)


def _total(values: Sequence[Decimal | None], scope: MetricScope) -> ScopedValue:
    if not values:
        return scoped(None, scope)
    return safe_sum([scoped(v, scope) for v in values])


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> float | None:
    """A ratio, or ``None`` when either side is missing.

    Returning ``None`` rather than 0 matters: an unobserved numerator over a known
    denominator is an unknown share, not a zero share.
    """
    if numerator is None or not denominator:
        return None
    return float(numerator / denominator)


def _pct(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _buy_ratio(buys: int | None, sells: int | None) -> float | None:
    if buys is None or sells is None:
        return None
    total = buys + sells
    if total == 0:
        return None
    return buys / total
