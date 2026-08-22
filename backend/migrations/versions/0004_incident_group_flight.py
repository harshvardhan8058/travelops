"""declare which flights a disruption group covers

Group membership becomes data. Deriving it from `flight.origin_icao == group.airport_icao`
returns seven of the eight flights in the storm scenario, because UK 705 is an arrival
(`VAAH → VOBL`). Seven flights still produce nine pairings, so the headline count looks right
while the `onward_duty` mechanism silently disappears and PAIR-E1 is relabelled `operating`.
A wrong number gets caught in review; a right number reached the wrong way does not.

Unblocks the cascade-open loop: one incident per member flight, all in one group.

Revision ID: 0004_incident_group_flight
Revises: 0003_weather_is_forecast
Create Date: 2026-08-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_incident_group_flight"
down_revision: str | None = "0003_weather_is_forecast"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_group_flight",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_group_id", sa.BigInteger(), nullable=False),
        sa.Column("flight_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "delay_minutes_at_injection",
            sa.SmallInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "provenance_kind",
            sa.String(length=16),
            server_default="synthetic",
            nullable=False,
        ),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "role IN ('primary','affected_departure','affected_arrival')",
            name="ck_incident_group_flight_incident_group_flight_role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["flight_id"], ["flight.id"], name="fk_incident_group_flight_flight_id_flight"
        ),
        sa.ForeignKeyConstraint(
            ["incident_group_id"],
            ["incident_group.id"],
            name="fk_incident_group_flight_incident_group_id_incident_group",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incident_group_flight"),
        sa.UniqueConstraint(
            "incident_group_id", "flight_id", name="uq_incident_group_flight_unique"
        ),
    )
    op.create_index(
        "ix_incident_group_flight_incident_group_id",
        "incident_group_flight",
        ["incident_group_id"],
    )
    # At most one primary flight per group, enforced by the database rather than by
    # convention, in the same shape as uq_incident_active_per_flight.
    op.create_index(
        "uq_incident_group_flight_primary",
        "incident_group_flight",
        ["incident_group_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary'"),
        sqlite_where=sa.text("role = 'primary'"),
    )


def downgrade() -> None:
    op.drop_index("uq_incident_group_flight_primary", table_name="incident_group_flight")
    op.drop_index(
        "ix_incident_group_flight_incident_group_id", table_name="incident_group_flight"
    )
    op.drop_table("incident_group_flight")
