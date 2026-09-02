"""Incident response contracts — STREAM A.

These shapes are contractual. `fixtures/api/incident_detail.json` and
`fixtures/api/timeline.json` (Stream C) and `frontend/src/api/types.ts` (Stream D) both
depend on them, so the field names here match those files rather than whatever would have
been most convenient to generate.

Two rules govern what goes in a response:

1. **A number is present only if it was computed from records.** An absent key in
   `affected_entities` means "not yet derived", which is why it is absent rather than `0`.
   Zero would read as "nothing affected", and inventing a total is the one thing
   `docs/25-evaluation-readiness.md` lists as a non-negotiable failure condition.
2. **Nullable means "not recorded yet", not "none".** `evidence.risk` is null before a
   Prediction exists; it is never a fabricated index.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IncidentState, RiskLevel, TaskState
from app.assurance.authorship import Authorship
from app.schemas.provenance import Provenance


class FlightSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    flight_number: str
    route: str
    scheduled_departure: datetime
    estimated_departure: datetime | None = None
    delay_minutes: int
    block_time_minutes: int
    #: Omitted when no booking records exist for this flight, never reported as 0.
    passengers: int | None = None


class RiskFactor(BaseModel):
    """One named contributor to the risk index.

    `value` is the observed figure where the rule recorded one, so a reader can check the
    factor against the observation instead of taking the label on trust. `points` shows the
    contribution, which is what makes an index of 80 explainable rather than asserted.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str
    detail: str | None = None
    points: int | None = None
    threshold: str | None = None
    runway: str | None = None


class RiskEvidence(BaseModel):
    """A deterministic index and band. Never a calibrated probability."""

    model_config = ConfigDict(extra="forbid")

    risk_index: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    rule_version: str
    factors: list[RiskFactor] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class WeatherEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airport_icao: str
    observed_at: datetime
    wind_speed_kt: int | None = None
    wind_direction_deg: int | None = None
    visibility_m: int | None = None
    ceiling_ft: int | None = None
    precipitation: str | None = None
    provenance: Provenance


class IncidentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Null until the Delay Risk service has recorded a Prediction.
    risk: RiskEvidence | None = None
    #: Null until a weather observation exists for the origin airport.
    weather: WeatherEvidence | None = None
    #: Only keys derived from records. An absent key means "not computed", not zero.
    affected_entities: dict[str, int] = Field(default_factory=dict)
    #: Populated by SQL-retrieved precedent, which is Stream C's memory layer.
    retrieved_precedent: dict[str, Any] | None = None


class StateRailEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: IncidentState
    reached_at: datetime | None = None


class PlanTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    task_order: int
    action_type: str
    state: TaskState
    depends_on: list[str] = Field(default_factory=list)
    #: The evaluation that authorised this task, or null if it has not been assured.
    assurance_id: int | None = None


class PlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    #: 'fallback-playbook' or 'groq:llama-3.3-70b'. Never ambiguous, so a judge never has to
    #: guess whether a model was involved.
    generator: str
    #: Who wrote it, decided by the server. Published because the console used to re-derive this
    #: by string-matching `generator` in the browser, and returned "unclassified" for a plan that
    #: was plainly the deterministic playbook.
    authored_by: Authorship
    prompt_version: str | None = None
    #: Diagnostic metadata only. Never used for control flow.
    model_self_report: int | None = None
    generated_at: datetime
    rationale: str | None = None
    #: 'selected' when a person chose this plan; 'candidate' when it is the plan of record only
    #: because it is the earliest one. The two are very different claims about who decided.
    selection_state: str
    #: A model-authored plan exists on this incident and is NOT the plan of record.
    #:
    #: This is the fact the demo most needs stated. The deterministic playbook is persisted first
    #: and unconditionally, the planner agent's output is stored as a candidate, and nothing
    #: auto-selects it — so a fully successful live model call still leaves the playbook running.
    #: Without this field the console can show "LLM live" beside a model-authored candidate and
    #: leave a viewer to conclude the model planned the recovery, which it did not.
    model_candidate_available: bool = False
    tasks: list[PlanTaskSummary] = Field(default_factory=list)


class ActionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    plan_task_id: int
    action_type: str
    #: Never null: an action cannot exist without the evaluation that authorised it.
    assurance_id: int
    #: Set when the gate required a human and one approved.
    human_decision_id: int | None = None
    actor: str
    status: str
    reason: str
    cost_inr: int | None = None
    provenance_kind: str
    executed_at: datetime | None = None
    idempotency_key: str
    #: Promoted from `action.payload["reason_code"]`, where dispatch and the services already
    #: record it. A short bounded token, which is exactly what a list view needs — the console
    #: was prefix-matching `reason` prose only because the structured value was buried.
    reason_code: str | None = None
    #: `action` or `plan` when a human authorised this. Both are a person's act; an auditor
    #: still has to tell a per-action signature from a plan-wide one.
    decision_scope: str | None = None
    plan_approval_id: int | None = None


class ActionDetailResponse(ActionSummary):
    """One action with the payload the services recorded.

    A separate endpoint rather than a `payload` field on `ActionSummary`, for two reasons.
    `ActionSummary` appears in list responses, so inlining an unbounded service dict would make
    every incident detail carry every service's internal structure. And `action.payload` is
    service-shaped and unversioned — exposing it verbatim on the list contract would silently
    promote Stream C's private dicts to public API. `payload_schema_version` makes a future
    refactor detectable rather than mysterious.
    """

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] = Field(default_factory=dict)
    payload_schema_version: int = 1
    incident_reference: str


class IncidentDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    reference: str
    group_reference: str | None = None
    flight: FlightSummary
    trigger_type: str
    severity: str
    state: IncidentState
    opened_at: datetime
    closed_at: datetime | None = None
    state_rail: list[StateRailEntry] = Field(default_factory=list)
    evidence: IncidentEvidence
    #: Null before the orchestrator has proposed a plan.
    plan: PlanSummary | None = None
    actions: list[ActionSummary] = Field(default_factory=list)
    provenance: Provenance


class TimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    occurred_at: datetime
    stage: str
    actor: str
    #: Derived from `actor` so the UI can group without string matching.
    actor_kind: str
    event_type: str
    summary: str
    detail: dict[str, Any] | None = None
    correlation_id: str | None = None


class TimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    entries: list[TimelineEntry] = Field(default_factory=list)


class RunResponse(BaseModel):
    """Result of driving the workflow forward.

    `replayed` is true when the Idempotency-Key had already been used, in which case the
    recorded result is returned and the workflow was not advanced again.
    """

    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    state: IncidentState
    previous_state: IncidentState
    steps_taken: int
    is_terminal: bool
    #: Why the run stopped, when it stopped short of a terminal state.
    note: str | None = None
    replayed: bool = False
    idempotency_key: str | None = None
