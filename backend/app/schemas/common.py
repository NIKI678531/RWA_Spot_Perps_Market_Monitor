"""Response primitives shared by every endpoint.

The one idea running through this module: a money figure never travels alone. It
carries the metric scope it belongs to and how much of it was actually observed, so
a chart cannot put spot turnover and open interest on one axis by accident, and a
failed fetch cannot arrive at the browser looking like a zero.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.metrics import SCOPE_DIMENSION, MetricDimension, MetricScope, ScopedValue
from app.models.enums import VerificationStatus
from app.services.report.dataset import Coverage, coverage

#: Rendered by the UI as a grey placeholder, never as a zero-height bar.
NOT_VERIFIED: Coverage = "not_verified"

#: How a ``Coverage`` — which only ever distinguished three cases — maps onto the
#: five verification states. ``EMPTY`` and ``STALE`` cannot be derived from coverage
#: alone; a caller that knows the difference passes the status explicitly.
_COVERAGE_TO_VERIFICATION: dict[Coverage, VerificationStatus] = {
    "complete": VerificationStatus.VERIFIED,
    "partial": VerificationStatus.PARTIAL,
    "not_verified": VerificationStatus.NOT_VERIFIED,
}


class Amount(BaseModel):
    """A USD figure that knows its scope and its coverage."""

    value: Decimal | None = Field(
        default=None, description="Null means not observed. It does not mean zero."
    )
    scope: MetricScope
    dimension: MetricDimension
    coverage: Coverage

    @classmethod
    def of(cls, value: ScopedValue) -> Amount:
        return cls(
            value=value.amount,
            scope=value.scope,
            dimension=SCOPE_DIMENSION[value.scope],
            coverage=coverage(value),
        )

    @classmethod
    def raw(cls, value: Decimal | None, scope: MetricScope) -> Amount:
        return cls(
            value=value,
            scope=scope,
            dimension=SCOPE_DIMENSION[scope],
            coverage="complete" if value is not None else NOT_VERIFIED,
        )


class MetricValue(BaseModel):
    """The value object every displayable R1 number travels in.

    :class:`Amount` says what a figure is and how complete it is. This says
    everything else a reader needs before quoting it: which window it covers, when it
    was observed, how many sources agreed, and — for a ratio — what it was weighted
    by. Those are the questions that get asked *after* a number has already been
    pasted into a deck, which is too late.

    ``raw_value`` and ``adjusted_value`` are both present and both optional. Where a
    quality adjustment exists it ships alongside the raw figure rather than replacing
    it; one venue in the reference data reports ~$29.3mn raw against ~$216 adjusted,
    and either number alone misleads.
    """

    value: Decimal | None = Field(
        default=None, description="Null means not observed. It does not mean zero."
    )
    unit: str = "USD"
    metric_scope: MetricScope
    metric_dimension: MetricDimension
    #: Before and after quality screening. Null where the distinction does not apply.
    raw_value: Decimal | None = None
    adjusted_value: Decimal | None = None
    verification_status: VerificationStatus
    observed_at: datetime | None = None
    #: The period the figure covers (``24h``, ``7d``, ``spot``). Null for a stock.
    window: str | None = None
    source_count: int | None = None
    #: Mandatory for RATIO-dimension figures; see the validator below.
    weight_basis: str | None = None

    @model_validator(mode="after")
    def _check(self) -> MetricValue:
        """Reject the two shapes that silently produce a wrong number.

        A ratio without a weighting basis is the more dangerous of the two: turnover
        weighted by market cap and turnover weighted by volume are different figures
        with the same name, and defaulting to either one guesses on the reader's
        behalf. So it is rejected here rather than filled in.
        """
        if self.metric_dimension is MetricDimension.RATIO and not self.weight_basis:
            raise ValueError(
                "a RATIO-dimension value must state its weight_basis; "
                "aggregating ratios without one produces a different number"
            )
        if (
            self.verification_status is VerificationStatus.NOT_VERIFIED
            and self.value is not None
        ):
            raise ValueError(
                "a NOT_VERIFIED value must not carry a number — "
                "a failed observation is not a measurement"
            )
        return self

    @classmethod
    def of(
        cls,
        value: ScopedValue,
        *,
        observed_at: datetime | None = None,
        window: str | None = None,
        source_count: int | None = None,
        raw_value: Decimal | None = None,
        adjusted_value: Decimal | None = None,
        weight_basis: str | None = None,
        status: VerificationStatus | None = None,
    ) -> MetricValue:
        resolved = status or _COVERAGE_TO_VERIFICATION[coverage(value)]
        return cls(
            value=(
                value.amount
                if resolved is not VerificationStatus.NOT_VERIFIED
                else None
            ),
            metric_scope=value.scope,
            metric_dimension=SCOPE_DIMENSION[value.scope],
            raw_value=raw_value,
            adjusted_value=adjusted_value,
            verification_status=resolved,
            observed_at=observed_at,
            window=window,
            source_count=source_count,
            weight_basis=weight_basis,
        )

    @classmethod
    def missing(
        cls, scope: MetricScope, *, reason_window: str | None = None
    ) -> MetricValue:
        """A figure we tried and failed to observe. Never a zero."""
        return cls(
            value=None,
            metric_scope=scope,
            metric_dimension=SCOPE_DIMENSION[scope],
            verification_status=VerificationStatus.NOT_VERIFIED,
            window=reason_window,
        )


class Meta(BaseModel):
    """Envelope every list response carries.

    ``scopes`` tells the client which metric families are present. A response with
    more than one is stating that its figures are side by side, not addable — the UI
    reads this to decide between a shared axis and split charts.
    """

    as_of: datetime
    scopes: list[MetricScope] = Field(default_factory=list)
    note: str = ""
    row_count: int = 0


class StatusBar(BaseModel):
    """The strip that sits on every page (GEN-001).

    Its job is to make "when is this from and can I trust it" answerable without
    leaving the screen. Every field here has been the subject of a real argument
    about a number, which is why the bar is not optional on any page.
    """

    #: Which metric families the page is currently showing.
    scopes: list[MetricScope] = Field(default_factory=list)
    edition_key: str
    edition_kind: str
    edition_revision: int = 1
    #: Data cut-off, and separately when we rendered. Often hours apart.
    as_of: datetime
    generated_at: datetime | None = None
    #: Minutes between ``as_of`` and now. The UI turns this amber past 90.
    data_age_minutes: int | None = None
    #: Sources that answered over sources we asked.
    sources_ok: int = 0
    sources_total: int = 0
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    next_refresh_at: datetime | None = None


class EvidenceItem(BaseModel):
    """One supporting fact behind a stated conclusion."""

    label: str
    #: Present when the evidence is a figure; absent when it is a statement.
    value: MetricValue | None = None
    detail: str | None = None
    #: Where to go to check it.
    href: str | None = None


class CounterEvidenceItem(BaseModel):
    """One fact that argues against the conclusion.

    Never optional on a published finding. A page that only ever argues one way
    trains its readers to stop reading it, and then the one alert that mattered goes
    unread too.
    """

    #: Stable machine code (``raw_adjusted_gap``, ``single_venue``, ``thin_pool``,
    #: ``no_cross_venue_confirmation``, ``cold_baseline``) so the same weakness can be
    #: counted across findings rather than only read.
    code: str
    label: str
    detail: str | None = None


class Confidence(BaseModel):
    """How much weight a conclusion can bear, and why.

    Deliberately not a single number. "0.7" invites a threshold; the three fields
    below invite a reading.
    """

    level: Literal["low", "medium", "high"]
    #: Consecutive same-session confirmations behind the finding, e.g. 2 of 2.
    confirmations: int | None = None
    confirmations_required: int | None = None
    #: Baseline sample size, where the claim rests on one.
    sample_size: int | None = None
    #: The one-line reason for the level, in the reader's language.
    basis: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    environment: str
    database: str
    #: Newest observation in the warehouse. Null before the first collection.
    as_of: datetime | None = None
