"""The source registry, seeded from ARCHITECTURE.md §2.

Two reasons this is a table rather than a constant:

1. ``fetch_log.source_id`` is a foreign key here, so a collector cannot record an
   outcome for a source nobody registered. That is deliberate — an unnamed source
   producing numbers is exactly the thing this system exists to prevent.
2. Sources we evaluated and *rejected* stay listed with ``REFERENCE_ONLY`` status.
   Otherwise the ASXN challenge-wall gets rediscovered in six months by someone who
   assumes nobody looked at it, and pays the probing cost again.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.metrics import MetricScope
from app.models.enums import AuthMode, SourceStatus
from app.models.operations import SourceRegistry


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """A source as designed, before it exists in the database."""

    source_id: str
    name: str
    base_url: str
    auth_mode: AuthMode
    status: SourceStatus
    cadence_minutes: int | None
    rate_limit_per_minute: int | None
    scopes: tuple[MetricScope, ...]
    notes: str


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        source_id="coingecko",
        name="CoinGecko",
        base_url=settings.coingecko_base_url,
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.ACTIVE,
        cadence_minutes=60,
        rate_limit_per_minute=30,
        scopes=(MetricScope.SPOT_MARKET_CAP, MetricScope.SPOT_VOLUME),
        notes="Free tier ~30 req/min. The five categories overlap by construction; "
        "only the deduplicated union row is a valid total.",
    ),
    SourceSpec(
        source_id="geckoterminal",
        name="GeckoTerminal",
        base_url=settings.geckoterminal_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=60,
        rate_limit_per_minute=12,
        scopes=(MetricScope.DEX_LIQUIDITY, MetricScope.SPOT_VOLUME),
        notes="The only source of buy/sell counts, and therefore the only direction-"
        "bearing data in the system. Pool coverage is incomplete. Paced at 12/min "
        "rather than the documented 30: at 30 a live pass got 11 responses and 29 "
        "429s.",
    ),
    SourceSpec(
        source_id="hyperliquid",
        name="Hyperliquid",
        base_url=settings.hyperliquid_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=60,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="Primary perpetuals source. HIP-3 deploys are permissionless, so an "
        "aggregator's Top 25 cannot see a new RWA perp market until it is large.",
    ),
    SourceSpec(
        source_id="binance",
        name="Binance",
        base_url=settings.binance_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=120,
        scopes=(MetricScope.SPOT_VOLUME, MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="Notional OI is not published for these contracts; it is derived as "
        "units x mark. The exchange's own EQUITY label is stored verbatim.",
    ),
    # --- Cross-venue perpetuals -------------------------------------------
    # Five keyless exchanges standing in for the Loris aggregation. Each publishes
    # turnover and open interest for its whole linear-perp book in one or two calls,
    # so the cost of covering all five is lower than one paid aggregator seat, and
    # unlike an aggregator's Top 25 the coverage is complete and dated.
    SourceSpec(
        source_id="okx",
        name="OKX",
        base_url=settings.okx_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=60,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="Publishes oiUsd directly, so open interest needs no multiplier. "
        "Turnover does: volCcy24h is in base currency and is multiplied by last.",
    ),
    SourceSpec(
        source_id="bybit",
        name="Bybit",
        base_url=settings.bybit_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=60,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="The only one of the five where turnover and USD open interest arrive "
        "in the same call, so its two figures always cover the same contract set.",
    ),
    SourceSpec(
        source_id="gate",
        name="Gate.io",
        base_url=settings.gate_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=60,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="Open interest is in contracts. quanto_multiplier comes from a second "
        "endpoint and varies by contract (AAPLX_USDT is 0.01), so it cannot be "
        "assumed to be 1.",
    ),
    SourceSpec(
        source_id="mexc",
        name="MEXC",
        base_url=settings.mexc_futures_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=60,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="Largest tokenized-equity perp book of the five: 283 of ~1,124 USDT "
        "contracts use the AAPLSTOCK naming. Open interest is holdVol x contractSize "
        "x fairPrice.",
    ),
    SourceSpec(
        source_id="bitget",
        name="Bitget",
        base_url=settings.bitget_base_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=15,
        rate_limit_per_minute=60,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="holdingAmount is in base coin and is converted at lastPr, so its open "
        "interest moves with price even when no position changed.",
    ),
    SourceSpec(
        source_id="alpaca",
        name="Alpaca",
        base_url=settings.alpaca_base_url,
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=60,
        rate_limit_per_minute=200,
        # Deliberately empty: a share price belongs to none of the five metric
        # families, is never summed, and exists for per-underlying comparison only.
        scopes=(),
        notes="US equity and ETF reference prices, one row per underlying — the "
        "benchmark the tokenized price is measured against. Free accounts get the "
        "IEX feed, a single venue at a few per cent of consolidated volume: the "
        "price is a fair sanity check, the share volume is not a market total, and "
        "the daily close is IEX's last print rather than the official auction. "
        "PLANNED because it needs a (free) key: the collector is written and the "
        "scheduler picks it up automatically once ALPACA_API_KEY_ID and "
        "ALPACA_API_SECRET_KEY are set.",
    ),
    SourceSpec(
        source_id="issuer_official",
        name="Issuer official sites",
        base_url=settings.ondo_products_url,
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.ACTIVE,
        cadence_minutes=360,
        rate_limit_per_minute=10,
        scopes=(),
        notes="Official product counts, the coverage denominator: Ondo states 443 "
        "and xStocks lists 716, against the ~113 CoinGecko indexes. Read from the "
        "server-rendered JSON payload, not a supported API, so a redesign returns "
        "200 and parses to nothing — that case is logged NOT_VERIFIED and writes no "
        "count, because a zero denominator would read as full coverage.",
    ),
    SourceSpec(
        source_id="loris",
        name="Loris Tools",
        base_url=settings.loris_api_base_url,
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(MetricScope.PERP_VOLUME, MetricScope.PERP_OI),
        notes="Cross-venue perp aggregation; the public view is Top 25 only and "
        "carries no contract history. The pages render client-side, so their HTML "
        "yields venue names and no figures. The real API is api.loris.tools "
        "(/rwa/exchanges, /rwa/aggregates-timeseries, /markets/symbols), which "
        "answers 401 Missing API key on every one. PLANNED rather than "
        "REFERENCE_ONLY: nothing about it was rejected, it only needs a key, and the "
        "collector is written and waiting for loris_api_key. Until then the five CEX "
        "perp sources above cover the same ground. See ADR 0006.",
    ),
    # --- Gap-filling sources, registered but never scheduled ---------------
    # Each names a hole in 14_Data_Quality that no free source closes, and what it
    # would cost to close it. They are listed so the next person to hit that hole
    # finds the evaluation instead of repeating it; none has a collector, and
    # PLANNED status keeps them out of every scheduled pass.
    SourceSpec(
        source_id="dune",
        name="Dune Analytics",
        base_url="https://api.dune.com/api/v1",
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(MetricScope.SPOT_VOLUME, MetricScope.DEX_LIQUIDITY),
        notes="Fills the on-chain holder and transfer gap: GeckoTerminal gives pool "
        "activity but never holder counts or issuance, so 'who owns this' is "
        "currently unanswerable. Free tier is 1,000 executions/month; Plus is "
        "$349/month. Needs a query author, not just a key.",
    ),
    SourceSpec(
        source_id="bitquery",
        name="Bitquery",
        base_url="https://streaming.bitquery.io/graphql",
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(MetricScope.SPOT_VOLUME,),
        notes="Fills the DEX long-tail gap: GeckoTerminal indexes the pools it knows "
        "about, so a new xStocks pool is invisible until it is listed. Bitquery reads "
        "the chain directly. ~$99/month for the developer tier.",
    ),
    SourceSpec(
        source_id="polygon_io",
        name="Polygon.io",
        base_url="https://api.polygon.io",
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(),
        notes="Fills the benchmark gap properly: Alpaca's free feed is IEX only, "
        "roughly 2-3% of consolidated US volume, so a tokenized/underlying premium "
        "computed from it inherits that error. Polygon sells SIP-consolidated data "
        "from $29/month. Only worth buying if the premium becomes a published metric.",
    ),
    SourceSpec(
        source_id="coingecko_pro",
        name="CoinGecko Pro",
        base_url="https://pro-api.coingecko.com/api/v3",
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(MetricScope.SPOT_MARKET_CAP, MetricScope.SPOT_VOLUME),
        notes="Not new data — the same endpoints without the 30 req/min ceiling that "
        "forces the long-tail pass onto a 6-hour cadence. Buy this when snapshot "
        "frequency is the binding constraint on detection latency. From $129/month.",
    ),
    SourceSpec(
        source_id="rwa_xyz",
        name="RWA.xyz",
        base_url="https://api.rwa.xyz",
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(MetricScope.SPOT_MARKET_CAP,),
        notes="Fills the off-chain-asset gap: private credit and treasuries that "
        "never appear on a CEX or DEX ticker and so are absent from every source "
        "here. Enterprise pricing, quote only. The closest thing to an industry "
        "reference total, which also makes it a way to check ours.",
    ),
    SourceSpec(
        source_id="asxn_hyperscreener",
        name="ASXN Hyperscreener",
        base_url="https://hyperscreener.asxn.xyz",
        auth_mode=AuthMode.CHALLENGE,
        status=SourceStatus.REFERENCE_ONLY,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(),
        notes="Every endpoint returns 403 VERIFICATION_REQUIRED. curl-cffi chrome124 "
        "/ chrome120 / safari17_0 fingerprints were all rejected. Never scheduled: we "
        "adopt its slippage-tier and depth-band definitions and compute them from "
        "Hyperliquid l2Book instead. See ADR 0004.",
    ),
)


def seed(session: Session) -> list[SourceRegistry]:
    """Insert missing sources. Existing rows are left exactly as they are.

    An operator who has paused a source by setting its status must not have that
    undone by the next boot.
    """
    known = set(session.execute(select(SourceRegistry.source_id)).scalars())
    created = []
    for spec in SOURCES:
        if spec.source_id in known:
            continue
        row = SourceRegistry(
            source_id=spec.source_id,
            name=spec.name,
            base_url=spec.base_url or None,
            auth_mode=spec.auth_mode,
            status=spec.status,
            cadence_minutes=spec.cadence_minutes,
            rate_limit_per_minute=spec.rate_limit_per_minute,
            provides_scopes="\n".join(s.value for s in spec.scopes) or None,
            notes=spec.notes,
        )
        session.add(row)
        created.append(row)
    return created
