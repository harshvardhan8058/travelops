"""separate forecasts from observations

`docs/11-data-model.md` specifies `is_forecast` on `weather_observation` and names the reason:
"Training a model on forecasts as though they were observations is a subtle and very common
leakage bug." The ORM model was missing the column, so a TAF and a METAR were indistinguishable
once persisted.

The weather provider already keeps them apart in flight — TAF readings carry a `taf:` prefixed
`source_ref` — but a prefix convention is not a schema guarantee. This makes the distinction
queryable, which is what any later calibration work needs.

Additive and defaulted false, so existing rows keep meaning what they meant.

Revision ID: 0003_weather_is_forecast
Revises: 0002_runway_heading_source
Create Date: 2026-08-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_weather_is_forecast"
down_revision: str | None = "0002_runway_heading_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "weather_observation",
        sa.Column(
            "is_forecast",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # The prediction feature source reads current observations for one airport, and must not
    # scan forecast rows to find them.
    op.create_index(
        "ix_weather_observation_airport_observed_actual",
        "weather_observation",
        ["airport_icao", "observed_at"],
        postgresql_where=sa.text("is_forecast = false"),
    )


def downgrade() -> None:
    op.drop_index("ix_weather_observation_airport_observed_actual", table_name="weather_observation")
    op.drop_column("weather_observation", "is_forecast")
