"""plan-level approval, covering low and medium risk only

P2-D3: an operator may approve a plan, and that approval may authorise its low and medium risk
actions. High risk always requires its own action-level approval, and no approval ever covers a
failed check.

A separate table rather than a nullable `human_decision.assurance_id`. Making that column
nullable would legalise an action whose `needs_human` references no decision at all, which is
precisely the Phase 1 invariant.

`plan_approval_tier` is a child table so that "never high" is a CHECK constraint. P2-D3's
central rule is then a database guarantee instead of something application code must remember.

`action.plan_approval_id` is the second of two mutually exclusive authorisation routes. The
cross-row rule — exactly one of `human_decision_id` / `plan_approval_id`, and for a plan
approval the tier must be low or medium, no check may have FAILED, the task id must be covered,
and `plan.plan_hash` must still match — is enforced in the service transaction and tested,
the same way the existing action↔evaluation rule is.

Revision ID: 0006_plan_approval
Revises: 0005_plan_candidates_and_hash
Create Date: 2026-08-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_plan_approval"
down_revision: str | None = "0005_plan_candidates_and_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "plan_approval",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_group_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("covered_task_ids", JSON_TYPE, nullable=False),
        sa.Column("gate_config_version", sa.String(length=32), nullable=False),
        sa.Column("gate_config_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], name="fk_plan_approval_plan_id_plan"),
        sa.ForeignKeyConstraint(
            ["incident_group_id"],
            ["incident_group.id"],
            name="fk_plan_approval_incident_group_id_incident_group",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_approval"),
        # One signature per plan version. A re-plan produces a new hash and needs a new one.
        sa.UniqueConstraint("plan_id", "plan_hash", name="uq_plan_approval_plan_hash_unique"),
    )
    op.create_index("ix_plan_approval_plan_id", "plan_approval", ["plan_id"])

    op.create_table(
        "plan_approval_tier",
        sa.Column("plan_approval_id", sa.BigInteger(), nullable=False),
        sa.Column("risk_tier", sa.String(length=8), nullable=False),
        sa.CheckConstraint(
            "risk_tier IN ('low','medium')",
            name="ck_plan_approval_tier_plan_approval_tier_never_high",
        ),
        sa.ForeignKeyConstraint(
            ["plan_approval_id"],
            ["plan_approval.id"],
            name="fk_plan_approval_tier_plan_approval_id_plan_approval",
        ),
        sa.PrimaryKeyConstraint("plan_approval_id", "risk_tier", name="pk_plan_approval_tier"),
    )

    op.add_column("action", sa.Column("plan_approval_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_action_plan_approval_id_plan_approval",
        "action",
        "plan_approval",
        ["plan_approval_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_action_plan_approval_id_plan_approval", "action", type_="foreignkey")
    op.drop_column("action", "plan_approval_id")
    op.drop_table("plan_approval_tier")
    op.drop_index("ix_plan_approval_plan_id", table_name="plan_approval")
    op.drop_table("plan_approval")
