"""record where a runway's true heading came from

Crosswind is a function of wind direction relative to runway orientation, so
`runway.heading_degrees_true` is load-bearing for every delay-risk score. The OurAirports
snapshot does not supply a true heading for every runway — VOBL 09L/27R is blank upstream,
and VOBL is the demo airport — so those headings are derived from the designator instead.

Without this column a derived heading is indistinguishable from a surveyed one, and a risk
index would implicitly claim precision it does not have. `real` provenance on the airport
row would be doing work the runway row cannot support.

Additive and defaulted, so nothing existing has to change.

Revision ID: 0002_runway_heading_source
Revises: 0001_initial_schema
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_runway_heading_source"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runway",
        sa.Column(
            "heading_source",
            sa.String(length=24),
            nullable=False,
            server_default="ourairports_true",
        ),
    )
    op.create_check_constraint(
        "runway_heading_source_known",
        "runway",
        "heading_source IN ('ourairports_true', 'designator_derived')",
    )


def downgrade() -> None:
    op.drop_constraint("runway_heading_source_known", "runway", type_="check")
    op.drop_column("runway", "heading_source")
