"""the disruption graph, rollup snapshots, crew depth, passenger impact and hotel holds

Four capabilities, one revision, because they are one product increment and splitting them
would leave the graph referencing a depth column that does not exist yet.

* `disruption_edge` — the cascade as edges over rows that already exist. There is no node
  table: nodes are addressed `kind:id`, the same vocabulary as `evidence_refs`, so `flight`,
  `pairing` and `booking` are not duplicated and cannot drift. Every edge must name the
  recorded row it was read from, enforced by a CHECK requiring exactly one of
  `derived_from_action_id` / `derived_from_prediction_id`. An edge with no evidence behind it
  is an assertion. Two columns because root-cause edges derive from a `prediction` — the
  weather is not an action anyone took — while crew and connection edges derive from an
  `action`.
* `cascade_snapshot` — an append-only record of a rollup computation, with the action ids it
  read and a hash over them. Derivation stays the source of truth; this makes a figure
  replayable and stops the dashboard recomputing on every poll. Deliberately NOT denormalised
  onto `incident_group`: a mutable rollup column drifts from the rows it summarises and then
  nothing says which is right.
* `pairing_impact.depth` — separates the direct crew set from anything reached by second-order
  expansion, so switching expansion on cannot move the headline count of nine.
* `passenger_impact` — a recorded operational priority with named factors, which is what makes
  a partial hotel allocation defensible rather than arbitrary.
* `hotel_inventory_hold` — availability as `total_rooms - sum(active holds)` instead of a
  mutated counter on `hotel`. A counter loses updates and cannot be replayed.

Revision ID: 0007_cascade_graph_and_impact
Revises: 0006_plan_approval
Create Date: 2026-08-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_cascade_graph_and_impact"
down_revision: str | None = "0006_plan_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "disruption_edge",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_group_id", sa.BigInteger(), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("target_ref", sa.String(length=64), nullable=False),
        sa.Column("edge_kind", sa.String(length=24), nullable=False),
        sa.Column("mechanism", sa.String(length=24), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("derived_from_action_id", sa.BigInteger(), nullable=True),
        sa.Column("derived_from_prediction_id", sa.BigInteger(), nullable=True),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("depth", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("is_at_risk", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "edge_kind IN ('root_cause','crew','connection','accommodation')",
            name="ck_disruption_edge_disruption_edge_kind_valid",
        ),
        sa.CheckConstraint(
            "depth >= 0", name="ck_disruption_edge_disruption_edge_depth_non_negative"
        ),
        sa.CheckConstraint(
            "(derived_from_action_id IS NULL) <> (derived_from_prediction_id IS NULL)",
            name="ck_disruption_edge_disruption_edge_exactly_one_evidence_row",
        ),
        sa.ForeignKeyConstraint(
            ["derived_from_action_id"],
            ["action.id"],
            name="fk_disruption_edge_derived_from_action_id_action",
        ),
        sa.ForeignKeyConstraint(
            ["derived_from_prediction_id"],
            ["prediction.id"],
            name="fk_disruption_edge_derived_from_prediction_id_prediction",
        ),
        sa.ForeignKeyConstraint(
            ["incident_group_id"],
            ["incident_group.id"],
            name="fk_disruption_edge_incident_group_id_incident_group",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_disruption_edge"),
        sa.UniqueConstraint(
            "incident_group_id",
            "source_ref",
            "target_ref",
            "edge_kind",
            "mechanism",
            name="uq_disruption_edge_unique",
        ),
    )
    op.create_index(
        "ix_disruption_edge_incident_group_id", "disruption_edge", ["incident_group_id"]
    )

    op.create_table(
        "cascade_snapshot",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_group_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("flights_affected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("passengers_affected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("connections_at_risk", sa.Integer(), server_default="0", nullable=False),
        sa.Column("candidate_hotels", sa.Integer(), server_default="0", nullable=False),
        sa.Column("crew_pairings_affected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rooms_required", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_inr", sa.Integer(), nullable=True),
        sa.Column("incidents_in_group", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "incidents_assessed_connections", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("incidents_assessed_crew", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source_action_ids", JSON_TYPE, nullable=False),
        sa.Column("payload", JSON_TYPE, nullable=False),
        sa.ForeignKeyConstraint(
            ["incident_group_id"],
            ["incident_group.id"],
            name="fk_cascade_snapshot_incident_group_id_incident_group",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cascade_snapshot"),
    )
    op.create_index(
        "ix_cascade_snapshot_incident_group_id", "cascade_snapshot", ["incident_group_id"]
    )
    op.create_index("ix_cascade_snapshot_snapshot_hash", "cascade_snapshot", ["snapshot_hash"])

    op.add_column(
        "pairing_impact", sa.Column("depth", sa.SmallInteger(), server_default="1", nullable=False)
    )

    op.create_table(
        "passenger_impact",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("incident_group_id", sa.BigInteger(), nullable=False),
        sa.Column("passenger_id", sa.BigInteger(), nullable=False),
        sa.Column("booking_id", sa.BigInteger(), nullable=False),
        sa.Column("priority_index", sa.SmallInteger(), nullable=False),
        sa.Column("priority_band", sa.String(length=12), nullable=False),
        sa.Column("factors", JSON_TYPE, nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("ruleset_hash", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "priority_index >= 0 AND priority_index <= 100",
            name="ck_passenger_impact_passenger_impact_index_range",
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["action.id"], name="fk_passenger_impact_action_id_action"
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"], ["booking.id"], name="fk_passenger_impact_booking_id_booking"
        ),
        sa.ForeignKeyConstraint(
            ["incident_group_id"],
            ["incident_group.id"],
            name="fk_passenger_impact_incident_group_id_incident_group",
        ),
        sa.ForeignKeyConstraint(
            ["passenger_id"], ["passenger.id"], name="fk_passenger_impact_passenger_id_passenger"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_passenger_impact"),
        sa.UniqueConstraint(
            "incident_group_id",
            "passenger_id",
            "action_id",
            name="uq_passenger_impact_unique",
        ),
    )
    op.create_index("ix_passenger_impact_action_id", "passenger_impact", ["action_id"])
    op.create_index(
        "ix_passenger_impact_incident_group_id", "passenger_impact", ["incident_group_id"]
    )

    op.create_table(
        "hotel_inventory_hold",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("action_id", sa.BigInteger(), nullable=True),
        sa.Column("hotel_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_group_id", sa.BigInteger(), nullable=True),
        sa.Column("rooms", sa.SmallInteger(), nullable=False),
        sa.Column(
            "held_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint(
            "rooms > 0", name="ck_hotel_inventory_hold_hotel_inventory_hold_rooms_positive"
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["action.id"], name="fk_hotel_inventory_hold_action_id_action"
        ),
        sa.ForeignKeyConstraint(
            ["hotel_id"], ["hotel.id"], name="fk_hotel_inventory_hold_hotel_id_hotel"
        ),
        sa.ForeignKeyConstraint(
            ["incident_group_id"],
            ["incident_group.id"],
            name="fk_hotel_inventory_hold_incident_group_id_incident_group",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hotel_inventory_hold"),
    )
    op.create_index("ix_hotel_inventory_hold_action_id", "hotel_inventory_hold", ["action_id"])
    op.create_index("ix_hotel_inventory_hold_hotel_id", "hotel_inventory_hold", ["hotel_id"])


def downgrade() -> None:
    op.drop_table("hotel_inventory_hold")
    op.drop_table("passenger_impact")
    op.drop_column("pairing_impact", "depth")
    op.drop_table("cascade_snapshot")
    op.drop_table("disruption_edge")
