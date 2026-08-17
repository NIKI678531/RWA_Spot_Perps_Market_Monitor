"""perp venue open-interest coverage

Volume for a perp venue comes from one bulk call while open interest costs one
request per symbol, so the two money columns of a single rollup row can cover
different numbers of contracts. Without this column a capped open-interest total
reads as the venue's whole book.

Revision ID: b1c4f0a97d52
Revises: 8e37454e71a4
Create Date: 2026-08-17 21:40:00.000000

"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c4f0a97d52"
down_revision: str | None = "8e37454e71a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fact_perp_venue_snapshot",
        sa.Column("oi_symbol_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fact_perp_venue_snapshot", "oi_symbol_count")
