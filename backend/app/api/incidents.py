"""Incident endpoints — STREAM A.

Replaces the Wave 0 fixture routes for `/incidents/{ref}`, `/incidents/{ref}/timeline` and
`POST /incidents/{ref}/run`. Those fixture routes are deleted in the same commit, so there
is never a period where two implementations of one path exist.

Every response declares a Pydantic `response_model`. The fixture routes returned `Any`,
which made the OpenAPI document render their schemas as `"string"` — useless for a
generated client, and the reason `frontend/src/api/types.ts` had to be hand-written.

Nothing here computes a domain number. The endpoints read what the orchestrator recorded
and shape it; where a value has not been derived yet, the key is absent rather than zero.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.enums import IncidentState
from app.models.reference import Booking, BookingSegment, Flight, WeatherObservation
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    DecisionLog,
    Incident,
    IncidentGroup,
    Plan,
    Prediction,
)
from app.models.workflow import PlanTask as PlanTaskRow
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator.engine import Orchestrator
from app.schemas.incidents import (
    ActionSummary,
    FlightSummary,
    IncidentDetailResponse,
    IncidentEvidence,
    PlanSummary,
    PlanTaskSummary,
    RiskEvidence,
    RiskFactor,
    RunResponse,
    StateRailEntry,
    TimelineEntry,
    TimelineResponse,
    WeatherEvidence,
)
from app.schemas.provenance import Provenance, ProvenanceKind

router = APIRouter(tags=["incidents"])
log = get_logger(__name__)

#: The canonical happy path, so the UI progress rail has a stable spine. Any branch state
#: the incident actually reached is appended, rather than the rail pretending it did not
#: happen.
_RAIL: tuple[IncidentState, ...] = (
    IncidentState.detected,
    IncidentState.assessing,
    IncidentState.planning,
    IncidentState.assuring,
    IncidentState.executing,
    IncidentState.resolved,
)

_BRANCH_STATES: tuple[IncidentState, ...] = (
    IncidentState.awaiting_approval,
    IncidentState.blocked,
    IncidentState.failed,
)

#: actor -> actor_kind, so the UI groups without string matching. `assurance_gate` is part
#: of the deterministic control plane, which is why it is not its own kind.
_ACTOR_KINDS: dict[str, str] = {
    "orchestrator": "orchestrator",
    "assurance_gate": "orchestrator",
    "human": "human",
    "provider": "provider",
}

RUN_EVENT = "WORKFLOW_RUN_REQUESTED"

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        description="Replaying a key returns the original result instead of acting twice.",
    ),
]


def _actor_kind(actor: str) -> str:
    """Classify an actor for the UI, which groups by kind rather than by name."""
    if actor in _ACTOR_KINDS:
        return _ACTOR_KINDS[actor]
    if actor.endswith("_service"):
        return "service"
    if actor.endswith("_agent"):
        return "agent"
    return "orchestrator"


async def _load_incident(session: AsyncSession, key: str) -> Incident:
    """Accept either the reference (INC-2026-0820-VOBL-01) or the numeric id."""
    stmt = select(Incident).where(Incident.reference == key).limit(1)
    incident = (await session.execute(stmt)).scalars().first()
    if incident is None and key.isdigit():
        incident = await session.get(Incident, int(key))
    if incident is None:
        raise EntityNotFound("incident not found", details={"incident": key})
    return incident


def _as_utc(value: datetime | None) -> datetime | None:
    """Label a naive timestamp as UTC. Storage is always UTC; display converts, not this."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentDetailResponse,
    summary="Incident state, plan, tasks and recorded evidence",
)
async def get_incident(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentDetailResponse:
    incident = await _load_incident(session, incident_id)
    flight = await session.get(Flight, incident.flight_id)
    if flight is None:
        raise EntityNotFound("flight not found", details={"flight_id": incident.flight_id})

    group_reference: str | None = None
    if incident.group_id is not None:
        group = await session.get(IncidentGroup, incident.group_id)
        group_reference = group.reference if group else None

    scheduled = _as_utc(flight.scheduled_departure)
    estimated = _as_utc(flight.estimated_departure)
    delay_minutes = int((estimated - scheduled).total_seconds() // 60) if estimated else 0

    passengers = await _passenger_count(session, flight.id)
    plan = await _plan_summary(session, incident.id)

    return IncidentDetailResponse(
        id=incident.id,
        reference=incident.reference,
        group_reference=group_reference,
        flight=FlightSummary(
            id=flight.id,
            flight_number=flight.flight_number,
            route=f"{flight.origin_icao} → {flight.destination_icao}",
            scheduled_departure=scheduled,
            estimated_departure=estimated,
            delay_minutes=delay_minutes,
            block_time_minutes=flight.block_time_minutes,
            passengers=passengers,
        ),
        trigger_type=str(incident.trigger_type),
        severity=incident.severity,
        state=IncidentState(incident.state),
        opened_at=_as_utc(incident.opened_at),
        closed_at=_as_utc(incident.closed_at),
        state_rail=await _state_rail(session, incident),
        evidence=await _evidence(session, incident, flight, passengers),
        plan=plan,
        actions=await _actions(session, incident.id),
        provenance=Provenance(
            kind=ProvenanceKind(str(flight.provenance_kind)),
            provider="generator",
            source_ref=flight.source_ref,
        ),
    )


async def _passenger_count(session: AsyncSession, flight_id: int) -> int | None:
    """Distinct passengers booked on this flight, or None when no bookings are seeded.

    None rather than 0: "no records" and "nobody affected" are different claims, and only
    one of them is true here.
    """
    stmt = (
        select(func.count(func.distinct(Booking.passenger_id)))
        .select_from(BookingSegment)
        .join(Booking, BookingSegment.booking_id == Booking.id)
        .where(BookingSegment.flight_id == flight_id)
    )
    count = (await session.execute(stmt)).scalar_one()
    return int(count) if count else None


async def _state_rail(session: AsyncSession, incident: Incident) -> list[StateRailEntry]:
    """Reconstructed from the recorded transitions, not from the current state.

    That distinction matters: the rail is evidence of what happened, so a state nobody
    reached must show `null` rather than being inferred from position in the sequence.
    """
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.incident_id == incident.id, DecisionLog.event_type == "STATE_CHANGED")
        .order_by(DecisionLog.id)
    )
    reached: dict[str, datetime] = {}
    for entry in (await session.execute(stmt)).scalars():
        target = (entry.detail or {}).get("to")
        if target and target not in reached:
            reached[target] = _as_utc(entry.occurred_at)
    reached.setdefault(IncidentState.detected.value, _as_utc(incident.opened_at))

    rail = [StateRailEntry(state=state, reached_at=reached.get(state.value)) for state in _RAIL]
    rail.extend(
        StateRailEntry(state=state, reached_at=reached[state.value])
        for state in _BRANCH_STATES
        if state.value in reached
    )
    return rail


async def _evidence(
    session: AsyncSession, incident: Incident, flight: Flight, passengers: int | None
) -> IncidentEvidence:
    risk: RiskEvidence | None = None
    if incident.prediction_id is not None:
        prediction = await session.get(Prediction, incident.prediction_id)
        if prediction is not None:
            risk = RiskEvidence(
                risk_index=prediction.risk_index,
                risk_level=prediction.risk_level,
                rule_version=prediction.rule_version,
                factors=[_risk_factor(item) for item in (prediction.factors or [])],
                evidence_refs=list(prediction.evidence_refs or []),
            )

    weather: WeatherEvidence | None = None
    stmt = (
        select(WeatherObservation)
        .where(WeatherObservation.airport_icao == flight.origin_icao)
        .order_by(WeatherObservation.observed_at.desc())
        .limit(1)
    )
    observation = (await session.execute(stmt)).scalars().first()
    if observation is not None:
        weather = WeatherEvidence(
            airport_icao=observation.airport_icao,
            observed_at=_as_utc(observation.observed_at),
            wind_speed_kt=observation.wind_speed_kt,
            wind_direction_deg=observation.wind_direction_deg,
            visibility_m=observation.visibility_m,
            ceiling_ft=observation.ceiling_ft,
            precipitation=observation.precipitation,
            provenance=Provenance(
                kind=ProvenanceKind(str(observation.provenance_kind)),
                provider=observation.provenance_provider,
                source_ref=observation.source_ref,
                observed_at=_as_utc(observation.observed_at),
            ),
        )

    # Only counts that came from records. A key is omitted when the data does not exist,
    # because 0 would read as "nothing affected".
    affected: dict[str, int] = {}
    if passengers is not None:
        affected["passengers"] = passengers
        bookings = (
            await session.execute(
                select(func.count(func.distinct(BookingSegment.booking_id))).where(
                    BookingSegment.flight_id == flight.id
                )
            )
        ).scalar_one()
        affected["bookings"] = int(bookings)

    return IncidentEvidence(
        risk=risk,
        weather=weather,
        affected_entities=affected,
        retrieved_precedent=None,
    )


def _risk_factor(item: Any) -> RiskFactor:
    if isinstance(item, dict):
        return RiskFactor(
            name=str(item.get("name", "")),
            value=str(item.get("value", "")),
            threshold=item.get("threshold"),
            runway=item.get("runway"),
        )
    return RiskFactor(name=str(item), value="")


async def _plan_summary(session: AsyncSession, incident_id: int) -> PlanSummary | None:
    plan = (
        (
            await session.execute(
                select(Plan)
                .where(Plan.incident_id == incident_id)
                .order_by(Plan.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if plan is None:
        return None

    rows = (
        (
            await session.execute(
                select(PlanTaskRow)
                .where(PlanTaskRow.plan_id == plan.id)
                .order_by(PlanTaskRow.task_order)
            )
        )
        .scalars()
        .all()
    )
    latest = await _latest_evaluation_ids(session, [row.id for row in rows])
    return PlanSummary(
        id=plan.id,
        generator=plan.generator,
        prompt_version=plan.prompt_version,
        model_self_report=plan.model_self_report,
        generated_at=_as_utc(plan.generated_at),
        rationale=plan.rationale,
        tasks=[
            PlanTaskSummary(
                id=row.id,
                task_order=row.task_order,
                action_type=row.action_type,
                state=row.state,
                depends_on=[str(dep) for dep in (row.depends_on or [])],
                assurance_id=latest.get(row.id),
            )
            for row in rows
        ],
    )


async def _latest_evaluation_ids(session: AsyncSession, plan_task_ids: list[int]) -> dict[int, int]:
    if not plan_task_ids:
        return {}
    stmt = (
        select(AssuranceEvaluation.plan_task_id, func.max(AssuranceEvaluation.id))
        .where(AssuranceEvaluation.plan_task_id.in_(plan_task_ids))
        .group_by(AssuranceEvaluation.plan_task_id)
    )
    return dict((await session.execute(stmt)).all())


async def _actions(session: AsyncSession, incident_id: int) -> list[ActionSummary]:
    stmt = (
        select(Action, PlanTaskRow)
        .join(PlanTaskRow, Action.plan_task_id == PlanTaskRow.id)
        .join(Plan, PlanTaskRow.plan_id == Plan.id)
        .where(Plan.incident_id == incident_id)
        .order_by(Action.id)
    )
    return [
        ActionSummary(
            id=action.id,
            plan_task_id=action.plan_task_id,
            action_type=task_row.action_type,
            assurance_id=action.assurance_id,
            human_decision_id=action.human_decision_id,
            actor=action.actor,
            status=str(action.status),
            reason=action.reason,
            cost_inr=action.cost_inr,
            provenance_kind=action.provenance_kind,
            executed_at=_as_utc(action.executed_at),
            idempotency_key=action.idempotency_key,
        )
        for action, task_row in (await session.execute(stmt)).all()
    ]


@router.get(
    "/incidents/{incident_id}/timeline",
    response_model=TimelineResponse,
    summary="Ordered immutable event and action records",
)
async def get_timeline(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TimelineResponse:
    incident = await _load_incident(session, incident_id)
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.incident_id == incident.id)
        .order_by(DecisionLog.occurred_at, DecisionLog.id)
    )
    return TimelineResponse(
        incident_reference=incident.reference,
        entries=[
            TimelineEntry(
                id=entry.id,
                occurred_at=_as_utc(entry.occurred_at),
                stage=entry.stage,
                actor=entry.actor,
                actor_kind=_actor_kind(entry.actor),
                event_type=entry.event_type,
                summary=entry.summary,
                detail=entry.detail,
                correlation_id=entry.correlation_id,
            )
            for entry in (await session.execute(stmt)).scalars()
        ],
    )


@router.post(
    "/incidents/{incident_id}/run",
    response_model=RunResponse,
    summary="Continue the workflow from its current legal state",
)
async def run_incident(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> RunResponse:
    """Drive the incident forward.

    Stopping short of a terminal state is a normal outcome: `awaiting_approval` means the
    gate asked for a person, and `note` says so. An illegal transition raises
    `409 INVALID_STATE_TRANSITION` from the state machine rather than being silently ignored.

    Replay protection uses `decision_log` as the ledger. A `WORKFLOW_RUN_REQUESTED` entry
    records the key alongside the result it produced, so a repeated key returns that result
    without advancing again. The alternative — a dedicated idempotency table — would need a
    migration, and `backend/migrations/` is Stream C's.
    """
    incident = await _load_incident(session, incident_id)

    if idempotency_key:
        replay = await _recorded_run(session, incident.id, idempotency_key)
        if replay is not None:
            log.info(
                "workflow_run_replayed",
                incident_reference=incident.reference,
                idempotency_key=idempotency_key,
                outcome="idempotent_replay",
            )
            return replay

    previous = IncidentState(incident.state)
    orchestrator = Orchestrator(session)
    ctx = await orchestrator.load_context(incident.id, correlation_id=correlation_id_var.get())
    await orchestrator.run(ctx)

    response = RunResponse(
        incident_reference=incident.reference,
        state=ctx.state,
        previous_state=previous,
        steps_taken=ctx.steps_taken,
        is_terminal=ctx.state in IncidentState.terminal(),
        note=ctx.last_note,
        replayed=False,
        idempotency_key=idempotency_key,
    )

    if idempotency_key:
        session.add(
            DecisionLog(
                incident_id=incident.id,
                occurred_at=datetime.now(UTC),
                stage="run",
                actor="orchestrator",
                event_type=RUN_EVENT,
                summary=f"Run requested with idempotency key {idempotency_key}",
                detail={
                    "idempotency_key": idempotency_key,
                    "result": response.model_dump(mode="json"),
                },
                correlation_id=ctx.correlation_id,
            )
        )
        await session.flush()
    return response


async def _recorded_run(session: AsyncSession, incident_id: int, key: str) -> RunResponse | None:
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.incident_id == incident_id, DecisionLog.event_type == RUN_EVENT)
        .order_by(DecisionLog.id)
    )
    for entry in (await session.execute(stmt)).scalars():
        detail = entry.detail or {}
        if detail.get("idempotency_key") == key and isinstance(detail.get("result"), dict):
            return RunResponse(**{**detail["result"], "replayed": True})
    return None
