"""Declarative base and shared column helpers.

This module deliberately imports no models — that would be circular. Registration
happens in ``app.models.__init__``, which Alembic's ``env.py`` imports before
diffing ``Base.metadata``; a model missing from there produces an empty migration
rather than an error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar

from sqlalchemy import DateTime, Enum, Numeric, String
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every table in the system."""


_E = TypeVar("_E", bound=StrEnum)


def enum_column(enum_cls: type[_E], **kwargs: Any) -> Any:
    """A portable enum column that stores the *value*, not the member name.

    ``native_enum=False`` keeps this a VARCHAR + CHECK constraint so the same schema
    works on SQLite (local) and MySQL (compose/production) without divergence.
    """
    return mapped_column(
        Enum(
            enum_cls,
            native_enum=False,
            length=32,
            values_callable=lambda e: [member.value for member in e],
        ),
        **kwargs,
    )


def money_column(**kwargs: Any) -> Any:
    """A USD amount.

    ``Numeric`` rather than ``Float``: these figures end up in reports that are
    reconciled against exchange statements, and binary floating point loses cents in
    ways that surface as unexplained differences. Nullable by default — a missing
    value means *not verified*, never zero.
    """
    return mapped_column(Numeric(30, 8), nullable=True, **kwargs)


def ratio_column(**kwargs: Any) -> Any:
    """A proportion (share, funding rate, spread). Never summed; see core.metrics."""
    return mapped_column(Numeric(18, 10), nullable=True, **kwargs)


def id_column(**kwargs: Any) -> Any:
    """A human-readable natural key such as ``SPY`` or ``bStocks``."""
    return mapped_column(String(96), **kwargs)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def created_at_column(**kwargs: Any) -> Any:
    return mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, **kwargs
    )


def snapshot_pk_column(**kwargs: Any) -> Any:
    """The observation time of a fact row, part of that table's primary key.

    Declared per fact table rather than on a shared mixin: it participates in each
    table's composite key, and a mixin cannot express where in that key it sits.
    """
    return mapped_column(DateTime(timezone=True), primary_key=True, **kwargs)


__all__ = [
    "Base",
    "Decimal",
    "created_at_column",
    "enum_column",
    "id_column",
    "money_column",
    "ratio_column",
    "snapshot_pk_column",
    "utcnow",
]
