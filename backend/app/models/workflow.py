"""Incidents, plans, assurance evaluations, human decisions, actions and the audit log.

Two invariants are enforced structurally rather than by convention:

1. An `action` cannot exist without the `assurance_evaluation` that authorised it.
2. When that evaluation decided `needs_human`, the action must also reference an
   `approved` `human_decision` for the same evaluation.

The second is a cross-row rule, so it is enforced in the service transaction and covered
by an integration test — see tests/unit/test_workflow_invariants.py.

Owner: Stream C (schema) / Stream A (transitions) / Stream B (assurance rows).
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    ActionStatus,
    AssuranceDecision,
    HumanDecisionType,
    IncidentState,
    RiskLevel,
    RiskTier,
    TaskState,
    TriggerType,
)

# JSONB on Postgres; plain JSON elsewhere so unit tests can run on SQLite.
JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")

# Partial-index predicate for "one active incident per flight" (FR-6).
_ACTIVE_INCIDENT_PREDICATE = text("state NOT IN ('resolved','blocked','failed')")


class Prediction(Base):
    """Deterministic delay-risk output.

    Deliberately stores a risk INDEX and LEVEL, not a probability. Nothing here is
    calibrated against observed outcomes, so calling it a probability would be unearned.
    """

    __tablename__ = "prediction"

    id: Mapped[int] = mapped_column(primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id"), nullable=False)
    airport_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    risk_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(String(12), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    factors: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)

    __table_args__ = (
        CheckConstraint("risk_index >= 0 AND risk_index <= 100", name="prediction_index_range"),
    )


class IncidentGroup(Base):
    """One root cause owning many flight incidents (the cascade)."""

    __tablename__ = "incident_group"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    root_cause: Mapped[TriggerType] = mapped_column(String(20), nullable=False)
    airport_icao: Mapped[str] = mapped_column(ForeignKey("airport.icao_code"), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    state: Mapped[IncidentState] = mapped_column(
        String(20), nullable=False, default=IncidentState.detected
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    demo_dataset_id: Mapped[str | None] = mapped_column(String(32), index=True)

    incidents: Mapped[list[Incident]] = relationship(back_populates="group")

    __table_args__ = (
        CheckConstraint(
            "state IN ('detected','assessing','planning','assuring','awaiting_approval',"
            "'executing','resolved','blocked','failed')",
            name="incident_group_state_valid",
        ),
    )


class Incident(Base):
    __tablename__ = "incident"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("incident_group.id"), index=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flight.id"), nullable=False)
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("prediction.id"))

    trigger_type: Mapped[TriggerType] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    state: Mapped[IncidentState] = mapped_column(
        String(20), nullable=False, default=IncidentState.detected
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    demo_dataset_id: Mapped[str | None] = mapped_column(String(32), index=True)

    group: Mapped[IncidentGroup | None] = relationship(back_populates="incidents")
    plans: Mapped[list[Plan]] = relationship(back_populates="incident")

    __table_args__ = (
        CheckConstraint(
            "state IN ('detected','assessing','planning','assuring','awaiting_approval',"
            "'executing','resolved','blocked','failed')",
            name="incident_state_valid",
        ),
        # FR-6 in the database: a 60-second weather poll cannot open 60 incidents an hour.
        # `awaiting_approval` stays active and resumes the same incident; `blocked` is
        # terminal and releases the slot only once explicitly closed.
        Index(
            "uq_incident_active_per_flight",
            "flight_id",
            unique=True,
            postgresql_where=_ACTIVE_INCIDENT_PREDICATE,
            sqlite_where=_ACTIVE_INCIDENT_PREDICATE,
        ),
    )


class Plan(Base):
    __tablename__ = "plan"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident.id"), nullable=False, index=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # 'groq:llama-3.3-70b' | 'fallback-playbook'. Surfaced in the UI, never ambiguous.
    generator: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    # Diagnostic only. NEVER an execution gate. Recorded so we can compare a model's
    # self-assessment against what the deterministic gate actually decided.
    model_self_report: Mapped[int | None] = mapped_column(SmallInteger)

    rationale: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict | None] = mapped_column(JSON_TYPE)
    retrieved_incident_ids: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)

    incident: Mapped[Incident] = relationship(back_populates="plans")
    tasks: Mapped[list[PlanTask]] = relationship(back_populates="plan", order_by="PlanTask.task_order")


class PlanTask(Base):
    __tablename__ = "plan_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    task_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    depends_on: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    target_refs: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    inputs: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    state: Mapped[TaskState] = mapped_column(String(16), nullable=False, default=TaskState.pending)

    plan: Mapped[Plan] = relationship(back_populates="tasks")


class AssuranceEvaluation(Base):
    """Immutable authorisation record. One per proposed task evaluation.

    Never updated. A corrected decision requires a new evaluation.
    """

    __tablename__ = "assurance_evaluation"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_task_id: Mapped[int] = mapped_column(ForeignKey("plan_task.id"), nullable=False, index=True)
    decision: Mapped[AssuranceDecision] = mapped_column(String(20), nullable=False)
    risk_tier: Mapped[RiskTier] = mapped_column(String(8), nullable=False)

    # All six checks with PASS | WARN | FAIL plus reason codes.
    check_results: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    blocking_reasons: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)

    # Replay must use the semantics that applied at decision time.
    config_version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    human_decision: Mapped[HumanDecision | None] = relationship(
        back_populates="evaluation", uselist=False
    )


class HumanDecision(Base):
    """Append-only operator decision, unique per blocked evaluation."""

    __tablename__ = "human_decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    assurance_id: Mapped[int] = mapped_column(
        ForeignKey("assurance_evaluation.id"), nullable=False, unique=True
    )
    decision: Mapped[HumanDecisionType] = mapped_column(String(12), nullable=False)
    # Pseudonymous. No personal identity is stored for a demo operator.
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    evaluation: Mapped[AssuranceEvaluation] = relationship(back_populates="human_decision")

    __table_args__ = (
        CheckConstraint("decision IN ('approved','rejected')", name="human_decision_valid"),
    )


class Action(Base):
    __tablename__ = "action"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_task_id: Mapped[int] = mapped_column(ForeignKey("plan_task.id"), nullable=False, index=True)
    assurance_id: Mapped[int] = mapped_column(
        ForeignKey("assurance_evaluation.id"), nullable=False
    )
    # Required when the gate decided needs_human. Enforced in the service transaction.
    human_decision_id: Mapped[int | None] = mapped_column(ForeignKey("human_decision.id"))

    actor: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[ActionStatus] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    cost_inr: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict | None] = mapped_column(JSON_TYPE)
    provenance_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="simulated")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionLog(Base):
    """Append-only chronology. Powers the timeline rail and replay."""

    __tablename__ = "decision_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incident.id"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    # orchestrator | agent | service | human | provider
    actor: Mapped[str] = mapped_column(String(48), nullable=False)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSON_TYPE)
    correlation_id: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_decision_log_incident_occurred", "incident_id", "occurred_at"),)


class IncidentOutcome(Base):
    __tablename__ = "incident_outcome"

    incident_id: Mapped[int] = mapped_column(ForeignKey("incident.id"), primary_key=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    passengers_affected: Mapped[int | None] = mapped_column(Integer)
    passengers_reaccommodated: Mapped[int | None] = mapped_column(Integer)
    connections_protected: Mapped[int | None] = mapped_column(Integer)
    total_cost_inr: Mapped[int | None] = mapped_column(Integer)
    resolution_minutes: Mapped[int | None] = mapped_column(Integer)
    operator_rating: Mapped[int | None] = mapped_column(SmallInteger)
    operator_notes: Mapped[str | None] = mapped_column(Text)


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action.id"))
    passenger_id: Mapped[int] = mapped_column(ForeignKey("passenger.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    # This column keeps the demo honest: three real emails and 177 simulated is fine,
    # implying all 180 were delivered is not.
    delivery_mode: Mapped[str] = mapped_column(String(12), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HotelReservation(Base):
    __tablename__ = "hotel_reservation"

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("action.id"))
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False)
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("booking.id"))
    rooms: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    nights: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    rate_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("action_id", "hotel_id", "booking_id", name="hotel_reservation_unique"),
    )
