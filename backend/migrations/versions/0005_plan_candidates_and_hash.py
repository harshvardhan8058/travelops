"""candidate plans, selection attribution and a deterministic plan hash

Stream A's candidate lifecycle (A2.4) and plan comparison (A2.5) both wait on this, so it is
deliberately small and lands early.

Four columns and two indexes:

* `selection_state` defaults to `candidate`, so every existing row keeps the Phase 1 meaning
  and `_current_plan`'s "latest when nothing is selected" fallback behaves identically. The
  backfill is a no-op by design.
* A partial unique index gives "one selected plan per incident" from the database, which is
  what makes a second, different selection a 409 rather than a race — the same shape as
  `human_decision`'s uniqueness.
* A CHECK requires `selected_at` and `selected_by` whenever the state is `selected`, so a
  selection cannot be recorded without attribution.
* `plan_hash` is the identity an approval binds to. A re-planned or reordered plan hashes
  differently and stops being covered; without it, "approve the plan" silently grows to cover
  tasks nobody reviewed.

Revision ID: 0005_plan_candidates_and_hash
Revises: 0004_incident_group_flight
Create Date: 2026-08-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_plan_candidates_and_hash"
down_revision: str | None = "0004_incident_group_flight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SELECTED = sa.text("selection_state = 'selected'")


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column(
            "selection_state",
            sa.String(length=12),
            server_default="candidate",
            nullable=False,
        ),
    )
    op.add_column("plan", sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan", sa.Column("selected_by", sa.String(length=64), nullable=True))
    op.add_column("plan", sa.Column("variant_key", sa.String(length=32), nullable=True))
    op.add_column("plan", sa.Column("plan_hash", sa.String(length=64), nullable=True))

    op.create_check_constraint(
        "plan_selection_state_valid",
        "plan",
        "selection_state IN ('candidate','selected','discarded')",
    )
    op.create_check_constraint(
        "plan_selection_attributed",
        "plan",
        "selection_state <> 'selected' OR "
        "(selected_at IS NOT NULL AND selected_by IS NOT NULL)",
    )

    op.create_index("ix_plan_plan_hash", "plan", ["plan_hash"])
    op.create_index("ix_plan_incident_selection", "plan", ["incident_id", "selection_state"])
    op.create_index(
        "uq_plan_selected_per_incident",
        "plan",
        ["incident_id"],
        unique=True,
        postgresql_where=_SELECTED,
        sqlite_where=_SELECTED,
    )


def downgrade() -> None:
    op.drop_index("uq_plan_selected_per_incident", table_name="plan")
    op.drop_index("ix_plan_incident_selection", table_name="plan")
    op.drop_index("ix_plan_plan_hash", table_name="plan")
    op.drop_constraint("ck_plan_plan_selection_attributed", "plan", type_="check")
    op.drop_constraint("ck_plan_plan_selection_state_valid", "plan", type_="check")
    op.drop_column("plan", "plan_hash")
    op.drop_column("plan", "variant_key")
    op.drop_column("plan", "selected_by")
    op.drop_column("plan", "selected_at")
    op.drop_column("plan", "selection_state")
