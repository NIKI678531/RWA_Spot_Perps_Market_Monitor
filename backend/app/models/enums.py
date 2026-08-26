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


class AlertState(StrEnum):
    """Where an alert sits in the detect -> verify -> explain -> act -> review loop.

    This is the business state, distinct from :class:`AlertStatus`, which only ever
    described the *detector's* confidence. An alert is a work item with an owner and
    a resolution, so the two cannot share one column: an alert can be statistically
    ``CONFIRMED`` and organisationally ``DISMISSED`` at the same time.

    ``DETECTED`` is a private state. It is what a detector emits before the
    publication gate has run, and it must never appear in an API response or on a
    screen — that is the whole point of having a gate.
    """

    DETECTED = "detected"  # pre-gate; never rendered
    TENTATIVE = "tentative"  # published but awaiting a second confirmation
    PUBLISHED = "published"  # visible, unclaimed
    CLAIMED = "claimed"  # an owner took it
    IN_REVIEW = "in_review"  # analysis underway, evidence being checked
    ACTIONED = "actioned"  # a decision was taken and recorded
    RESOLVED = "resolved"  # closed with a retrospective
    FALSE_POSITIVE = "false_positive"  # closed: the finding was not real
    DISMISSED = "dismissed"  # closed: real but deliberately not acted on


#: States an alert may occupy while still counting as open work.
OPEN_ALERT_STATES = frozenset(
    {
        AlertState.TENTATIVE,
        AlertState.PUBLISHED,
        AlertState.CLAIMED,
        AlertState.IN_REVIEW,
        AlertState.ACTIONED,
    }
)

#: Terminal states. Reaching one requires a retrospective or a declined reason.
CLOSED_ALERT_STATES = frozenset(
    {
        AlertState.RESOLVED,
        AlertState.FALSE_POSITIVE,
        AlertState.DISMISSED,
    }
)

#: The only legal transitions. Anything else is a bug, not a workflow variation;
#: ``alert_lifecycle`` raises rather than silently accepting an unmodelled jump.
ALERT_TRANSITIONS: dict[AlertState, frozenset[AlertState]] = {
    AlertState.DETECTED: frozenset({AlertState.TENTATIVE, AlertState.PUBLISHED}),
    AlertState.TENTATIVE: frozenset(
        {
            AlertState.PUBLISHED,
            AlertState.CLAIMED,
            AlertState.FALSE_POSITIVE,
            AlertState.DISMISSED,
        }
    ),
    AlertState.PUBLISHED: frozenset(
        {AlertState.CLAIMED, AlertState.FALSE_POSITIVE, AlertState.DISMISSED}
    ),
    AlertState.CLAIMED: frozenset(
        {
            AlertState.IN_REVIEW,
            AlertState.ACTIONED,
            AlertState.PUBLISHED,  # released back to the queue
            AlertState.FALSE_POSITIVE,
            AlertState.DISMISSED,
        }
    ),
    AlertState.IN_REVIEW: frozenset(
        {AlertState.ACTIONED, AlertState.FALSE_POSITIVE, AlertState.DISMISSED}
    ),
    AlertState.ACTIONED: frozenset({AlertState.RESOLVED, AlertState.DISMISSED}),
    AlertState.RESOLVED: frozenset(),
    AlertState.FALSE_POSITIVE: frozenset(),
    AlertState.DISMISSED: frozenset(),
}


class AlertActionType(StrEnum):
    """One entry in an alert's append-only action stream.

    The alert's ``state`` column is a materialised view of this stream. The stream is
    the record of what people did; the column exists so the queue can be filtered
    without replaying history on every request.
    """

    PUBLISH = "publish"  # crossed the publication gate
    CONFIRM = "confirm"  # a further snapshot re-fired the same finding
    CLAIM = "claim"
    RELEASE = "release"  # returned to the unclaimed queue
    REASSIGN = "reassign"
    NOTE = "note"  # a comment; changes no state
    START_REVIEW = "start_review"
    ACTION = "action"  # a business decision was taken
    CREATE_TASK = "create_task"  # spawned a research task
    DEFER = "defer"  # snoozed with a wake time
    MARK_FALSE_POSITIVE = "mark_false_positive"
    DISMISS = "dismiss"
    RESOLVE = "resolve"
    EVIDENCE_APPENDED = "evidence_appended"  # a data repair added a new version


class VerificationStatus(StrEnum):
    """How much of a displayed number was actually observed.

    Five states, and collapsing any of them into another reintroduces the bug the
    whole system exists to avoid. ``EMPTY`` is an observed zero and may be stated as
    one. ``NOT_VERIFIED`` may not be rendered as a number at all. ``STALE`` blocks a
    new management-level conclusion without blocking the display of the last one.
    """

    VERIFIED = "verified"
    PARTIAL = "partial"
    NOT_VERIFIED = "not_verified"
    STALE = "stale"
    EMPTY = "empty"


#: Verification states a number may hold and still support a published conclusion.
PUBLISHABLE_VERIFICATION = frozenset(
    {
        VerificationStatus.VERIFIED,
        VerificationStatus.PARTIAL,
        VerificationStatus.EMPTY,
    }
)


class UserRole(StrEnum):
    """Who may do what. ``SHARE_VIEWER`` is not a logged-in person.

    A share link is handed to someone outside the team, so its response is redacted
    on the server: candidates, owners, actions, business notes, issuance status and
    thresholds never enter the payload at all.
    """

    VIEWER = "viewer"
    ANALYST = "analyst"
    OWNER = "owner"
    ADMIN = "admin"
    SHARE_VIEWER = "share_viewer"


class EditionKind(StrEnum):
    """Which cut of the day a rendered edition represents."""

    LIVE = "live"  # rolling, never frozen, never quotable
    MORNING = "morning"  # 09:00 HKT freeze
    AFTERNOON = "afternoon"  # 17:00 HKT freeze


class EditionStatus(StrEnum):
    LIVE = "live"
    FROZEN = "frozen"
    #: A later revision replaced this one. It stays readable: someone quoted it.
    SUPERSEDED = "superseded"
    FAILED = "failed"  # the freeze job did not complete; must page, never slip


class ResearchTaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class CandidateStage(StrEnum):
    """Issuance evaluation gates.

    A candidate moves between these only through a recorded human decision. No
    composite score advances a stage, and the UI must not imply one could.
    """

    WATCH = "watch"
    SCREENING = "screening"
    FEASIBILITY = "feasibility"
    PROPOSAL = "proposal"
    APPROVED = "approved"
    DECLINED = "declined"
    PARKED = "parked"


class CandidateDecision(StrEnum):
    ADVANCE = "advance"
    HOLD = "hold"
    DECLINE = "decline"
    PARK = "park"


class CoverageState(StrEnum):
    """Whether we have a product against a given underlying."""

    LIVE = "live"  # we list it
    PLANNED = "planned"
    GAP = "gap"  # demand exists, we have nothing
    NOT_APPLICABLE = "not_applicable"


class DataGapStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    FIXED = "fixed"
    ACCEPTED = "accepted"  # known, deliberately not fixed; carries a reason


class DataGapKind(StrEnum):
    """Which of the five data-quality zones a gap belongs to."""

    SOURCE_HEALTH = "source_health"
    ENTITY_COVERAGE = "entity_coverage"
    QUALITY_DIVERGENCE = "quality_divergence"
    VERIFICATION = "verification"
    BASELINE_HEALTH = "baseline_health"


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
