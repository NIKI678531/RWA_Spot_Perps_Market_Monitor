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

from pydantic import BaseModel, Field

from app.core.metrics import SCOPE_DIMENSION, MetricDimension, MetricScope, ScopedValue
from app.services.report.dataset import Coverage, coverage

#: Rendered by the UI as a grey placeholder, never as a zero-height bar.
NOT_VERIFIED: Coverage = "not_verified"


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


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    environment: str
    database: str
    #: Newest observation in the warehouse. Null before the first collection.
    as_of: datetime | None = None
