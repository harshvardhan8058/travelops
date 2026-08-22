"""Group-scope response contracts — STREAM A.

Every shape the console reads for a network disruption. `extra="forbid"` throughout, so a field
the frontend depends on cannot quietly disappear and an undeclared one cannot quietly appear.

The consistent rule across all of these: **a figure is accompanied by what produced it.** A count
with no `measured_by`, no `derived_from`, or no completeness flag is a number a reviewer has to
take on trust, and the entire value of this system is that they do not have to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class GroupRollups(BaseModel):
    """The five headline figures. Every one derived, none stored on the group."""

    model_config = ConfigDict(extra="forbid")

    flights_affected: int
    passengers_affected: int
    connections_at_risk: int
    candidate_hotels: int
    crew_pairings_affected: int


class RollupStatus(BaseModel):
    """Whether the rollup above is a complete answer, and why not when it is not.

    A sibling of `rollups` rather than a member of it, because completeness is a property of the
    computation rather than one of the figures — and because `rollups` is typed as numbers.
    """

    model_config = ConfigDict(extra="forbid")

    is_complete: bool
    computed_at: datetime
    incidents_in_group: int
    incidents_assessed_connections: int
    incidents_assessed_crew: int
    member_flight_ids: list[int] = Field(default_factory=list)
    flights_without_incident: list[int] = Field(default_factory=list)
    membership_is_declared: bool
    note: str


class GroupFlightRow(BaseModel):
    """A declared member flight, with its role and its incident if one is open."""

    model_config = ConfigDict(extra="forbid")

    flight_id: int
    flight_number: str
    route: str
    origin_icao: str
    destination_icao: str
    role: str
    delay_minutes: int
    scheduled_departure_local: str
    incident_id: int | None = None
    incident_reference: str | None = None
    incident_state: str | None = None
    passengers: int = 0


class GroupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    reference: str
    root_cause: str
    airport_icao: str
    severity: str
    state: str
    opened_at: datetime
    rollups: GroupRollups
    rollup_status: RollupStatus
    awaiting_approval_count: int
    provenance: dict[str, Any]


class GroupListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[GroupSummary] = Field(default_factory=list)


class CascadePairingRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_reference: str
    base_icao: str
    source_flight: str
    affected_leg: str
    mechanism: str
    detail: str
    at_risk: bool


class GraphNodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    kind: str
    label: str
    sublabel: str | None = None
    depth: int
    at_risk: bool
    has_evidence: bool
    role: str | None = None


class GraphEdgeOut(BaseModel):
    """An edge, with the recorded row it was read from.

    `derived_from` is `action:N` or `prediction:N` and is never empty. An edge without evidence
    behind it is an assertion, and the database rejects one.
    """

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    target_ref: str
    edge_kind: str
    mechanism: str | None = None
    detail: str | None = None
    depth: int
    derived_from: str


class GraphCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_flight_count: int
    flights_with_evidence: int
    is_complete: bool
    note: str


class CascadeGraphOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_reference: str
    rule_version: str
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)
    edge_counts_by_kind: dict[str, int] = Field(default_factory=dict)
    completeness: GraphCompleteness
    source_action_ids: list[int] = Field(default_factory=list)
    source_prediction_ids: list[int] = Field(default_factory=list)
    snapshot_hash: str


class BlastRadiusDimensionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: int
    unit: str
    measured_by: str
    is_complete: bool
    note: str


class BlastRadiusCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flights_declared: int
    flights_assessed: int
    ratio: str
    is_complete: bool


class BlastRadiusOut(BaseModel):
    """Composition only. There is deliberately no confidence field."""

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    headline: str
    basis: Literal["composed_from_recorded_findings"]
    dimensions: list[BlastRadiusDimensionOut] = Field(default_factory=list)
    completeness: BlastRadiusCompleteness
    gaps: list[str] = Field(default_factory=list)


class GroupDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    reference: str
    root_cause: str
    airport_icao: str
    severity: str
    state: str
    opened_at: datetime
    rollups: GroupRollups
    rollup_status: RollupStatus
    flights: list[GroupFlightRow] = Field(default_factory=list)
    crew_pairings: list[CascadePairingRow] = Field(default_factory=list)
    mechanism_legend: dict[str, str] = Field(default_factory=dict)
    why_nine_not_eight: str
    graph: CascadeGraphOut
    blast_radius: BlastRadiusOut
    provenance: dict[str, Any]


# ------------------------------------------------------------------------------ run


class IncidentProgressOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_id: int
    flight_number: str
    role: str
    incident_id: int
    incident_reference: str
    state: str
    steps_taken: int
    is_terminal: bool
    note: str | None = None
    plan_id: int | None = None
    plan_hash: str | None = None
    awaiting_evaluation_ids: list[int] = Field(default_factory=list)


class GroupRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_reference: str
    is_terminal: bool
    states: dict[str, int] = Field(default_factory=dict)
    note: str
    incidents: list[IncidentProgressOut] = Field(default_factory=list)
    rollups: GroupRollups
    rollup_status: RollupStatus
    snapshot_hash: str | None = None
    edges_recorded: int = 0
    replayed: bool = False
    idempotency_key: str | None = None


# ------------------------------------------------------------------- plan assurance


class PlanCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    state: str
    reason_code: str
    reason: str | None = None
    tier: str | None = None
    offending_refs: list[str] = Field(default_factory=list)
    is_blocking: bool


class PlanAssuranceOut(BaseModel):
    """A plan-level summary that authorises nothing.

    `authorises_no_action` is a `Literal[True]` so the boundary is in the type. Every task still
    passes the action gate at execution, and a high-risk task always needs its own decision.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str
    plan_risk_tier: str
    plan_id: int | None = None
    plan_hash: str
    group_reference: str
    task_count: int
    admissible: bool
    requires_human: bool
    authorises_no_action: Literal[True]
    checks: list[PlanCheckOut] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    exposure: dict[str, Any] = Field(default_factory=dict)
    config_version: str
    config_hash: str
    evaluated_at: str | None = None
    tasks_needing_own_decision: list[str] = Field(default_factory=list)
    note: str
    incident_reference: str | None = None
    incident_id: int | None = None
    approval: PlanApprovalOut | None = None


class PlanAssuranceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_reference: str
    plans: list[PlanAssuranceOut] = Field(default_factory=list)
    config_version: str
    config_hash: str
    note: str


class PlanApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    actor_id: str = "operator-1"


class PlanApprovalOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    plan_id: int
    plan_hash: str
    covered_task_ids: list[str] = Field(default_factory=list)
    covers_tiers: list[str] = Field(default_factory=list)
    actor_id: str
    reason: str | None = None
    decided_at: datetime
    gate_config_version: str
    gate_config_hash: str
    tasks_needing_own_decision: list[str] = Field(default_factory=list)
    note: str


# -------------------------------------------------------------------------- what-if


class WhatIfRequest(BaseModel):
    """Only declared levers. An undeclared key is refused by name rather than ignored."""

    model_config = ConfigDict(extra="forbid")

    levers: dict[str, Any] = Field(default_factory=dict)


class ScenarioDeltaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    baseline: int
    scenario: int
    delta: int
    summary: str


class LeverRejectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lever: str
    reason: str | None = None


class WhatIfResponse(BaseModel):
    """Bounded zero-write re-evaluation. `wrote_rows` is a `Literal[False]`."""

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    rule_version: str
    basis: Literal["recorded_evidence"]
    wrote_rows: Literal[False]
    boundary_note: str
    headline: str
    levers_applied: dict[str, Any] = Field(default_factory=dict)
    levers_available: dict[str, str] = Field(default_factory=dict)
    levers_rejected: list[LeverRejectionOut] = Field(default_factory=list)
    recorded_baseline: dict[str, int] = Field(default_factory=dict)
    deltas: list[ScenarioDeltaOut] = Field(default_factory=list)
    guard: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- replay


class ReplayFrame(BaseModel):
    """One immutable record, in the order it was written.

    Replay is a fold over `decision_log`, not a re-run. Nothing is recomputed, so a replay
    cannot produce a figure the original run did not.
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    id: int
    incident_id: int | None = None
    incident_reference: str | None = None
    occurred_at: datetime
    stage: str
    actor: str
    actor_kind: str
    event_type: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class ReplayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_reference: str
    frame_count: int
    frames: list[ReplayFrame] = Field(default_factory=list)
    note: str


PlanAssuranceOut.model_rebuild()
