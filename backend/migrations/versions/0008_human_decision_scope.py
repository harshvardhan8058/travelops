"""Human decision scope: tell a plan-wide signature from a per-action one.

Migration 0006 gave us `plan_approval` and linked it from `action`. What it did not do is
record, on the decision itself, *how* a person authorised something.

Both routes are a human's act and both must keep reading as `actor_kind=human` — that was the
Phase 1 fix and it is not being reopened. But "the operator approved this cash payout" and
"this refund was covered by the operator's plan-wide signature" are different facts, and an
auditor has to be able to separate them without inferring it from timestamps.

Two constraints carry the guarantees:

* `human_decision_scope_valid` — only `action` or `plan`.
* `human_decision_scope_provenance` — a `plan`-scoped decision MUST name the `plan_approval`
  that produced it, and an `action`-scoped one must not. A plan-scoped row with no approval
  behind it would be an authorisation nobody could trace, which is the defect Phase 1 closed.

`assurance_id` stays `UNIQUE NOT NULL`. One decision, one evaluation, whichever route created
it — that is what keeps `execute()` needing no knowledge of plan approvals at all.

Existing rows are `action`, which is what they were.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008_human_decision_scope"
down_revision: str | None = "0007_cascade_graph_and_impact"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "human_decision",
        sa.Column("scope", sa.String(length=8), nullable=False, server_default="action"),
    )
    op.add_column(
        "human_decision",
        sa.Column("plan_approval_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_human_decision_plan_approval_id_plan_approval"),
        "human_decision",
        "plan_approval",
        ["plan_approval_id"],
        ["id"],
    )
    op.create_check_constraint(
        "human_decision_scope_valid",
        "human_decision",
        "scope IN ('action','plan')",
    )
    op.create_check_constraint(
        "human_decision_scope_provenance",
        "human_decision",
        "(scope = 'action' AND plan_approval_id IS NULL) OR "
        "(scope = 'plan' AND plan_approval_id IS NOT NULL)",
    )
    op.create_index(
        "ix_human_decision_plan_approval",
        "human_decision",
        ["plan_approval_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_human_decision_plan_approval", table_name="human_decision")
    op.drop_constraint("human_decision_scope_provenance", "human_decision", type_="check")
    op.drop_constraint("human_decision_scope_valid", "human_decision", type_="check")
    op.drop_constraint(
        op.f("fk_human_decision_plan_approval_id_plan_approval"),
        "human_decision",
        type_="foreignkey",
    )
    op.drop_column("human_decision", "plan_approval_id")
    op.drop_column("human_decision", "scope")
