"""Crew pairings.

The load-bearing design point: crew are assigned to PAIRINGS, not to flights. One flight
can carry several pairings, and one pairing spans several flights. That many-to-many
relationship is why eight delayed flights can affect nine rotations, and modelling it
explicitly is what makes the number countable rather than asserted.

A flat crew -> flight column would make the cascade claim indefensible.

Scope boundary: coordination and display only. Duty-time legality is NOT validated
anywhere in this system.

Owner: Stream C (schema) / Stream D (impact traversal).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import PairingLegRole, ProvenanceKind


class CrewMember(Base):
    __tablename__ = "crew_member"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # cockpit | cabin
    base_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)

    # Indicative display flag only. NOT a compliance decision.
    duty_hours_limit: Mapped[int | None] = mapped_column(SmallInteger)

    provenance_kind: Mapped[ProvenanceKind] = mapped_column(
        String(16), nullable=False, default=ProvenanceKind.synthetic
    )


class Pairing(Base):
    """A multi-leg duty sequence beginning and ending at a home base."""

    __tablename__ = "pairing"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    base_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    legs: Mapped[list[PairingLeg]] = relationship(
        back_populates="pairing", order_by="PairingLeg.leg_order"
    )
    assignments: Mapped[list[CrewPairingAssignment]] = relationship(back_populates="pairing")


class PairingLeg(Base):
    """One flight within a pairing.

    `role` is what makes the cascade explainable:
      operating   - this crew works the flight
      positioning - this crew travels as a passenger to operate a later flight
    """

    __tablename__ = "pairing_leg"

    id: Mapped[int] = mapped_column(primary_key=True)
    pairing_id: Mapped[int] = mapped_column(ForeignKey("pairing.id"), nullable=False, index=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id"), nullable=False, index=True)
    leg_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    role: Mapped[PairingLegRole] = mapped_column(String(16), nullable=False)

    # Minimum turnaround before the next leg is infeasible. Drives forward propagation.
    min_connection_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=45)

    pairing: Mapped[Pairing] = relationship(back_populates="legs")

    __table_args__ = (
        UniqueConstraint("pairing_id", "leg_order", name="pairing_leg_order_unique"),
        Index("ix_pairing_leg_flight_pairing", "flight_id", "pairing_id"),
    )


class CrewPairingAssignment(Base):
    __tablename__ = "crew_pairing_assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    crew_member_id: Mapped[int] = mapped_column(ForeignKey("crew_member.id"), nullable=False)
    pairing_id: Mapped[int] = mapped_column(ForeignKey("pairing.id"), nullable=False)

    pairing: Mapped[Pairing] = relationship(back_populates="assignments")

    __table_args__ = (UniqueConstraint("crew_member_id", "pairing_id", name="crew_pairing_unique"),)


class PairingImpact(Base):
    """A recorded, explainable reason why a pairing is at risk.

    `mechanism` is rendered as the edge label in the cascade graph, so a reviewer can read
    why each affected rotation is affected instead of trusting a headline count.
    """

    __tablename__ = "pairing_impact"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_group_id: Mapped[int] = mapped_column(
        ForeignKey("incident_group.id"), nullable=False, index=True
    )
    pairing_id: Mapped[int] = mapped_column(ForeignKey("pairing.id"), nullable=False)
    source_flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id"), nullable=False)
    affected_leg_id: Mapped[int] = mapped_column(ForeignKey("pairing_leg.id"), nullable=False)

    # operating | onward_duty | second_pairing | positioning
    mechanism: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    is_at_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint(
            "incident_group_id", "pairing_id", "affected_leg_id", name="pairing_impact_unique"
        ),
    )
