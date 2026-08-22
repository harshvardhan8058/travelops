"""Response contracts for the disruption group surface.

Every model declares `extra="forbid"` and every endpoint declares a `response_model`, so a
field the console expects and the API omits is a hard failure rather than a silent `undefined`.

The shapes stay byte-compatible with `fixtures/api/incident_groups.json` and
`incident_group_detail.json`, because Stream D renders those directly and the console must not
have to change when a route stops being fixture-backed. New blocks are additive.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IncidentState


class ProvenanceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    provider: str
    source_ref: str


class RollupStatus(BaseModel):
    """Whether the rollup describes the whole group or only part of it.

    `is_complete` is false whenever a declared member flight has no incident or an incident has
    not been assessed. A partial rollup must render as partial: eight flights' worth of caption
    over six flights' worth of evidence is the failure mode this field exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    is_complete: bool
    computed_at: datetime | None = None
    note: str
    #: Declared member flights with no incident open, named rather than counted.
    flights_without_incident: list[int] = Field(default_factory=list)
    membership_is_declared: bool = True


class GroupRollups(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flights_affected: int
    passengers_affected: int
    connections_at_risk: int
    candidate_hotels: int | None = None
    crew_pairings_affected: int
    note: str | None = None


class GroupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    reference: str
    root_cause: str
    airport_icao: str
    severity: str
    state: IncidentState
    opened_at: datetime
    rollups: GroupRollups
    awaiting_approval_count: int
    provenance: ProvenanceBlock
    rollup_status: RollupStatus


class IncidentGroupListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_by: str = "stream-a"
    groups: list[GroupSummary] = Field(default_factory=list)


class GroupFlightOut(BaseModel):
    """One member flight.

    `incident_reference` is nullable on purpose: a flight the group declares but has no incident
    for yet is "affected, not yet being worked", which is meaningful and different from missing.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    flight_number: str
    route: str
    delay_minutes: int
    passengers: int
    state: str
    role: str
    incident_reference: str | None = None


class CascadePairingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_reference: str
    base_icao: str | None = None
    source_flight: str | None = None
    affected_leg: str | None = None
    mechanism: str
    detail: str | None = None
    at_risk: bool = True
    depth: int = 1


class BlastRadiusDimensionOut(BaseModel):
    """One measured dimension, with the service that measured it named.

    Mirrors `services/blast_radius.BlastRadiusDimension` field for field. Stream A types the
    boundary; it does not recompute a single value. `is_complete=False` makes a value a floor,
    which is why `measured_by` travels with it — a floor from a named source is evidence, a
    floor from nowhere is a guess.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: int
    unit: str
    measured_by: str
    is_complete: bool
    note: str = ""


class BlastRadiusCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flights_declared: int
    flights_assessed: int
    ratio: str
    is_complete: bool


class BlastRadiusOut(BaseModel):
    """Reach composed from recorded findings.

    `basis` is a `Literal` in Stream C's model and stays one here, so a `confidence` field
    cannot be slipped in without changing the type and forcing the conversation. `headline`
    deliberately carries its own caveat inside one string, so a UI cannot render the totals and
    drop the qualification.
    """

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    headline: str
    basis: Literal["composed_from_recorded_findings"] = "composed_from_recorded_findings"
    dimensions: list[BlastRadiusDimensionOut] = Field(default_factory=list)
    completeness: BlastRadiusCompleteness
    #: Named, countable gaps. Never a score.
    gaps: list[str] = Field(default_factory=list)


class GraphNodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    kind: str
    label: str
    sublabel: str | None = None
    depth: int = 1
    at_risk: bool = True
    #: False when the node is declared but no service has looked at it yet. Rendered as a gap,
    #: never quietly dropped: a cascade that hides its unworked flights looks finished.
    has_evidence: bool = True
    role: str | None = None


class GraphEdgeOut(BaseModel):
    """One edge, naming the recorded row it came from.

    An edge with no provenance would be an assertion rather than evidence, which is why Stream
    C enforces it with an exclusive-arc CHECK: a root-cause edge cites a `prediction`, because
    the weather is not an action anyone took.
    """

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    target_ref: str
    edge_kind: str
    mechanism: str | None = None
    detail: str | None = None
    depth: int = 1
    is_at_risk: bool = True
    derived_from_action_id: int | None = None
    derived_from_prediction_id: int | None = None


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


class IncidentGroupDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_by: str = "stream-a"
    note: str
    id: int
    reference: str
    root_cause: str
    airport_icao: str
    severity: str
    state: IncidentState
    opened_at: datetime
    rollups: GroupRollups
    rollup_status: RollupStatus
    flights: list[GroupFlightOut] = Field(default_factory=list)
    crew_pairings: list[CascadePairingOut] = Field(default_factory=list)
    mechanism_legend: dict[str, str] = Field(default_factory=dict)
    why_nine_not_eight: str
    blast_radius: BlastRadiusOut | None = None
    graph: CascadeGraphOut | None = None
    awaiting_approval_count: int = 0
    provenance: ProvenanceBlock


class GroupMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_id: int
    flight_number: str
    role: str
    incident_id: int | None = None
    incident_reference: str | None = None
    state: str | None = None
    note: str | None = None


class GroupRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_reference: str
    state: IncidentState
    members: list[GroupMemberOut] = Field(default_factory=list)
    opened_incident_ids: list[int] = Field(default_factory=list)
    blocked_reason: str | None = None
    awaiting_approval_count: int = 0
    replayed: bool = False


class WhatIfLeverRejectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lever: str
    reason: str


class WhatIfDeltaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    baseline: int
    scenario: int
    delta: int
    summary: str


class WhatIfResponse(BaseModel):
    """A bounded, zero-write, deterministic re-evaluation — P2-D2.

    `basis` and `wrote_rows` are `Literal`s, so this contract **cannot express a projection and
    cannot claim a write**. The figures describe what the recorded evidence implies under
    altered levers; they are not a forecast, and `boundary_note` says so in the payload rather
    than only in a document.
    """

    model_config = ConfigDict(extra="forbid")

    group_reference: str
    rule_version: str
    basis: Literal["recorded_evidence"] = "recorded_evidence"
    wrote_rows: Literal[False] = False
    boundary_note: str
    headline: str
    permitted: bool = True
    refusals: list[str] = Field(default_factory=list)
    seed: int | None = None
    recorded_baseline: dict[str, Any] = Field(default_factory=dict)
    levers_applied: dict[str, Any] = Field(default_factory=dict)
    levers_available: list[str] = Field(default_factory=list)
    levers_rejected: list[WhatIfLeverRejectionOut] = Field(default_factory=list)
    deltas: list[WhatIfDeltaOut] = Field(default_factory=list)
