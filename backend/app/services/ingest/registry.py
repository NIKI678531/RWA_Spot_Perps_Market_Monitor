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
        rate_limit_per_minute=30,
        scopes=(MetricScope.DEX_LIQUIDITY, MetricScope.SPOT_VOLUME),
        notes="The only source of buy/sell counts, and therefore the only direction-"
        "bearing data in the system. Pool coverage is incomplete.",
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
    SourceSpec(
        source_id="alpaca",
        name="Alpaca",
        base_url=settings.alpaca_base_url,
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=1440,
        rate_limit_per_minute=200,
        scopes=(),
        notes="US equity reference prices. IEX rather than SIP, so it is a sanity "
        "check on tokenized prices, not a benchmark of record.",
    ),
    SourceSpec(
        source_id="issuer_official",
        name="Issuer official sites",
        base_url="",
        auth_mode=AuthMode.PUBLIC,
        status=SourceStatus.PLANNED,
        cadence_minutes=360,
        rate_limit_per_minute=None,
        scopes=(),
        notes="Official product counts. Larger than any aggregator's index (xStocks "
        "lists ~640 against CoinGecko's ~113), so it is the coverage denominator.",
    ),
    SourceSpec(
        source_id="loris",
        name="Loris Tools",
        base_url=settings.loris_base_url,
        auth_mode=AuthMode.API_KEY,
        status=SourceStatus.PLANNED,
        cadence_minutes=None,
        rate_limit_per_minute=None,
        scopes=(MetricScope.PERP_VOLUME,),
        notes="Cross-venue perp aggregation. The public view is Top 25 only and "
        "carries no contract history.",
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
