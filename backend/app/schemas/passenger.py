"""The passenger-facing projection of a disruption — Phase 5, Stream A.

This is the same recorded state the operator console reads, addressed by booking reference and
narrowed to what one passenger is entitled to see. It is a **projection, not a second source of
truth**: every field traces to a row somebody wrote, and nothing here is computed for the screen.

Four rules this contract enforces in its own shape, because the reader is the person least able to
challenge what it says.

## 1. No personal data

The only identifiers are the PNR the passenger already holds and the synthetic
`passenger_reference` (`PAX-00001`). `Passenger.full_name`, `.email` and `.phone` exist in the
database and are deliberately **not** in this contract — there is no field to put them in, which is
a stronger guarantee than remembering not to fill one. That follows the convention the impact
surface already set: "An id is not a person."

## 2. No money, and no legal claim

There is no compensation field. Entitlements are computed by the policy engine from a reviewed
pack, and `docs/38` reserves the words "current law" to one function. A rupee figure rendered here
from anything else would be an unreviewed legal claim made to a passenger. The policy surface owns
that answer; this one says nothing about it.

## 3. An alternative is not an offer

`RecoveryOptionOut.basis` is a `Literal`, and for a flight alternative it can only ever be
`schedule_feasible_only`. This system holds no seat-availability or capacity data whatsoever, so a
reachable later departure means exactly that: late enough to be caught. Presenting it as
availability would be inventing inventory. The type makes the weaker claim unavoidable.

## 4. Absent is not false, and absent is not zero

`disruption`, `connection`, `priority` and `delay_minutes` are all nullable, and each null means
"nothing is recorded", never "nothing is wrong". `unassessed_factors` carries the factors the
priority ruleset declares but no service has established yet, so a passenger is never told nobody
needs rebooking when nobody has looked.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.cascade import ImpactFactorOut, ProvenanceBlock, UnassessedFactorOut

#: What a segment is doing, in the vocabulary the flight board already publishes.
SegmentStatus = Literal["on_time", "scheduled", "delayed", "cancelled", "at_risk"]

#: Where one piece of work stands, as the orchestrator recorded it.
#:
#: `awaiting_approval` is a first-class state rather than a flavour of pending: a plan a person has
#: not signed is not in progress, and telling a passenger their rebooking is under way when it is
#: waiting for a human would misreport the one transition this system exists to make visible.
ActionState = Literal[
    "succeeded", "failed", "needs_human", "executing", "awaiting_approval", "pending"
]

#: Whether a piece of work is specific to this booking or covers the whole incident.
#:
#: The distinction is load-bearing. `check_connections` assessed hundreds of itineraries; saying
#: "we checked your connection" is true, but saying a room was held *for you* is only true when a
#: `hotel_reservation` row names this booking. One is scoped by the incident, the other by a row.
ActionScope = Literal["this_booking", "incident"]


class SegmentOut(BaseModel):
    """One flown leg of the booking, as recorded on `booking_segment` and `flight`."""

    model_config = ConfigDict(extra="forbid")

    segment_id: int
    segment_order: int
    flight_id: int
    flight_number: str
    #: ICAO, because that is what the database stores. Not translated to IATA here: inventing a
    #: mapping the dataset does not carry would be a guess rendered as a fact.
    origin_icao: str
    destination_icao: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    #: Null when the airline has published no revision. Never backfilled with the scheduled time.
    estimated_departure: datetime | None = None
    #: Null when no revision is published; `0` means published and on time. Different claims.
    delay_minutes: int | None = None
    status: SegmentStatus
    gate: str | None = None
    #: True when this leg is the one an incident was opened against.
    is_disrupted: bool = False


class TripOut(BaseModel):
    """The journey as sold, in segment order."""

    model_config = ConfigDict(extra="forbid")

    origin_icao: str
    destination_icao: str
    segments: list[SegmentOut] = Field(default_factory=list)


class DisruptionOut(BaseModel):
    """The recorded incident behind this trip.

    `cause_category` is the operational trigger as recorded — never a verdict about liability,
    which is a policy determination this surface does not make.
    """

    model_config = ConfigDict(extra="forbid")

    incident_reference: str
    group_reference: str | None = None
    flight_id: int
    flight_number: str
    airport_icao: str
    cause_category: str
    severity: str
    state: str
    opened_at: datetime
    closed_at: datetime | None = None


class ConnectionImpactOut(BaseModel):
    """This booking's own broken connection, lifted from the recorded assessment.

    Read from the `check_connections` action's payload, which is where the connection service
    persisted it. Not recomputed: a second implementation of "did this connection hold" is a second
    answer, and the passenger would have no way to tell which one was wrong.
    """

    model_config = ConfigDict(extra="forbid")

    inbound_flight_number: str
    onward_flight_number: str
    connection_airport_icao: str
    inbound_scheduled_arrival: datetime
    inbound_revised_arrival: datetime
    onward_scheduled_departure: datetime
    minimum_connection_minutes: int
    #: Negative for a broken connection: how far short of the minimum the turnaround falls.
    shortfall_minutes: int
    #: True when the onward flight is itself late enough that the passenger may still make it.
    #: Still recorded as broken as sold, so nobody is re-accommodated who does not need to be.
    recovered_by_onward_delay: bool
    #: Which recorded action established this, so the claim is traceable.
    established_by_action_id: int


class PriorityOut(BaseModel):
    """This passenger's recorded handling priority.

    A constraint ranking over persisted rows, used to order finite resources. Never a probability
    and never a statement about this person's worth.
    """

    model_config = ConfigDict(extra="forbid")

    priority_index: int
    priority_band: str
    #: The named reasons that produced the index, in the shape the operator surface already uses.
    #: Every point is attributable to one of these; a score without them cannot be argued with.
    factors: list[ImpactFactorOut] = Field(default_factory=list)
    rule_version: str
    ruleset_hash: str


class RecoveryOptionOut(BaseModel):
    """Something recorded that could change this passenger's journey.

    Only two kinds exist, because only two are backed by rows:

      * `alternative_flight` — a later departure on the same city pair that the schedule says is
        reachable. `basis` is pinned to `schedule_feasible_only`, and there is no seat data behind
        it anywhere in this system.
      * `hotel_room` — a `hotel_reservation` row naming this booking. `basis` records whether that
        reservation was real or simulated, because "a room is held" and "a room would be held" are
        different promises.

    There is deliberately no refund, meal or ground-transport option: nothing records them, and an
    option offered without a row behind it is a promise nobody made.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["alternative_flight", "hotel_room"]
    label: str
    basis: Literal["schedule_feasible_only", "recorded_reservation", "simulated_reservation"]
    flight_id: int | None = None
    flight_number: str | None = None
    scheduled_departure: datetime | None = None
    hotel_name: str | None = None
    nights: int | None = None
    #: True when arranging this needs an agent rather than being self-service. Recorded, not
    #: guessed: every option here is currently agent-arranged, because nothing in this system
    #: takes a passenger's instruction.
    requires_agent: bool = True


class PassengerActionOut(BaseModel):
    """One piece of recovery work, with the approval that authorised it.

    `state` is read from the recorded rows and nothing else. An action exists only with an
    assurance evaluation behind it, and a `needs_human` evaluation with no decision is
    `awaiting_approval` — the state a passenger most needs told accurately.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str
    state: ActionState
    applies_to: ActionScope
    #: Null while nothing has executed. Never a fabricated timestamp.
    at: datetime | None = None
    #: The service's own stable refusal code, when it declined. Never paraphrased prose.
    reason_code: str | None = None
    #: Set when a person decided. `plan` means one signature covered a set of actions.
    approval_scope: Literal["action", "plan"] | None = None
    #: True when this action cannot proceed until a person decides.
    awaiting_human: bool = False


class NextStepOut(BaseModel):
    """What happens next, derived only from recorded state.

    `respond_by` is always null: no deadline is recorded anywhere in this system, and a deadline
    shown to a passenger who then misses it would be this screen's invention doing real harm.
    """

    model_config = ConfigDict(extra="forbid")

    state: Literal["awaiting_approval", "executing", "resolved", "monitoring", "no_disruption"]
    #: The action that set the state, so the screen can point at something specific.
    driven_by_action_type: str | None = None
    respond_by: None = None


class PassengerDisruptionResponse(BaseModel):
    """Everything recorded about one booking's disruption, and nothing else.

    `note` states what the payload is and is not, in the same spirit as the group surfaces: a
    caption that travels with the figures cannot be separated from them by a screenshot.
    """

    model_config = ConfigDict(extra="forbid")

    booking_ref: str
    passenger_reference: str
    cabin: str
    tier: str
    has_special_needs: bool

    trip: TripOut
    #: Null when no incident touches any segment of this booking. Not an error: an undisrupted
    #: booking is a legitimate answer, and the screen says so rather than implying trouble.
    disruption: DisruptionOut | None = None
    connection: ConnectionImpactOut | None = None
    priority: PriorityOut | None = None

    options: list[RecoveryOptionOut] = Field(default_factory=list)
    actions: list[PassengerActionOut] = Field(default_factory=list)
    next_step: NextStepOut

    #: Factors the priority ruleset declares that no service has established. Never rendered false.
    unassessed_factors: list[UnassessedFactorOut] = Field(default_factory=list)

    #: Type-level statement of provenance, matching the group impact surface.
    basis: Literal["recorded_rows"] = "recorded_rows"
    note: str
    provenance: ProvenanceBlock
