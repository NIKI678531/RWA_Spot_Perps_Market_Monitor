"""case-sensitive source symbols in underlying_map

``AAPLx`` is an xStocks wrapper and ``AAPLX`` is an unrecognised ticker ending in X.
The suffix rules in ``normalize.underlying_map`` are case-sensitive for exactly that
reason, so the two must be storable as two rows.

MySQL 8.4 defaults every utf8mb4 column to ``utf8mb4_0900_ai_ci``, which compares
case-insensitively. Under that collation ``uq_underlying_map_source`` treats the two
spellings as one key: the second insert raises, the flush aborts, and the collector
loses every row it gathered — not just the symbol that clashed. SQLite compares
case-sensitively, so this never reproduces locally and only ever appears in
deployment.

Only the two columns in that unique constraint are pinned. Elsewhere a
case-insensitive comparison is harmless or helpful.

Revision ID: 7d5a1c2e9b04
Revises: e28ffa03520c
Create Date: 2026-08-18 11:20:00.000000

"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d5a1c2e9b04"
down_revision: str | None = "e28ffa03520c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Binary rather than ``utf8mb4_0900_as_cs``: a ticker is an identifier, so two
#: spellings are the same symbol only when they are the same bytes.
_CASE_SENSITIVE = "utf8mb4_bin"


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        # SQLite already compares strings by bytes, and has no per-column collation
        # to set. Nothing to do rather than a no-op ALTER that batch mode would have
        # to rebuild the table for.
        return
    for column in ("source_id", "source_symbol"):
        op.alter_column(
            "underlying_map",
            column,
            existing_type=sa.String(96),
            type_=sa.String(96, collation=_CASE_SENSITIVE),
            existing_nullable=False,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for column in ("source_id", "source_symbol"):
        op.alter_column(
            "underlying_map",
            column,
            existing_type=sa.String(96, collation=_CASE_SENSITIVE),
            type_=sa.String(96),
            existing_nullable=False,
        )
