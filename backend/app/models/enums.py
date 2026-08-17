"""Domain enumerations shared by the ORM, the services and the API schemas."""

from __future__ import annotations

from enum import StrEnum


class RwaTier(StrEnum):
    """How close an asset sits to a real backed claim.

    This is the gate on every statistic in the system: ``NON_RWA`` rows exist only
    as benchmark reference and never enter a ranking, rollup or alert.
    """

    CORE_RWA = "core_rwa"  # custodied or receipt-backed tokenized security
    RWA_ADJACENT = "rwa_adjacent"  # related, but not itself tokenized exposure
    SYNTHETIC = "synthetic"  # exposure without custody (perps, synths)
    NON_RWA = "non_rwa"  # crypto-native; out of scope


#: Tiers that may appear in rankings, rollups and alerts.
IN_SCOPE_TIERS = frozenset({RwaTier.CORE_RWA, RwaTier.RWA_ADJACENT, RwaTier.SYNTHETIC})


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FUND = "fund"
    COMMODITY = "commodity"
    FX = "fx"
    INDEX = "index"
    PRE_IPO = "pre_ipo"


class VenueType(StrEnum):
    CEX = "cex"
    DEX = "dex"
    PERP_DEX = "perp_dex"


class AuthMode(StrEnum):
    """How a source must be reached."""

    PUBLIC = "public"
    API_KEY = "api_key"
    CHALLENGE = "challenge"  # human-verification gated, e.g. Cloudflare Turnstile


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    #: Evaluated and deliberately not collected from. Retained so the evaluation is
    #: not repeated; never scheduled.
    REFERENCE_ONLY = "reference_only"
    DISABLED = "disabled"


class FetchStatus(StrEnum):
    """Outcome of one collection attempt.

    ``NOT_VERIFIED`` is the important one: it means we failed to observe, which is
    categorically different from observing a zero.
    """

    OK = "ok"
    PARTIAL = "partial"
    NOT_VERIFIED = "not_verified"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class DetectorFamily(StrEnum):
    CROSS_SECTIONAL = "cross_sectional"  # compares against peers, needs no history
    TIME_SERIES = "time_series"  # compares against own past, needs a baseline


class AlertSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    TENTATIVE = "tentative"  # fired on a single snapshot
    CONFIRMED = "confirmed"  # persisted across two consecutive snapshots
    RESOLVED = "resolved"


class EntityType(StrEnum):
    ASSET = "asset"
    PAIR = "pair"
    POOL = "pool"
    VENUE = "venue"
    ISSUER = "issuer"
    UNDERLYING = "underlying"
    PERP_CONTRACT = "perp_contract"
    PERP_VENUE = "perp_venue"
    THEME = "theme"
    CATEGORY = "category"


class MappingStatus(StrEnum):
    """State of an asset -> underlying mapping.

    Unmatched symbols go to ``PENDING_REVIEW`` rather than being guessed. The source
    data contains traps: GOLD, GOLDJM and GLDMINE are three different underlyings,
    and SKHX and SKHY trade roughly 7x apart.
    """

    AUTO = "auto"  # matched by suffix-stripping rules
    REVIEWED = "reviewed"  # confirmed by a human
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"
