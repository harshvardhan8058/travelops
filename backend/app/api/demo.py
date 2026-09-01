"""The demo control surface — Phase 5, Stream A.

A demonstration of TravelOps required a terminal: `python -m app.cli seed`, then `inject`, then
`demo-reset` between runs. Every one of those capabilities existed and none of them was reachable
from the product. This module exposes the three things an operator needs to run a demo from a
browser, and nothing more.

    GET  /demo/dataset      what is in the database right now
    GET  /demo/simulations  the named simulations this dataset can support
    POST /demo/reset        rebuild the dataset, on an explicitly typed confirmation

## It reuses the existing implementation rather than restating it

`dataset_counts`, `seed_demo_dataset` and `_clear_workflow_records` are called here, not
reimplemented. That matters more than the small awkwardness of importing a private helper from
`app.cli`: a second reset routine would be a second definition of "what a reset removes", and the
first thing to drift would be the foreign-key ordering that makes it work on Postgres at all.

## A simulation is a selection over recorded rows

The catalogue publishes no delays, no weather and no passenger figures of its own. Each definition
names a reproducible **selection** — an airport, a cause, a severity, and how the flights are
chosen — and is resolved against real `flight` rows when the endpoint is called. The members it
returns carry each flight's **recorded** delay.

That is forced rather than preferred. `POST /scenarios` refuses a declared delay that disagrees with
the recorded one, and it is right to: a builder that could assert its own operational facts would be
inventing the disruption it claims to be reacting to. So a simulation is a way of *pointing at* the
dataset, and the honesty of the numbers is the dataset's, not this module's.

Consequently there is no simulation engine here, and no second lifecycle. A simulation is POSTed to
the scenario endpoints the Scenario Builder already uses, and everything downstream — evidence,
planner, reflection, assurance, human approval, execution, passenger impact, replay — is the one
code path it has always been.

## Read-only unless it says otherwise

Two of the three routes are reads. The third is destructive, refuses outside a demo environment
through the same guard the CLI uses, and requires the caller to repeat a phrase. It reports what it
removed rather than what it intended to remove.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed import DEMO_DATASET_ID, INCIDENT_GROUP_REFERENCE, dataset_counts
from app.db.session import get_session
from app.errors import ValidationFailed
from app.models.enums import IncidentState, ProvenanceKind, TriggerType
from app.models.reference import Booking, BookingSegment, Flight
from app.models.workflow import Incident, IncidentGroup
from app.observability.logging import get_logger
from app.schemas.cascade import ProvenanceBlock
from app.schemas.demo import (
    RESET_CONFIRMATION,
    DatasetTableOut,
    DemoDatasetResponse,
    DemoResetRequest,
    DemoResetResponse,
    DemoSimulationsResponse,
    SimulationDefinitionOut,
    SimulationMemberOut,
)

router = APIRouter(tags=["demo"])
log = get_logger(__name__)

#: Bumped when a definition's meaning changes, so a recorded run can be tied to what it selected.
CATALOGUE_VERSION = "simulation-catalogue-v1"

#: Tables a demo cannot run without. `is_seeded` is derived from these rather than stored, because a
#: stored flag goes stale the moment somebody truncates a table.
_ESSENTIAL_TABLES = ("airport", "flight", "passenger", "booking", "booking_segment")

DATASET_NOTE = (
    "Row counts read back from the database, not cached. Reference rows come from the fixed-seed "
    "dataset; incident and group counts are the workflow's own output and a reset removes them."
)

CATALOGUE_NOTE = (
    "Each simulation is a reproducible selection over the recorded dataset, resolved against real "
    "flight rows. Delays are the recorded ones: the scenario API refuses any other value, so this "
    "catalogue cannot invent a disruption. Running one uses the same scenario lifecycle, "
    "orchestrator, assurance gate and approval path as any other cascade."
)


class _Definition:
    """A simulation shape, before it meets the dataset.

    Deliberately not a Pydantic model: it is never serialised. What reaches the wire is
    `SimulationDefinitionOut`, built only from rows that were actually found.
    """

    def __init__(
        self,
        *,
        identifier: str,
        name: str,
        summary: str,
        root_cause: TriggerType,
        airport_icao: str,
        severity: str,
        max_members: int,
        require_onward_connections: bool = False,
    ) -> None:
        self.identifier = identifier
        self.name = name
        self.summary = summary
        self.root_cause = root_cause
        self.airport_icao = airport_icao
        self.severity = severity
        self.max_members = max_members
        self.require_onward_connections = require_onward_connections


#: The catalogue. Three shapes, each answering a different operational question.
#:
#: They differ in what they select, not in how they run. `airport_cancellation_cascade` takes the
#: whole declared departure board so the crew and connection walks have something to chew on;
#: `connection_risk` takes only flights whose passengers actually hold an onward segment, which is
#: what makes the connection assessment produce anything at all.
_CATALOGUE: tuple[_Definition, ...] = (
    _Definition(
        identifier="bengaluru_severe_weather",
        name="Bengaluru severe weather",
        summary=(
            "A monsoon storm holds departures at Kempegowda. The most delayed departure leads, "
            "and the rest of the board follows it."
        ),
        root_cause=TriggerType.weather,
        airport_icao="VOBL",
        severity="high",
        max_members=4,
    ),
    _Definition(
        identifier="airport_cancellation_cascade",
        name="Airport-wide cancellation cascade",
        summary=(
            "The whole declared departure board at Kempegowda goes at once, so crew pairings and "
            "downstream legs are drawn in rather than one flight's passengers."
        ),
        root_cause=TriggerType.weather,
        airport_icao="VOBL",
        severity="high",
        max_members=8,
    ),
    _Definition(
        identifier="connection_risk",
        name="Connection-risk disruption",
        summary=(
            "Only flights whose passengers hold an onward segment. Narrower than a board-wide "
            "event, and the one that exercises the connection walk end to end."
        ),
        root_cause=TriggerType.weather,
        airport_icao="VOBL",
        severity="medium",
        max_members=3,
        require_onward_connections=True,
    ),
)


def _delay_minutes(flight: Flight) -> int:
    """The recorded delay, computed the way the rest of the system computes it.

    Mirrors `scenario_queries._flight_delay_minutes`, which `POST /scenarios` validates against.
    Kept as a local read of the same two columns rather than a second rule: if these disagreed, a
    simulation would be rejected by the endpoint it exists to feed.
    """
    scheduled = flight.scheduled_departure
    estimated = flight.estimated_departure
    if estimated is None or scheduled is None:
        return 0
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    if estimated.tzinfo is None:
        estimated = estimated.replace(tzinfo=UTC)
    return max(0, int((estimated - scheduled).total_seconds() // 60))


async def _declared_member_flights(session: AsyncSession, airport_icao: str) -> list[Flight]:
    """Departures from this airport, most delayed first.

    Ordered by recorded delay so the primary is the flight the dataset says is worst affected. A
    stable secondary sort on id keeps the selection reproducible, which is what lets a demo be run
    twice and compared.
    """
    rows = (
        (
            await session.execute(
                select(Flight).where(Flight.origin_icao == airport_icao).order_by(Flight.id)
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda flight: (-_delay_minutes(flight), flight.id))


async def _flight_ids_with_onward_segments(session: AsyncSession) -> set[int]:
    """Flights whose passengers hold a later segment on the same booking.

    Read from `booking_segment` rather than assumed from the schedule: a connection exists because
    somebody bought it, and that is a recorded fact.
    """
    inbound = BookingSegment.__table__.alias("inbound")
    onward = BookingSegment.__table__.alias("onward")
    rows = (
        await session.execute(
            select(inbound.c.flight_id)
            .join(
                onward,
                (onward.c.booking_id == inbound.c.booking_id)
                & (onward.c.segment_order > inbound.c.segment_order),
            )
            .distinct()
        )
    ).scalars()
    return {int(value) for value in rows}


async def _passengers_on(session: AsyncSession, flight_ids: list[int]) -> int | None:
    """Distinct passengers booked on any of these flights, or None when none are recorded."""
    if not flight_ids:
        return None
    count = (
        await session.execute(
            select(func.count(func.distinct(Booking.passenger_id)))
            .select_from(BookingSegment)
            .join(Booking, BookingSegment.booking_id == Booking.id)
            .where(BookingSegment.flight_id.in_(flight_ids))
        )
    ).scalar_one()
    return int(count) or None


async def _active_incidents_on(session: AsyncSession, flight_ids: list[int]) -> list[str]:
    """References of non-terminal incidents already covering any of these flights.

    The same `IncidentState.active()` set `_assert_no_foreign_active_incidents` uses in
    `app.api.scenarios`, so the catalogue and the start endpoint cannot disagree about what an
    active workflow is. Restating the state list here instead would let the two drift, and the
    symptom would be a button that looks ready and answers 409.

    Sorted so the reason string is stable across requests, which keeps a recorded run comparable.
    """
    if not flight_ids:
        return []
    references = (
        await session.execute(
            select(Incident.reference)
            .where(
                Incident.flight_id.in_(flight_ids),
                Incident.state.in_([state.value for state in IncidentState.active()]),
            )
            .order_by(Incident.reference)
        )
    ).scalars()
    return list(references)


async def _scenario_clock(session: AsyncSession) -> datetime | None:
    """The recorded scenario clock: the seeded group's `opened_at`.

    Read from the row rather than derived, because it is the SAME value `app.cli._inject` reads,
    and a second copy would drift from the evidence it has to agree with. That function's own
    docstring says why it matters: the timestamp "flows into the incident's `opened_at`, and from
    there into the Delay Risk `as_of` — which is what makes the storm score against the observation
    that was current when it hit".

    `None` when the dataset has not been seeded, in which case no simulation can be declared at all.
    """
    opened_at = (
        await session.execute(
            select(IncidentGroup.opened_at).where(
                IncidentGroup.reference == INCIDENT_GROUP_REFERENCE
            )
        )
    ).scalar_one_or_none()
    if opened_at is None:
        return None
    return opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=UTC)


async def _resolve(
    session: AsyncSession,
    definition: _Definition,
    *,
    onward: set[int],
    clock: datetime | None,
) -> SimulationDefinitionOut:
    """Turn a shape into a runnable definition, or into an honest refusal.

    A definition the dataset cannot support is still listed. Hiding it would leave an operator
    wondering where the third simulation went; this way it appears, cannot be started, and says why.
    """
    candidates = await _declared_member_flights(session, definition.airport_icao)
    if definition.require_onward_connections:
        candidates = [flight for flight in candidates if flight.id in onward]

    blocked: str | None = None
    if clock is None:
        # Without the recorded scenario clock there is no honest instant to declare this at,
        # and the wall clock produces an unapprovable evidence refusal. Refused, not guessed.
        blocked = (
            "the seeded disruption group is missing, so the recorded scenario clock cannot "
            "be read. Reset the demo data to restore it."
        )
    elif not candidates:
        blocked = (
            f"no flights depart {definition.airport_icao} in the current dataset"
            if not definition.require_onward_connections
            else (
                f"no departure from {definition.airport_icao} carries a passenger with an onward "
                "segment, so a connection assessment would have nothing to walk"
            )
        )

    selected = candidates[: definition.max_members]
    members = [
        SimulationMemberOut(
            flight_id=flight.id,
            flight_number=flight.flight_number,
            # Exactly one primary, as the scenario contract requires. The most delayed flight
            # leads, which is the one an operator would work first.
            role="primary" if index == 0 else "affected_departure",
            origin_icao=flight.origin_icao,
            destination_icao=flight.destination_icao,
            delay_minutes=_delay_minutes(flight),
        )
        for index, flight in enumerate(selected)
    ]

    if members and all(member.delay_minutes == 0 for member in members):
        # A cascade in which nothing is late is not a disruption. Refused rather than opened,
        # because opening it would put an incident on screen with no operational cause.
        blocked = (
            "no departure from this airport has a recorded delay, so there is no disruption to "
            "simulate. Reset the demo dataset to restore the seeded delays."
        )

    if blocked is None and members:
        # A flight already inside an active workflow cannot be declared by a new scenario:
        # `POST /scenarios/{ref}/start` refuses it with 409 INVALID_STATE_TRANSITION. Checking it
        # here is what makes `runnable` mean "can be started against the database as it is now"
        # rather than "the dataset contains suitable rows".
        #
        # Without this the console offered an enabled button whose only outcome was a 409 — a
        # control that looks ready and cannot work, which is the specific failure `blocked_reason`
        # exists to prevent. The catalogue does not reimplement the rule: it runs the same active-
        # state query and names the incidents that own the flights.
        conflicts = await _active_incidents_on(session, [member.flight_id for member in members])
        if conflicts:
            blocked = (
                "these flights are already inside an active workflow "
                f"({', '.join(conflicts)}), and a scenario cannot declare a flight another "
                "workflow owns. Resolve that disruption, or reset the demo data, and this "
                "simulation becomes available again."
            )

    return SimulationDefinitionOut(
        id=definition.identifier,
        name=definition.name,
        summary=definition.summary,
        root_cause=definition.root_cause,
        airport_icao=definition.airport_icao,
        severity=definition.severity,
        # The recorded clock when it is readable. When it is not, this definition is already
        # blocked, so the value is never acted on — but the field is non-optional, and the epoch
        # rather than `now()` keeps a blocked entry from ever looking declarable.
        effective_at=clock if clock is not None else datetime(1970, 1, 1, tzinfo=UTC),
        members=members if blocked is None else [],
        passengers_affected=(
            await _passengers_on(session, [member.flight_id for member in members])
            if blocked is None
            else None
        ),
        runnable=blocked is None,
        blocked_reason=blocked,
        provenance=ProvenanceBlock(
            kind=ProvenanceKind.simulated.value,
            provider="demo.simulation_catalogue",
            source_ref=f"{CATALOGUE_VERSION}:{definition.identifier}",
        ),
    )


@router.get(
    "/demo/dataset",
    response_model=DemoDatasetResponse,
    summary="What is in the demo database right now",
)
async def get_demo_dataset(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DemoDatasetResponse:
    from app.config import AppEnv, get_settings

    counts = await dataset_counts(session)
    settings = get_settings()

    groups = int(
        (await session.execute(select(func.count()).select_from(IncidentGroup))).scalar_one()
    )
    incidents = int(
        (await session.execute(select(func.count()).select_from(Incident))).scalar_one()
    )

    # The same rule `/incident-groups/current` uses: a group with no incident has not been started,
    # so it is not what the console is looking at.
    started = select(Incident.id).where(Incident.group_id == IncidentGroup.id).exists()
    current = (
        (
            await session.execute(
                select(IncidentGroup.reference)
                .where(started)
                .order_by(IncidentGroup.opened_at.desc(), IncidentGroup.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    return DemoDatasetResponse(
        is_seeded=all(counts.get(table, 0) > 0 for table in _ESSENTIAL_TABLES),
        tables=[DatasetTableOut(table=table, rows=rows) for table, rows in counts.items()],
        flights=counts.get("flight", 0),
        bookings=counts.get("booking", 0),
        booking_segments=counts.get("booking_segment", 0),
        airports=counts.get("airport", 0),
        incident_groups=groups,
        incidents=incidents,
        current_group_reference=current,
        reset_allowed=settings.app_env in {AppEnv.development, AppEnv.demo, AppEnv.test},
        app_env=settings.app_env.value,
        note=DATASET_NOTE,
    )


@router.get(
    "/demo/simulations",
    response_model=DemoSimulationsResponse,
    summary="Named simulations the current dataset can support",
)
async def list_demo_simulations(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DemoSimulationsResponse:
    onward = await _flight_ids_with_onward_segments(session)
    clock = await _scenario_clock(session)
    simulations = [
        await _resolve(session, definition, onward=onward, clock=clock) for definition in _CATALOGUE
    ]
    return DemoSimulationsResponse(
        catalogue_version=CATALOGUE_VERSION,
        simulations=simulations,
        runnable_count=sum(1 for simulation in simulations if simulation.runnable),
        note=CATALOGUE_NOTE,
    )


@router.post(
    "/demo/reset",
    response_model=DemoResetResponse,
    summary="Rebuild the demo dataset, on an explicitly typed confirmation",
)
async def reset_demo(
    payload: DemoResetRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DemoResetResponse:
    """Destructive, and gated twice.

    First by environment, through the same guard the CLI uses — so this cannot run against anything
    but a demo or development database. Then by a typed phrase, so a mis-click cannot trigger it.

    The order of operations is not incidental: the orchestrator's rows come out before the seed
    clears its own, because `seed_demo_dataset` deletes `incident` and a `decision_log` row still
    referencing it makes Postgres reject the delete outright.
    """
    # `_clear_workflow_records` and `_refuse_outside_demo` are imported rather than restated. A
    # second definition of "what a reset removes" would drift on exactly the foreign-key ordering
    # that makes this work on Postgres.
    from app.cli import _clear_workflow_records, _refuse_outside_demo
    from app.db.seed import seed_demo_dataset

    _refuse_outside_demo()

    if payload.confirm.strip().lower() != RESET_CONFIRMATION:
        raise ValidationFailed(
            "the reset confirmation phrase does not match",
            details={
                "expected": RESET_CONFIRMATION,
                "resolution": f"send confirm='{RESET_CONFIRMATION}' to proceed",
            },
        )

    removed = await _clear_workflow_records(session)
    report = await seed_demo_dataset(session, reset=True)
    await session.flush()

    seeded_group = (
        (
            await session.execute(
                select(IncidentGroup.reference).where(
                    IncidentGroup.reference == INCIDENT_GROUP_REFERENCE
                )
            )
        )
        .scalars()
        .first()
    )

    log.info(
        "demo_dataset_reset",
        actor_id=payload.actor_id,
        workflow_rows_removed=sum(removed.values()),
        seeded_rows=sum(report.counts.values()),
        dataset_id=DEMO_DATASET_ID,
    )

    return DemoResetResponse(
        workflow_removed={table: count for table, count in removed.items() if count},
        seeded=dict(report.counts),
        dataset_digest=report.digest,
        seeded_group_reference=seeded_group,
        performed_by=payload.actor_id,
        performed_at=datetime.now(UTC),
        note=(
            "The dataset is restored and no cascade is open: after a reset no incident exists, "
            "which is why the console reports nothing in progress. Run a simulation or open the "
            "seeded cascade to start one."
        ),
    )
