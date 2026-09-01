"""Contracts for the demo control surface — Phase 5, Stream A.

These exist so a demonstration needs a browser and nothing else. Every capability here already
existed behind `python -m app.cli`; what was missing was a way to *see* and *use* it without a
terminal, which is the difference between a product and a set of developer tools.

Three deliberate boundaries.

## A simulation is a selection, not an invention

`SimulationDefinitionOut` does not carry delays, weather or passenger counts it made up. It names a
reproducible **selection over the recorded dataset**, resolved against real `flight` rows at request
time, and the members it publishes carry each flight's **recorded** delay. That is forced rather
than chosen: `POST /scenarios` refuses a declared delay that disagrees with the recorded one, and
that refusal is correct — a scenario builder that could assert its own operational facts would be
inventing the disruption it claims to be reacting to.

So a simulation feeds the **existing** scenario lifecycle. There is no second lifecycle, no second
orchestrator and no simulation engine: a simulation is POSTed to `/scenarios` and started through
`/scenarios/{ref}/start`, landing in `IncidentGroup` exactly as the Scenario Builder does.
Everything downstream — evidence, planner, assurance, approval, execution, passenger impact,
replay — is the one code path it has always been.

## `runnable` is a fact, not an aspiration

A definition whose flights the dataset does not contain comes back `runnable: false` with the reason
named. It is still listed, because hiding it would leave an operator wondering where the third
simulation went; it simply cannot be started, and says why.

## Reset states what it will destroy before it does it

`DemoResetRequest` requires the caller to repeat a confirmation phrase, and the response reports the
counts actually deleted and re-seeded. `GET /demo/dataset` is the read-only half: it answers "what
is in the database right now", so the destructive control is never the only way to find out.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TriggerType
from app.schemas.cascade import ProvenanceBlock
from app.schemas.scenarios import ScenarioMemberRole

#: The phrase `POST /demo/reset` requires. Spelled out so a mis-click cannot satisfy it and so the
#: UI can render exactly what the operator is about to type.
RESET_CONFIRMATION = "reset demo data"


class DatasetTableOut(BaseModel):
    """One table and the number of rows actually in it."""

    model_config = ConfigDict(extra="forbid")

    table: str
    rows: int


class DemoDatasetResponse(BaseModel):
    """What is in the database right now, read back rather than assumed.

    `is_seeded` is derived from the reference tables a demo cannot run without. It is not a stored
    flag: a flag would go stale the moment somebody truncated a table, and the whole point of this
    surface is to tell an operator the truth about the current state.
    """

    model_config = ConfigDict(extra="forbid")

    is_seeded: bool
    #: Row counts for the seeded reference tables, in seed order.
    tables: list[DatasetTableOut] = Field(default_factory=list)
    #: Headline figures an operator recognises, lifted from `tables` for convenience.
    flights: int
    bookings: int
    booking_segments: int
    airports: int
    #: Groups and incidents currently open on top of the dataset. Distinct from the reference rows
    #: above: these are the workflow's own output and a reset removes them.
    incident_groups: int
    incidents: int
    #: The most recently started cascade, when one exists. Null is a legitimate answer.
    current_group_reference: str | None = None
    #: Whether destructive demo controls are permitted in this environment.
    #:
    #: True in every environment this build can legally run in, because `AppEnv` contains only
    #: `development`, `demo` and `test` — an unrecognised `APP_ENV` is refused when `Settings` is
    #: constructed, so the process does not boot at all. Published rather than assumed so the
    #: console renders a real answer, and so the field already exists if a stricter environment is
    #: ever added.
    reset_allowed: bool
    app_env: str
    note: str


class SimulationMemberOut(BaseModel):
    """One flight a simulation would declare, with the delay the dataset records for it."""

    model_config = ConfigDict(extra="forbid")

    flight_id: int
    flight_number: str
    role: ScenarioMemberRole
    origin_icao: str
    destination_icao: str
    #: The RECORDED delay. `POST /scenarios` refuses any other value, which is what keeps this
    #: surface from inventing the disruption.
    delay_minutes: int


class SimulationDefinitionOut(BaseModel):
    """A named, reproducible selection over the recorded dataset.

    Everything needed to POST it to the existing scenario lifecycle is here, so the console composes
    no operational facts of its own.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    summary: str
    root_cause: TriggerType
    airport_icao: str
    severity: str
    #: The instant this simulation must be declared at — the recorded scenario clock, NOT now.
    #:
    #: This is published rather than left to the caller because the wall clock is the wrong answer
    #: and the console has no way to know it. Every piece of evidence in the demo dataset is a
    #: fixed-seed snapshot: the METAR for VOBL is recorded against the scenario's own date. An
    #: incident opened at the current time is therefore evaluated against an observation that is
    #: however many days old this machine happens to be, `sources_fresh` FAILs with `SOURCE_STALE`,
    #: and the gate refuses the action for an EVIDENCE reason.
    #:
    #: That refusal is not approvable by anyone — `enforce_action_approval` answers 409
    #: `NOT_APPROVABLE_EVIDENCE`, because approval covers risk and never failed evidence — so the
    #: cascade parks on a hold no operator can clear. Measured before this field existed: a
    #: browser-started simulation reported `metar:VOBL 15159m old, max 60m` and deadlocked.
    #:
    #: The value is read from the seeded `incident_group.opened_at`, which is the same row
    #: `app.cli._inject` reads for exactly this reason. One value, read from the database, rather
    #: than a second copy of the scenario clock that can drift.
    effective_at: datetime
    #: Resolved members, primary first. Empty when the dataset cannot support this definition.
    members: list[SimulationMemberOut] = Field(default_factory=list)
    #: Passengers booked on the declared flights, counted from `booking_segment`. Null when no
    #: bookings are recorded — "no records" and "nobody affected" are different claims.
    passengers_affected: int | None = None
    #: False when the dataset cannot support this definition. `blocked_reason` then says why.
    runnable: bool
    blocked_reason: str | None = None
    #: Simulated by construction: a selection over a synthetic dataset is not a live observation.
    provenance: ProvenanceBlock


class DemoSimulationsResponse(BaseModel):
    """The simulation catalogue, resolved against the dataset as it is right now."""

    model_config = ConfigDict(extra="forbid")

    catalogue_version: str
    simulations: list[SimulationDefinitionOut] = Field(default_factory=list)
    runnable_count: int
    #: Type-level statement that these are selections over recorded rows, never generated facts.
    basis: Literal["recorded_dataset_selection"] = "recorded_dataset_selection"
    note: str


class DemoResetRequest(BaseModel):
    """Explicit confirmation. Nothing else, because nothing else is a choice.

    Reset restores the dataset and stops. It deliberately does **not** also open a cascade: opening
    is a separate operation the console already offers, and a control that quietly did two things
    would make "what will this do?" unanswerable from the button.
    """

    model_config = ConfigDict(extra="forbid")

    #: Must equal `RESET_CONFIRMATION`. A typed phrase rather than a boolean, so the control cannot
    #: be satisfied by a stray click or by a request body replayed from a log.
    confirm: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(default="operator-1", min_length=1, max_length=64)


class DemoResetResponse(BaseModel):
    """What the reset actually did, counted from the rows it touched.

    Counts are read back from the reports the seed layer returns, not predicted. A control that
    claimed to have removed rows it did not is worse than no control.
    """

    model_config = ConfigDict(extra="forbid")

    #: Workflow rows removed before the re-seed, by table. The orchestrator's output, not seed data.
    workflow_removed: dict[str, int] = Field(default_factory=dict)
    #: Reference rows written by the re-seed, by table.
    seeded: dict[str, int] = Field(default_factory=dict)
    #: The dataset digest, which is what makes "byte-identical across runs" checkable.
    dataset_digest: str
    #: The seeded cascade now available to open. Declared, not opened: after a reset no incident
    #: exists, which is why `/incident-groups/current` correctly reports nothing in progress.
    seeded_group_reference: str | None = None
    performed_by: str
    performed_at: datetime
    note: str
