"""Typed events.

Every event carries event_id, schema_version, correlation_id, causation_id, incident_id,
occurred_at and producer. Consumers are idempotent by `event_id`.

Owner: Stream A. Other streams publish these; they do not add fields without agreement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActionStatus,
    AssuranceDecision,
    HumanDecisionType,
    RiskLevel,
    TriggerType,
)

SCHEMA_VERSION = "1"


class EventType(StrEnum):
    weather_observed = "WEATHER_OBSERVED"
    high_risk_delay = "HIGH_RISK_DELAY"
    incident_opened = "INCIDENT_OPENED"
    plan_proposed = "PLAN_PROPOSED"
    assurance_evaluated = "ASSURANCE_EVALUATED"
    action_completed = "ACTION_COMPLETED"
    human_decision_recorded = "HUMAN_DECISION_RECORDED"
    incident_resolved = "INCIDENT_RESOLVED"
    recovery_blocked = "RECOVERY_BLOCKED"


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    schema_version: str = SCHEMA_VERSION
    occurred_at: datetime = Field(default_factory=_now)
    producer: str

    correlation_id: str | None = None
    causation_id: str | None = None
    incident_id: int | None = None
    incident_group_id: int | None = None


class WeatherObserved(EventBase):
    event_type: Literal[EventType.weather_observed] = EventType.weather_observed
    airport_icao: str
    wind_speed_kt: int | None = None
    visibility_m: int | None = None
    ceiling_ft: int | None = None
    provenance: dict[str, Any]


class HighRiskDelay(EventBase):
    event_type: Literal[EventType.high_risk_delay] = EventType.high_risk_delay
    flight_id: int
    risk_index: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    rule_version: str
    factors: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class IncidentOpened(EventBase):
    event_type: Literal[EventType.incident_opened] = EventType.incident_opened
    incident_reference: str
    flight_id: int
    trigger_type: TriggerType
    affected_entity_refs: list[str] = Field(default_factory=list)


class PlanProposed(EventBase):
    event_type: Literal[EventType.plan_proposed] = EventType.plan_proposed
    plan_id: int
    generator: str
    prompt_version: str | None = None
    task_ids: list[int] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class AssuranceEvaluated(EventBase):
    event_type: Literal[EventType.assurance_evaluated] = EventType.assurance_evaluated
    evaluation_id: int
    plan_task_id: int
    decision: AssuranceDecision
    risk_tier: str
    check_results: dict[str, Any]
    blocking_reasons: list[str] = Field(default_factory=list)
    config_version: str
    config_hash: str


class ActionCompleted(EventBase):
    event_type: Literal[EventType.action_completed] = EventType.action_completed
    action_id: int
    plan_task_id: int
    status: ActionStatus
    actor: str
    cost_inr: int | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class HumanDecisionRecorded(EventBase):
    event_type: Literal[EventType.human_decision_recorded] = EventType.human_decision_recorded
    evaluation_id: int
    decision: HumanDecisionType
    # Pseudonymous operator identifier. No personal identity is published.
    actor_id: str
    reason: str


class IncidentResolved(EventBase):
    event_type: Literal[EventType.incident_resolved] = EventType.incident_resolved
    incident_reference: str
    # Derived from recorded values only.
    outcome_metrics: dict[str, Any] = Field(default_factory=dict)


class RecoveryBlocked(EventBase):
    event_type: Literal[EventType.recovery_blocked] = EventType.recovery_blocked
    incident_reference: str
    blocking_reasons: list[str] = Field(default_factory=list)


DomainEvent = Annotated[
    WeatherObserved
    | HighRiskDelay
    | IncidentOpened
    | PlanProposed
    | AssuranceEvaluated
    | ActionCompleted
    | HumanDecisionRecorded
    | IncidentResolved
    | RecoveryBlocked,
    Field(discriminator="event_type"),
]

STREAM_NAME = "travelops.events"
