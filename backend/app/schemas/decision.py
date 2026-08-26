"""The decision home payload, and the search that reaches everything else.

The home page is not a summary of the dashboard. It answers four fixed questions and
states, for each answer, what argues against it and how much weight it will bear. The
shapes below encode that: there is no way to construct a :class:`QuestionCard` that
carries an answer and no counter-evidence field, and no way to return a KPI block with
a total across scopes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.metrics import MetricScope
from app.models.enums import EntityType
from app.schemas.common import (
    Confidence,
    CounterEvidenceItem,
    EvidenceItem,
    Meta,
    MetricValue,
    StatusBar,
)
from app.schemas.workflow import QueueAlertRow

#: The four questions, in the order they appear. Fixed: the page's structure is the
#: product decision, not a layout preference, so the keys are part of the contract.
QuestionKey = Literal["q1_volume", "q2_heating", "q3_new_products", "q4_issuance"]


class SummaryLine(BaseModel):
    """The one-line summary at the top of the page (HOME-002).

    Four parts, always in this order: what happened, what it means, how far it can be
    trusted, what to do. Split into fields rather than shipped as a sentence so the
    "confidence" part cannot quietly go missing when the copy gets tightened, and so
    an unverifiable dependency can suppress the conclusion instead of the hedge.
    """

    fact: str
    meaning: str
    confidence: Confidence
    action: str | None = None
    action_href: str | None = None
    #: True when a dependency was not verifiable. The UI then states what cannot be
    #: judged and why, and ``meaning`` carries that explanation rather than a claim.
    indeterminate: bool = False


class QuestionCard(BaseModel):
    """One of the four question cards (HOME-003)."""

    key: QuestionKey
    question: str
    #: The answer as a sentence — an entity name, a direction, a magnitude.
    answer: str
    #: The headline figure behind the answer, where there is one.
    value: MetricValue | None = None
    #: Change against the comparison basis named in ``change_basis``.
    change_pct: float | None = None
    change_basis: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    #: Never empty on a card that states an answer; see rule 18.
    counter_evidence: list[CounterEvidenceItem] = Field(default_factory=list)
    confidence: Confidence
    cta_label: str
    cta_href: str
    #: Shown when the underlying data could not support an answer. The card then
    #: renders the reason instead of a number — an empty card is not an answer.
    unavailable_reason: str | None = None


class KpiCard(BaseModel):
    """One of the five KPI blocks (HOME-004).

    Five separate cards, one per metric scope, laid out physically apart. There is no
    total field and no place to put one: the five numbers are different kinds of
    quantity and their sum has no referent.
    """

    scope: MetricScope
    label: str
    value: MetricValue
    change_pct: float | None = None
    change_basis: str | None = None
    #: Where this scope's depth page lives.
    href: str | None = None


class Highlight(BaseModel):
    """One opportunity or risk in the two lists under the question cards."""

    kind: Literal["opportunity", "risk"]
    title: str
    detail: str | None = None
    entity_type: EntityType | None = None
    entity_id: str | None = None
    value: MetricValue | None = None
    confidence: Confidence | None = None
    counter_evidence: list[CounterEvidenceItem] = Field(default_factory=list)
    href: str | None = None
    #: The alert this came from, when it came from one.
    alert_id: int | None = None


class DrillLink(BaseModel):
    """One entry in the quick drill-down strip at the foot of the page."""

    label: str
    href: str
    detail: str | None = None


class DecisionHome(BaseModel):
    """The whole home page, in the order it renders (HOME-001)."""

    meta: Meta
    status_bar: StatusBar
    summary: SummaryLine
    questions: list[QuestionCard]
    opportunities: list[Highlight] = Field(default_factory=list)
    risks: list[Highlight] = Field(default_factory=list)
    kpis: list[KpiCard] = Field(default_factory=list)
    #: Management-facing, so ``high``/``critical`` with complete evidence only
    #: (HOME-005). The publication gate decides this, not the route.
    alerts: list[QueueAlertRow] = Field(default_factory=list)
    drilldowns: list[DrillLink] = Field(default_factory=list)


class SearchHit(BaseModel):
    """One result from the unified search (GEN-002)."""

    entity_type: EntityType
    entity_id: str
    name: str
    #: What kind of thing this is, in the reader's words — 底层 / 包装 / 发行商 /
    #: 场所 / 交易对 / 合约 / 主题.
    kind_label: str
    #: The secondary line: issuer, venue, or the underlying a wrapper belongs to.
    detail: str | None = None
    #: Where selecting it goes. Wrappers, pairs and contracts all resolve to their
    #: Underlying 360 with the filter context preserved rather than to a page of
    #: their own — the entity people reason about is the underlying.
    href: str
    score: float = 0.0


class SearchResults(BaseModel):
    meta: Meta
    query: str
    hits: list[SearchHit]
    #: Counts per kind, so the UI can group without re-scanning the list.
    counts: dict[str, int] = Field(default_factory=dict)


class HomeAsOf(BaseModel):
    """Trivial envelope used by the status-bar endpoint."""

    status_bar: StatusBar
    server_time: datetime


__all__ = [
    "DecisionHome",
    "DrillLink",
    "Highlight",
    "HomeAsOf",
    "KpiCard",
    "QuestionCard",
    "QuestionKey",
    "SearchHit",
    "SearchResults",
    "SummaryLine",
]
