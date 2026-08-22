"""Phase 2 cascade tables: group membership, the disruption graph, rollup snapshots,
passenger impact, plan approval and hotel inventory holds.

Every table here exists to make a group-level figure **traceable to a persisted row or a
recorded action**. None of them stores a projection, a forecast or an inferred fact.

Three design points that are load-bearing rather than stylistic:

1. **Group membership is declared data, not an inferred query.** `incident_group_flight` says
   which flights a disruption group covers. Deriving it from `flight.origin_icao ==
   group.airport_icao` returns seven of the eight in the storm scenario — UK 705 is an arrival
   — and the missing flight still yields nine pairings while silently dropping the
   `onward_duty` mechanism. A right-looking number reached the wrong way.
2. **The graph is edges over existing rows, not a node table.** Nodes are addressed as
   `kind:id` using the same vocabulary as `evidence_refs` and `target_refs`. A node table would
   duplicate `flight`, `pairing` and `booking` and need syncing.
3. **A plan approval can never cover high risk.** That is a CHECK constraint on
   `plan_approval_tier`, not a convention in application code — see P2-D3.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ProvenanceKind

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")

#: Partial-index predicates, shared with the migration so the two cannot drift.
_PRIMARY_ROLE = text("role = 'primary'")


class IncidentGroupFlight(Base):
    """Which flights a disruption group covers. Seeded, never inferred.

    `role` distinguishes the primary flight from the rest, and separates departures from
    arrivals — the distinction that makes the eight-flight cascade correct rather than
    merely nine-shaped.
    """

    __tablename__ = "incident_group_flight"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_group_id: Mapped[int] = mapped_column(
        ForeignKey("incident_group.id"), nullable=False, index=True
    )
    flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id"), nullable=False)
    #: primary | affected_departure | affected_arrival
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    delay_minutes_at_injection: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    provenance_kind: Mapped[ProvenanceKind] = mapped_column(
        String(16), nullable=False, default=ProvenanceKind.synthetic
    )
    source_ref: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "role IN ('primary','affected_departure','affected_arrival')",
            name="incident_group_flight_role_valid",
        ),
        UniqueConstraint("incident_group_id", "flight_id", name="incident_group_flight_unique"),
        # At most one primary per group, in the database rather than by convention.
        Index(
            "uq_incident_group_flight_primary",
            "incident_group_id",
            unique=True,
            postgresql_where=_PRIMARY_ROLE,
            sqlite_where=_PRIMARY_ROLE,
        ),
    )


class DisruptionEdge(Base):
    """One traceable propagation step in the cascade.

    The provenance columns are the point of the table: every edge names the recorded row whose
    payload produced it. An edge with no evidence behind it is an assertion rather than
    evidence, so a CHECK requires exactly one to be set.

    Two columns rather than one because the two kinds of evidence are genuinely different rows.
    A crew or connection edge comes from an `action`; a root-cause edge comes from a
    `prediction`, since the weather is not an action anyone took. Collapsing them into a single
    `String` ref would have bought brevity at the cost of the foreign keys — and an unverifiable
    `"action:57"` in a text column is exactly the kind of provenance that turns out to be wrong.
    """

    __tablename__ = "disruption_edge"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_group_id: Mapped[int] = mapped_column(
        ForeignKey("incident_group.id"), nullable=False, index=True
    )
    #: `kind:identifier`, the same vocabulary as evidence_refs — event, flight, pairing,
    #: booking, hotel, hotel_pool.
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    #: root_cause | crew | connection | accommodation
    edge_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Crew mechanisms, or missed_connection / overnight_required.
    mechanism: Mapped[str | None] = mapped_column(String(24))
    detail: Mapped[str | None] = mapped_column(Text)
    #: Set for crew, connection and accommodation edges.
    derived_from_action_id: Mapped[int | None] = mapped_column(ForeignKey("action.id"))
    #: Set for root-cause edges: the delay-risk assessment tying this flight to the event.
    #: The weather is not an action anyone took, so it cannot be an action id.
    derived_from_prediction_id: Mapped[int | None] = mapped_column(ForeignKey("prediction.id"))
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Hops from the root event. 1 = directly caused by the disruption.
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    is_at_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "edge_kind IN ('root_cause','crew','connection','accommodation')",
            name="disruption_edge_kind_valid",
        ),
        CheckConstraint("depth >= 0", name="disruption_edge_depth_non_negative"),
        # Exactly one evidence row. Not "at least one": two would make it ambiguous which
        # payload the reviewer should open to check the edge.
        CheckConstraint(
            "(derived_from_action_id IS NULL) <> (derived_from_prediction_id IS NULL)",
            name="disruption_edge_exactly_one_evidence_row",
        ),
        UniqueConstraint(
            "incident_group_id",
            "source_ref",
            "target_ref",
            "edge_kind",
            "mechanism",
            name="disruption_edge_unique",
        ),
    )


class CascadeSnapshot(Base):
    """An append-only record of a rollup computation.

    Derivation stays the source of truth; this records *what was computed when, from which
    actions*. `snapshot_hash` makes a replay checkable: identical inputs reproduce it.

    Deliberately not denormalised onto `incident_group`. A mutable rollup column drifts from
    the rows it summarises, and nothing then says which is right.
    """

    __tablename__ = "cascade_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_group_id: Mapped[int] = mapped_column(
        ForeignKey("incident_group.id"), nullable=False, index=True
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    flights_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passengers_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    connections_at_risk: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_hotels: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crew_pairings_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rooms_required: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_inr: Mapped[int | None] = mapped_column(Integer)

    incidents_in_group: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incidents_assessed_connections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    incidents_assessed_crew: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Exactly which actions fed this snapshot, so the figure is reconstructable.
    source_action_ids: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    payload: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)


class PassengerImpact(Base):
    """A recorded, explainable operational priority for one passenger.

    An index and a band with named factors — not a probability, and not a judgement about
    whose journey matters more. It records who is most *constrained*, which is what makes a
    partial hotel allocation defensible.
    """

    __tablename__ = "passenger_impact"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action.id"), index=True)
    incident_group_id: Mapped[int] = mapped_column(
        ForeignKey("incident_group.id"), nullable=False, index=True
    )
    passenger_id: Mapped[int] = mapped_column(ForeignKey("passenger.id"), nullable=False)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id"), nullable=False)

    priority_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    priority_band: Mapped[str] = mapped_column(String(12), nullable=False)
    factors: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ruleset_hash: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "priority_index >= 0 AND priority_index <= 100",
            name="passenger_impact_index_range",
        ),
        UniqueConstraint(
            "incident_group_id", "passenger_id", "action_id", name="passenger_impact_unique"
        ),
    )


class PlanApproval(Base):
    """A human signature covering a plan's low and medium risk actions — P2-D3.

    It is a separate table rather than a nullable `human_decision.assurance_id` because
    making that column nullable would legalise an action whose `needs_human` references no
    decision at all, which is the Phase 1 invariant.

    `plan_hash` is what keeps the approval honest: a re-planned or reordered plan hashes
    differently, so the signature stops covering it. Without that, "approve the plan" silently
    grows to cover tasks nobody saw.
    """

    __tablename__ = "plan_approval"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id"), nullable=False, index=True)
    #: P2-D1: the operator view is group scoped.
    incident_group_id: Mapped[int | None] = mapped_column(ForeignKey("incident_group.id"))
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The exact task ids covered, each with its risk tier at signing time.
    covered_task_ids: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    gate_config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    gate_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # One signature per plan version. A re-plan produces a new hash and needs a new one.
        UniqueConstraint("plan_id", "plan_hash", name="plan_approval_plan_hash_unique"),
    )


class PlanApprovalTier(Base):
    """The risk tiers a plan approval covers.

    A child table so `high` is excluded by a CHECK constraint. P2-D3's central rule is then a
    database guarantee rather than something application code must remember.
    """

    __tablename__ = "plan_approval_tier"

    plan_approval_id: Mapped[int] = mapped_column(ForeignKey("plan_approval.id"), primary_key=True)
    risk_tier: Mapped[str] = mapped_column(String(8), primary_key=True)

    __table_args__ = (
        CheckConstraint("risk_tier IN ('low','medium')", name="plan_approval_tier_never_high"),
    )


class HotelInventoryHold(Base):
    """Rooms held against an action, append-only.

    Availability is `total_rooms - sum(active holds)` rather than a mutated counter on
    `hotel`. A counter loses updates under concurrency and cannot be replayed; a hold ledger
    does neither.
    """

    __tablename__ = "hotel_inventory_hold"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action.id"), index=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False, index=True)
    incident_group_id: Mapped[int | None] = mapped_column(ForeignKey("incident_group.id"))
    rooms: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    held_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (CheckConstraint("rooms > 0", name="hotel_inventory_hold_rooms_positive"),)
