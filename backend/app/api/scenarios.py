"""Scenario Builder lifecycle endpoints.

The API deliberately persists into the existing incident-group aggregate and starts work through
``GroupOrchestrator``. It does not introduce a competing scenario, incident, or execution model.

Owner: Stream A.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.scenario_queries import _flight_delay_minutes as recorded_flight_delay_minutes
from app.db.session import get_session
from app.errors import EntityNotFound, InvalidStateTransition, ValidationFailed
from app.models.cascade import IncidentGroupFlight
from app.models.enums import IncidentState, ProvenanceKind
from app.models.reference import Airport, Flight
from app.models.workflow import DecisionLog, Incident, IncidentGroup
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator.group import GroupOrchestrator
from app.schemas.cascade import GroupMemberOut, ProvenanceBlock
from app.schemas.scenarios import (
    ScenarioCreateRequest,
    ScenarioCreateResponse,
    ScenarioMemberInput,
    ScenarioMemberOut,
    ScenarioStartRequest,
    ScenarioStartResponse,
)

router = APIRouter(tags=["scenarios"])
log = get_logger(__name__)

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="A repeat returns the first recorded lifecycle result.",
    ),
]

SCENARIO_CREATED = "SCENARIO_CREATED"
SCENARIO_START_REQUESTED = "SCENARIO_START_REQUESTED"
SCENARIO_STARTED = "SCENARIO_STARTED"
SCENARIO_STAGE = "scenario"
SCENARIO_PROVIDER = "scenario-builder"


def _idempotency_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _request_fingerprint(payload: BaseModel) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _advisory_key(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big", signed=True)


@asynccontextmanager
async def _operation_locks(session: AsyncSession, values: list[str]) -> AsyncIterator[None]:
    """Serialize lifecycle operations on PostgreSQL without adding a lock table.

    Session-level advisory locks survive the commits inside ``open_incident``. Acquiring the
    sorted member-flight set makes two different scenarios that overlap on any flight mutually
    exclusive, while deterministic ordering prevents deadlocks for multi-flight scenarios.
    """
    if not values or session.bind is None or session.bind.dialect.name != "postgresql":
        yield
        return

    keys = sorted({_advisory_key(value) for value in values})
    # Hold locks on a dedicated connection. The workflow session commits once per opened
    # incident and a NullPool may then close that session's connection, which would silently
    # release a session-level advisory lock before the group is complete.
    async with session.bind.connect() as lock_connection:
        acquired: list[int] = []
        try:
            for key in keys:
                await lock_connection.execute(select(func.pg_advisory_lock(key)))
                acquired.append(key)
        except BaseException:
            # Closing the physical backend session releases both the known acquired set and a
            # lock whose server response may have raced with task cancellation.
            await lock_connection.invalidate()
            raise

        try:
            yield
        finally:
            for key in reversed(acquired):
                try:
                    await lock_connection.execute(select(func.pg_advisory_unlock(key)))
                except BaseException:
                    await lock_connection.invalidate()
                    raise


@asynccontextmanager
async def _operation_lock(session: AsyncSession, value: str | None) -> AsyncIterator[None]:
    async with _operation_locks(session, [value] if value is not None else []):
        yield


def _new_reference(effective_at: datetime) -> str:
    day = effective_at.astimezone(UTC).strftime("%Y%m%d")
    return f"SCN-{day}-{uuid.uuid4().hex[:10].upper()}"


def _provenance(reference: str) -> ProvenanceBlock:
    return ProvenanceBlock(
        kind=ProvenanceKind.simulated.value,
        provider=SCENARIO_PROVIDER,
        source_ref=f"scenario-builder:{reference}",
    )


def _dataset_id(reference: str, flights: dict[int, Flight]) -> str:
    """Keep scenario/group/incident ownership aligned with their referenced flight data."""
    demo_dataset_id = get_settings().demo_dataset_id
    if any(demo_dataset_id in (flight.source_ref or "") for flight in flights.values()):
        return demo_dataset_id
    return reference


async def _recorded_create(
    session: AsyncSession,
    idempotency_key: str,
    request_fingerprint: str,
) -> ScenarioCreateResponse | None:
    digest = _idempotency_digest(idempotency_key)
    entries = (
        await session.execute(
            select(DecisionLog)
            .where(DecisionLog.event_type == SCENARIO_CREATED)
            .order_by(DecisionLog.id)
        )
    ).scalars()
    for entry in entries:
        detail = entry.detail or {}
        if detail.get("idempotency_digest") != digest:
            continue
        if detail.get("request_fingerprint") != request_fingerprint:
            raise InvalidStateTransition(
                "Idempotency-Key was already used for a different scenario request",
                details={"idempotency_key_reused": True},
            )
        result = detail.get("result")
        if isinstance(result, dict):
            return ScenarioCreateResponse(**{**result, "replayed": True})
    return None


async def _scenario(
    session: AsyncSession, scenario_reference: str
) -> tuple[IncidentGroup, DecisionLog]:
    group = (
        await session.execute(
            select(IncidentGroup).where(IncidentGroup.reference == scenario_reference)
        )
    ).scalar_one_or_none()
    created = (
        await session.execute(
            select(DecisionLog)
            .where(
                DecisionLog.event_type == SCENARIO_CREATED,
                DecisionLog.correlation_id == scenario_reference,
            )
            .order_by(DecisionLog.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if group is None or created is None:
        raise EntityNotFound(
            "scenario not found", details={"scenario_reference": scenario_reference}
        )
    return group, created


async def _recorded_start(
    session: AsyncSession, scenario_reference: str
) -> ScenarioStartResponse | None:
    entry = (
        await session.execute(
            select(DecisionLog)
            .where(
                DecisionLog.event_type == SCENARIO_STARTED,
                DecisionLog.correlation_id == scenario_reference,
            )
            .order_by(DecisionLog.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if entry is None:
        return None
    result = (entry.detail or {}).get("result")
    if not isinstance(result, dict):
        return None
    return ScenarioStartResponse(**{**result, "replayed": True})


async def _validated_flights(
    session: AsyncSession,
    *,
    airport_icao: str,
    members: list[ScenarioMemberInput],
) -> dict[int, Flight]:
    if await session.get(Airport, airport_icao) is None:
        raise EntityNotFound("airport not found", details={"airport_icao": airport_icao})

    requested_ids = [member.flight_id for member in members]
    flights = (await session.execute(select(Flight).where(Flight.id.in_(requested_ids)))).scalars()
    by_id = {flight.id: flight for flight in flights}
    missing = sorted(set(requested_ids) - set(by_id))
    if missing:
        raise EntityNotFound("one or more flights were not found", details={"flight_ids": missing})

    route_errors: list[dict[str, object]] = []
    delay_errors: list[dict[str, object]] = []
    for member in members:
        flight = by_id[member.flight_id]
        expected_airport = (
            flight.destination_icao if member.role == "affected_arrival" else flight.origin_icao
        )
        if expected_airport != airport_icao:
            route_errors.append(
                {
                    "flight_id": flight.id,
                    "flight_number": flight.flight_number,
                    "role": member.role,
                    "expected_airport_icao": expected_airport,
                }
            )
        recorded_delay = recorded_flight_delay_minutes(flight)
        if member.delay_minutes != recorded_delay:
            delay_errors.append(
                {
                    "flight_id": flight.id,
                    "flight_number": flight.flight_number,
                    "declared_delay_minutes": member.delay_minutes,
                    "recorded_delay_minutes": recorded_delay,
                }
            )
    if route_errors:
        raise ValidationFailed(
            "scenario membership does not match the root airport",
            details={"airport_icao": airport_icao, "members": route_errors},
        )
    if delay_errors:
        raise ValidationFailed(
            "scenario delay does not match recorded flight state",
            details={"members": delay_errors},
        )
    return by_id


async def _member_flight_ids(session: AsyncSession, group_id: int) -> list[int]:
    return list(
        (
            await session.execute(
                select(IncidentGroupFlight.flight_id).where(
                    IncidentGroupFlight.incident_group_id == group_id
                )
            )
        )
        .scalars()
        .all()
    )


async def _assert_no_foreign_active_incidents(
    session: AsyncSession, *, group: IncidentGroup, flight_ids: list[int]
) -> None:
    active = [state.value for state in IncidentState.active()]
    incidents = (
        await session.execute(
            select(Incident).where(
                Incident.flight_id.in_(flight_ids),
                Incident.state.in_(active),
            )
        )
    ).scalars()
    conflicts = [incident for incident in incidents if incident.group_id != group.id]
    if conflicts:
        raise InvalidStateTransition(
            "scenario cannot start while a member flight belongs to another active workflow",
            details={
                "scenario_reference": group.reference,
                "conflicts": [
                    {
                        "flight_id": incident.flight_id,
                        "incident_reference": incident.reference,
                        "incident_group_id": incident.group_id,
                    }
                    for incident in conflicts
                ],
            },
        )


async def _assert_opened_incident_ownership(
    session: AsyncSession, *, group: IncidentGroup, incident_ids: list[int]
) -> None:
    if not incident_ids:
        return
    incidents = (
        await session.execute(select(Incident).where(Incident.id.in_(incident_ids)))
    ).scalars()
    conflicts = [incident for incident in incidents if incident.group_id != group.id]
    if conflicts:
        raise InvalidStateTransition(
            "scenario start conflicted with another active workflow",
            details={
                "scenario_reference": group.reference,
                "incident_references": [incident.reference for incident in conflicts],
            },
        )


async def _record_start_request(
    session: AsyncSession,
    *,
    group: IncidentGroup,
    actor_id: str,
    idempotency_key: str | None,
) -> str:
    existing = (
        await session.execute(
            select(DecisionLog)
            .where(
                DecisionLog.event_type == SCENARIO_START_REQUESTED,
                DecisionLog.correlation_id == group.reference,
            )
            .order_by(DecisionLog.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str((existing.detail or {}).get("actor_id") or actor_id)

    recorded_at = datetime.now(UTC)
    session.add(
        DecisionLog(
            incident_id=None,
            occurred_at=recorded_at,
            stage=SCENARIO_STAGE,
            actor="human",
            event_type=SCENARIO_START_REQUESTED,
            summary=f"Operator requested scenario start {group.reference}",
            detail={
                "scenario_reference": group.reference,
                "group_id": group.id,
                "actor_id": actor_id,
                "idempotency_digest": (
                    _idempotency_digest(idempotency_key) if idempotency_key else None
                ),
                "request_correlation_id": correlation_id_var.get(),
            },
            correlation_id=group.reference,
        )
    )
    await session.commit()
    return actor_id


@router.post(
    "/scenarios",
    response_model=ScenarioCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and persist an authored disruption scenario",
)
async def create_scenario(
    payload: ScenarioCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> ScenarioCreateResponse:
    """Validate and persist a scenario without opening or advancing any incident."""
    fingerprint = _request_fingerprint(payload)
    lock_identity = (
        f"scenario-create:{_idempotency_digest(idempotency_key)}" if idempotency_key else None
    )
    async with _operation_lock(session, lock_identity):
        if idempotency_key:
            replay = await _recorded_create(session, idempotency_key, fingerprint)
            if replay is not None:
                return replay

        flights = await _validated_flights(
            session, airport_icao=payload.airport_icao, members=payload.members
        )
        reference = _new_reference(payload.effective_at)
        recorded_at = datetime.now(UTC)
        source_ref = f"scenario-builder:{reference}"

        group = IncidentGroup(
            reference=reference,
            root_cause=payload.root_cause,
            airport_icao=payload.airport_icao,
            severity=payload.severity,
            state=IncidentState.detected,
            opened_at=payload.effective_at.astimezone(UTC),
            demo_dataset_id=_dataset_id(reference, flights),
        )
        session.add(group)
        await session.flush()

        session.add_all(
            [
                IncidentGroupFlight(
                    incident_group_id=group.id,
                    flight_id=member.flight_id,
                    role=member.role,
                    delay_minutes_at_injection=member.delay_minutes,
                    provenance_kind=ProvenanceKind.simulated,
                    source_ref=source_ref,
                )
                for member in payload.members
            ]
        )
        await session.flush()

        response = ScenarioCreateResponse(
            scenario_reference=reference,
            state=IncidentState.detected,
            root_cause=payload.root_cause,
            airport_icao=payload.airport_icao,
            severity=payload.severity,
            effective_at=payload.effective_at.astimezone(UTC),
            members=[
                ScenarioMemberOut(
                    flight_id=member.flight_id,
                    flight_number=flights[member.flight_id].flight_number,
                    role=member.role,
                    delay_minutes=member.delay_minutes,
                )
                for member in payload.members
            ],
            created_by=payload.actor_id,
            created_at=recorded_at,
            provenance=_provenance(reference),
        )
        session.add(
            DecisionLog(
                incident_id=None,
                occurred_at=recorded_at,
                stage=SCENARIO_STAGE,
                actor="human",
                event_type=SCENARIO_CREATED,
                summary=f"Operator created scenario {reference}",
                detail={
                    "scenario_reference": reference,
                    "group_id": group.id,
                    "actor_id": payload.actor_id,
                    "provenance_kind": ProvenanceKind.simulated.value,
                    "source_ref": source_ref,
                    "idempotency_digest": (
                        _idempotency_digest(idempotency_key) if idempotency_key else None
                    ),
                    "request_fingerprint": fingerprint,
                    "result": response.model_dump(mode="json"),
                },
                correlation_id=reference,
            )
        )
        await session.commit()
        log.info(
            "scenario_created",
            scenario_reference=reference,
            members=len(payload.members),
            actor=payload.actor_id,
            outcome="created",
        )
        return response


@router.post(
    "/scenarios/{scenario_reference}/start",
    response_model=ScenarioStartResponse,
    summary="Start the existing TravelOps workflow for a persisted scenario",
)
async def start_scenario(
    scenario_reference: str,
    payload: ScenarioStartRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> ScenarioStartResponse:
    """Open one canonical incident per declared member; never execute actions here."""
    group, _created = await _scenario(session, scenario_reference)
    flight_ids = await _member_flight_ids(session, group.id)
    lock_identities = [
        f"scenario-start:{scenario_reference}",
        *(f"scenario-flight:{flight_id}" for flight_id in flight_ids),
    ]
    async with _operation_locks(session, lock_identities):
        replay = await _recorded_start(session, scenario_reference)
        if replay is not None:
            return replay

        await _assert_no_foreign_active_incidents(session, group=group, flight_ids=flight_ids)
        started_by = await _record_start_request(
            session,
            group=group,
            actor_id=payload.actor_id,
            idempotency_key=idempotency_key,
        )

        orchestrator = GroupOrchestrator(session)
        context = await orchestrator.open_group(
            group.id,
            correlation_id=correlation_id_var.get(),
            opened_at=group.opened_at,
        )
        returned_ids = [member.incident_id for member in context.members if member.incident_id]
        await _assert_opened_incident_ownership(session, group=group, incident_ids=returned_ids)

        recorded_at = datetime.now(UTC)
        response = ScenarioStartResponse(
            scenario_reference=group.reference,
            state=context.state,
            members=[GroupMemberOut(**vars(member)) for member in context.members],
            opened_incident_ids=list(context.opened_incident_ids),
            blocked_reason=context.blocked_reason,
            awaiting_approval_count=await orchestrator.awaiting_approval_count(group.id),
            started_by=started_by,
            started_at=recorded_at,
            provenance=_provenance(group.reference),
        )
        session.add(
            DecisionLog(
                incident_id=None,
                occurred_at=recorded_at,
                stage=SCENARIO_STAGE,
                actor="human",
                event_type=SCENARIO_STARTED,
                summary=f"Operator started scenario {group.reference}",
                detail={
                    "scenario_reference": group.reference,
                    "group_id": group.id,
                    "actor_id": started_by,
                    "idempotency_digest": (
                        _idempotency_digest(idempotency_key) if idempotency_key else None
                    ),
                    "result": response.model_dump(mode="json"),
                },
                correlation_id=group.reference,
            )
        )
        await session.commit()
        log.info(
            "scenario_started",
            scenario_reference=group.reference,
            incidents=len(context.opened_incident_ids),
            actor=started_by,
            outcome="started",
        )
        return response
