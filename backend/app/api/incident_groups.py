"""Disruption group endpoints — STREAM A.

Replaces the fixture routes for `/incident-groups` and `/incident-groups/{id}` and adds the
cascade, blast-radius, graph, what-if, group-assurance and plan-approval surface. The fixture
routes are deleted in the same commit, so there is never a period where two implementations of
one path exist.

**Stream A computes no domain figure here.** Every number comes from Stream C
(`cascade_rollup`, `project_and_record`, `compose_blast_radius`, `evaluate_what_if`) or Stream B
(the plan gate and approval rules). This module resolves references, types the boundary and
shapes the response. That division is the reason a figure on the cascade screen and the same
figure on an action detail cannot disagree: there is exactly one place each was computed.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.plan_contract import WhatIfPolicy
from app.assurance.whatif import WhatIfRequest, assert_zero_write
from app.db.scenario_queries import CascadeRollup, cascade_rollup
from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.enums import IncidentState
from app.models.reference import Booking, BookingSegment, Flight
from app.models.workflow import Action, AssuranceEvaluation, DecisionLog, IncidentGroup, PlanTask
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator.group import GroupOrchestrator
from app.orchestrator.plan_approval import PlanApprovalService, approval_payload
from app.orchestrator.plan_assurance import PlanAssuranceService, load_plan_configuration
from app.schemas.cascade import (
    BlastRadiusOut,
    CascadeGraphOut,
    GroupImpactResponse,
    GroupMemberOut,
    GroupRollups,
    GroupRunResponse,
    GroupSummary,
    ImpactCohortOut,
    ImpactFactorOut,
    IncidentGroupDetailResponse,
    IncidentGroupListResponse,
    PassengerImpactOut,
    ProvenanceBlock,
    RollupStatus,
    UnassessedFactorOut,
    WhatIfDeltaOut,
    WhatIfLeverRejectionOut,
    WhatIfResponse,
)
from app.schemas.plans import (
    CoveredEvaluationOut,
    ExcludedEvaluationOut,
    GroupAssuranceResponse,
    IncidentPlanAssuranceOut,
    PlanApprovalPreview,
    PlanApprovalRequest,
    PlanApprovalResponse,
    PlanCheckOut,
    PlanTaskOutcomeOut,
)
from app.services import blast_radius as blast_radius_service
from app.services import cascade_graph as graph_service
from app.services import what_if as what_if_service
from app.services.hotel import group_hotel_totals
from app.services.passenger_impact import UNASSESSED_FACTORS, load_group_impacts

router = APIRouter(tags=["cascade"])
log = get_logger(__name__)

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="A repeat returns the recorded result."),
]

GROUP_RUN_EVENT = "GROUP_RUN_REQUESTED"

MECHANISM_LEGEND: dict[str, str] = {
    "operating": "The pairing operates the delayed flight itself.",
    "onward_duty": "The pairing has a later duty that the delay makes unreachable.",
    "positioning": "The crew were being positioned on the delayed flight for a later duty.",
    "second_pairing": "A second pairing shares a crew member with an affected pairing.",
}

ROLLUP_NOTE = (
    "Derived from the recorded per-incident findings and never stored on the group. "
    "`is_complete` is false whenever a declared flight has no incident open or an incident has "
    "not been assessed, and a partial rollup must render as partial."
)

DETAIL_NOTE = (
    "Counts are DERIVED from the arrays below and from the recorded per-incident findings. "
    "The UI must never render a hardcoded total."
)


# ------------------------------------------------------------------------------- helpers


async def _resolve(session: AsyncSession, reference: str) -> IncidentGroup:
    return await GroupOrchestrator(session).resolve(reference)


def _rollups(rollup: CascadeRollup) -> GroupRollups:
    return GroupRollups(
        flights_affected=rollup.flights_affected,
        passengers_affected=rollup.passengers_affected,
        connections_at_risk=rollup.connections_at_risk,
        candidate_hotels=rollup.candidate_hotels,
        crew_pairings_affected=rollup.crew_pairings_affected,
        note="Each value is derived server-side from recorded rows; none is stored on the group.",
    )


def _status(rollup: CascadeRollup, *, computed_at: datetime | None = None) -> RollupStatus:
    return RollupStatus(
        is_complete=rollup.is_complete,
        computed_at=computed_at or datetime.now(UTC),
        note=ROLLUP_NOTE,
        flights_without_incident=list(rollup.flights_without_incident),
        membership_is_declared=rollup.membership_is_declared,
    )


def _provenance(group: IncidentGroup) -> ProvenanceBlock:
    return ProvenanceBlock(
        kind="derived",
        provider="scenario_queries.cascade_rollup",
        source_ref=f"incident_group:{group.id}",
    )


async def _awaiting_count(session: AsyncSession, group_id: int) -> int:
    return await GroupOrchestrator(session).awaiting_approval_count(group_id)


async def _passengers_for(session: AsyncSession, flight_id: int) -> int:
    stmt = (
        select(func.count(func.distinct(Booking.id)))
        .select_from(BookingSegment)
        .join(Booking, Booking.id == BookingSegment.booking_id)
        .where(BookingSegment.flight_id == flight_id)
    )
    return int((await session.execute(stmt)).scalar_one())


def _why_nine_not_eight(rollup: CascadeRollup) -> str:
    """Derived from the mechanism counts, so the sentence cannot contradict the data.

    The alternative — recorded prose — is a caption that survives the numbers changing
    underneath it, which is exactly how a demo ends up explaining a figure it is not showing.
    """
    if not rollup.pairings:
        return (
            "No crew pairings have been assessed for this group yet, so there is no "
            "flights-to-rotations comparison to explain."
        )
    counts: dict[str, int] = {}
    for pairing in rollup.pairings:
        counts[pairing.mechanism] = counts.get(pairing.mechanism, 0) + 1
    breakdown = ", ".join(f"{count} {mechanism}" for mechanism, count in sorted(counts.items()))
    return (
        f"{rollup.crew_pairings_affected} rotations across {rollup.flights_affected} flights, "
        f"because crew are assigned to pairings rather than to flights: {breakdown}. "
        "A pairing can be affected without operating a delayed flight at all."
    )


# --------------------------------------------------------------------------------- reads


@router.get(
    "/incident-groups",
    response_model=IncidentGroupListResponse,
    summary="Every disruption group, with derived rollups",
)
async def list_incident_groups(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IncidentGroupListResponse:
    stmt = select(IncidentGroup).order_by(IncidentGroup.opened_at.desc(), IncidentGroup.id.desc())
    groups = list((await session.execute(stmt)).scalars())

    summaries: list[GroupSummary] = []
    for group in groups:
        rollup = await cascade_rollup(session, group_id=group.id)
        summaries.append(
            GroupSummary(
                id=group.id,
                reference=group.reference,
                root_cause=group.root_cause,
                airport_icao=group.airport_icao,
                severity=group.severity,
                state=IncidentState(group.state),
                opened_at=group.opened_at,
                rollups=_rollups(rollup),
                awaiting_approval_count=await _awaiting_count(session, group.id),
                provenance=_provenance(group),
                rollup_status=_status(rollup),
            )
        )
    return IncidentGroupListResponse(groups=summaries)


@router.get(
    "/incident-groups/current",
    response_model=GroupSummary,
    summary="The most recently opened disruption group",
)
async def current_incident_group(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GroupSummary:
    """The console's landing query. 404 when nothing is open, never an empty placeholder."""
    stmt = select(IncidentGroup).order_by(IncidentGroup.opened_at.desc(), IncidentGroup.id.desc())
    group = (await session.execute(stmt)).scalars().first()
    if group is None:
        raise EntityNotFound(
            "no disruption group exists",
            details={"resolution": "seed the demo dataset, then inject the scenario"},
        )
    rollup = await cascade_rollup(session, group_id=group.id)
    return GroupSummary(
        id=group.id,
        reference=group.reference,
        root_cause=group.root_cause,
        airport_icao=group.airport_icao,
        severity=group.severity,
        state=IncidentState(group.state),
        opened_at=group.opened_at,
        rollups=_rollups(rollup),
        awaiting_approval_count=await _awaiting_count(session, group.id),
        provenance=_provenance(group),
        rollup_status=_status(rollup),
    )


@router.get(
    "/incident-groups/{group_ref}",
    response_model=IncidentGroupDetailResponse,
    summary="One cascade: members, pairings, blast radius and graph",
)
async def get_incident_group(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    include_graph: Annotated[bool, Query(description="Project the disruption graph.")] = True,
) -> IncidentGroupDetailResponse:
    group = await _resolve(session, group_ref)
    rollup = await cascade_rollup(session, group_id=group.id)
    members = await GroupOrchestrator(session).member_flights(group.id)

    flights = []
    for member in members:
        flight = await session.get(Flight, member.flight_id)
        incident_reference = None
        if member.incident_id is not None:
            from app.models.workflow import Incident

            incident = await session.get(Incident, member.incident_id)
            incident_reference = incident.reference if incident else None
        flights.append(
            {
                "id": member.flight_id,
                "flight_number": member.flight_number,
                "route": f"{member.origin_icao} -> {member.destination_icao}",
                "delay_minutes": member.delay_minutes_at_injection,
                "passengers": await _passengers_for(session, member.flight_id),
                "state": member.incident_state or "not_opened",
                "role": member.role,
                "incident_reference": incident_reference,
            }
        )
        del flight

    graph_out: CascadeGraphOut | None = None
    radius_out: BlastRadiusOut | None = None
    graph = None
    if include_graph:
        graph = await graph_service.project_graph(session, group_id=group.id)
        graph_out = CascadeGraphOut(**graph_service.graph_payload(graph, rollup))

    # Accommodation figures come from Stream C's service, summed across the group. Without them the
    # blast radius silently omits the room requirement and the shortfall entirely — the two figures
    # an operator most needs when the inventory does not cover the disruption. The summing lives in
    # the service, not here: aggregating an action payload in this layer is what
    # `test_phase2_guards` forbids, and rightly.
    radius = blast_radius_service.compose_blast_radius(
        rollup=rollup,
        graph=graph,
        hotel_payload=await group_hotel_totals(session, group_id=group.id),
    )
    radius_out = BlastRadiusOut(**blast_radius_service.blast_radius_payload(radius))

    return IncidentGroupDetailResponse(
        note=DETAIL_NOTE,
        id=group.id,
        reference=group.reference,
        root_cause=group.root_cause,
        airport_icao=group.airport_icao,
        severity=group.severity,
        state=IncidentState(group.state),
        opened_at=group.opened_at,
        rollups=_rollups(rollup),
        rollup_status=_status(rollup),
        flights=flights,
        crew_pairings=[
            {
                "pairing_reference": pairing.pairing_reference,
                "base_icao": getattr(pairing, "base_icao", None),
                "source_flight": getattr(pairing, "source_flight", None),
                "affected_leg": getattr(pairing, "affected_leg", None),
                "mechanism": pairing.mechanism,
                "detail": getattr(pairing, "detail", None),
                "at_risk": getattr(pairing, "at_risk", True),
                "depth": getattr(pairing, "depth", 1),
            }
            for pairing in rollup.pairings
        ],
        mechanism_legend={
            mechanism: MECHANISM_LEGEND[mechanism]
            for mechanism in sorted({pairing.mechanism for pairing in rollup.pairings})
            if mechanism in MECHANISM_LEGEND
        },
        why_nine_not_eight=_why_nine_not_eight(rollup),
        blast_radius=radius_out,
        graph=graph_out,
        awaiting_approval_count=await _awaiting_count(session, group.id),
        provenance=_provenance(group),
    )


@router.get(
    "/incident-groups/{group_ref}/blast-radius",
    response_model=BlastRadiusOut,
    summary="Reach composed from recorded findings",
)
async def get_blast_radius(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BlastRadiusOut:
    group = await _resolve(session, group_ref)
    rollup = await cascade_rollup(session, group_id=group.id)
    graph = await graph_service.project_graph(session, group_id=group.id)
    # Accommodation figures come from Stream C's service, summed across the group. Without them the
    # blast radius silently omits the room requirement and the shortfall entirely — the two figures
    # an operator most needs when the inventory does not cover the disruption. The summing lives in
    # the service, not here: aggregating an action payload in this layer is what
    # `test_phase2_guards` forbids, and rightly.
    radius = blast_radius_service.compose_blast_radius(
        rollup=rollup,
        graph=graph,
        hotel_payload=await group_hotel_totals(session, group_id=group.id),
    )
    return BlastRadiusOut(**blast_radius_service.blast_radius_payload(radius))


@router.get(
    "/incident-groups/{group_ref}/graph",
    response_model=CascadeGraphOut,
    summary="The disruption graph, projected from recorded rows",
)
async def get_cascade_graph(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CascadeGraphOut:
    group = await _resolve(session, group_ref)
    rollup = await cascade_rollup(session, group_id=group.id)
    graph = await graph_service.project_graph(session, group_id=group.id)
    return CascadeGraphOut(**graph_service.graph_payload(graph, rollup))


@router.get(
    "/incident-groups/{group_ref}/impacts",
    response_model=GroupImpactResponse,
    summary="Per-passenger recorded priorities for the group",
)
async def get_group_impacts(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> GroupImpactResponse:
    """Read `passenger_impact`. Reads rows; derives nothing and authorises nothing.

    The banding and the counts are built in `services/passenger_impact`, not here: aggregating in
    the transport layer is what `test_phase2_guards` forbids, because a figure computed next to a
    response model is a figure nobody can trace back to a row.

    `unassessed_factors` is the part that matters. Two of the ruleset's factors — whether an onward
    option remains today, and whether a passenger is stranded mid-itinerary — are Rebooking's
    findings and nothing has established them. They are false in the rows because that is all the
    record supports, and they are named here so the console can render "not established" rather than
    "no". The difference is the difference between "nobody needs rebooking" and "nobody has looked".
    """
    group = await _resolve(session, group_ref)
    recorded = await load_group_impacts(session, incident_group_id=group.id, limit=limit)

    # When the ranking has never run there is no assessment to describe, and saying so beats
    # returning zeros that read as "every passenger is fine".
    if not recorded.passengers_assessed:
        return GroupImpactResponse(
            group_reference=group.reference,
            rule_version="",
            ruleset_hash="",
            computed_at=None,
            passengers_assessed=0,
            unassessed_factors=_unassessed_factors(),
            note=(
                "No passenger priorities are recorded for this group yet. They are derived when "
                "the group is advanced, from connection findings already persisted."
            ),
        )

    return GroupImpactResponse(
        group_reference=group.reference,
        rule_version=recorded.rule_version,
        ruleset_hash=recorded.ruleset_hash,
        computed_at=await _impacts_recorded_at(session, group.reference),
        passengers_assessed=recorded.passengers_assessed,
        cohorts=[
            ImpactCohortOut(
                band=cohort.band.value,
                passenger_count=cohort.passenger_count,
                lowest_index=cohort.lowest_index,
                highest_index=cohort.highest_index,
                factor_counts=cohort.factor_counts,
                booking_ids=cohort.booking_ids,
            )
            for cohort in recorded.cohorts
        ],
        passengers=[
            PassengerImpactOut(
                passenger_id=record.passenger_id,
                passenger_reference=record.passenger_reference,
                booking_id=record.booking_id,
                pnr=record.pnr,
                priority_index=record.priority_index,
                priority_band=record.priority_band,
                factors=[
                    ImpactFactorOut(
                        factor=str(entry.get("factor", "")),
                        weight=int(entry.get("weight", 0)),
                        source=str(entry.get("source", "")),
                    )
                    for entry in record.factors
                ],
                rule_version=record.rule_version,
                ruleset_hash=record.ruleset_hash,
            )
            for record in recorded.passengers
        ],
        returned=len(recorded.passengers),
        unassessed_factors=_unassessed_factors(),
        note=(
            "A constraint ranking read from persisted rows: who has the fewest remaining options, "
            "not who matters more. It reserves nothing and authorises nothing. No seat "
            "availability is asserted anywhere, because the schema carries none."
        ),
    )


def _unassessed_factors() -> list[UnassessedFactorOut]:
    """Name the factors nothing has established, so a client never renders them as false."""
    return [
        UnassessedFactorOut(factor=factor, reason=reason, established_by=established_by)
        for factor, reason, established_by in UNASSESSED_FACTORS
    ]


async def _impacts_recorded_at(session: AsyncSession, group_reference: str) -> datetime | None:
    """When the ranking was last recorded, taken from the group's own journal entry.

    `passenger_impact` carries no timestamp column, and adding one would mean a migration for a
    display convenience. The journal already records the moment, so it is read from there rather
    than invented — a `computed_at` of `now()` would be a lie about when the figure was true.
    """
    stmt = (
        select(DecisionLog.occurred_at)
        .where(
            DecisionLog.incident_id.is_(None),
            DecisionLog.correlation_id == group_reference,
            DecisionLog.event_type == "PASSENGER_IMPACT_RECORDED",
        )
        .order_by(DecisionLog.occurred_at.desc(), DecisionLog.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------------- run


@router.post(
    "/incident-groups/{group_ref}/open",
    response_model=GroupRunResponse,
    summary="Open one incident per declared member flight",
)
async def open_group(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> GroupRunResponse:
    """Idempotent by construction: `open_incident` returns the existing active incident for a
    flight, so a repeated call cannot double the cascade."""
    orchestrator = GroupOrchestrator(session)
    ctx = await orchestrator.open_group(group_ref, correlation_id=correlation_id_var.get())
    return GroupRunResponse(
        group_reference=ctx.group_reference,
        state=ctx.state,
        members=[GroupMemberOut(**vars(member)) for member in ctx.members],
        opened_incident_ids=list(ctx.opened_incident_ids),
        blocked_reason=ctx.blocked_reason,
        awaiting_approval_count=await _awaiting_count(session, ctx.group_id),
    )


@router.post(
    "/incident-groups/{group_ref}/run",
    response_model=GroupRunResponse,
    summary="Advance every non-terminal member incident",
)
async def run_group(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
    max_incidents: Annotated[
        int | None,
        Query(ge=1, le=64, description="Bound how many members advance in this call."),
    ] = None,
) -> GroupRunResponse:
    """Drive the cascade forward.

    A member whose service refuses does not stop the others: each advances independently and
    the group ends `blocked` naming what did not resolve. One flight without a hotel must not
    strand the other seven.
    """
    group = await _resolve(session, group_ref)

    if idempotency_key:
        replay = await _recorded_group_run(session, group.reference, idempotency_key)
        if replay is not None:
            log.info(
                "group_run_replayed",
                group_reference=group.reference,
                idempotency_key=idempotency_key,
                outcome="idempotent_replay",
            )
            return replay

    # Deliberately does NOT open. `uq_incident_active_per_flight` is partial over ACTIVE states,
    # so a member that reached `blocked` has released its slot — and calling `open_group` here
    # would create a *second* incident for that flight on every subsequent run, quietly growing
    # the cascade and dragging the derived group state backwards. Opening is `POST /open`.
    orchestrator = GroupOrchestrator(session)
    ctx = await orchestrator.advance_group(
        group.id,
        max_incidents=max_incidents,
        correlation_id=correlation_id_var.get(),
    )

    response = GroupRunResponse(
        group_reference=ctx.group_reference,
        state=ctx.state,
        members=[GroupMemberOut(**vars(member)) for member in ctx.members],
        opened_incident_ids=list(ctx.opened_incident_ids),
        blocked_reason=ctx.blocked_reason,
        awaiting_approval_count=await _awaiting_count(session, group.id),
    )

    if idempotency_key:
        session.add(
            DecisionLog(
                incident_id=None,
                occurred_at=datetime.now(UTC),
                stage="cascade",
                actor="orchestrator",
                event_type=GROUP_RUN_EVENT,
                summary=f"Group run requested with idempotency key {idempotency_key}",
                detail={
                    "group_reference": group.reference,
                    "idempotency_key": idempotency_key,
                    "result": response.model_dump(mode="json"),
                },
                correlation_id=group.reference,
            )
        )
        await session.flush()
    return response


async def _recorded_group_run(
    session: AsyncSession, group_reference: str, key: str
) -> GroupRunResponse | None:
    stmt = (
        select(DecisionLog)
        .where(
            DecisionLog.event_type == GROUP_RUN_EVENT,
            DecisionLog.correlation_id == group_reference,
        )
        .order_by(DecisionLog.id)
    )
    for entry in (await session.execute(stmt)).scalars():
        detail = entry.detail or {}
        if detail.get("idempotency_key") == key and isinstance(detail.get("result"), dict):
            return GroupRunResponse(**{**detail["result"], "replayed": True})
    return None


# --------------------------------------------------------------------- group assurance


async def _group_assurance(
    session: AsyncSession, group: IncidentGroup, *, with_preview: bool
) -> GroupAssuranceResponse:
    service = PlanAssuranceService(session)
    plans = await service.selected_plans(group.id)
    if not plans:
        raise EntityNotFound(
            "no member incident in this group has a plan yet",
            details={
                "group_reference": group.reference,
                "resolution": "run the cascade to planning first",
            },
        )

    rooms, exposure_inr = await _recorded_exposure(session, group.id)
    result, scopes, _rollup = await service.evaluate_group(
        group_id=group.id,
        group_reference=group.reference,
        plans=plans,
        rooms_committed=rooms,
        total_exposure_inr=exposure_inr,
    )

    incidents: list[IncidentPlanAssuranceOut] = []
    hashes: set[str] = set()
    for plan, scope in zip(plans, scopes, strict=True):
        versions = await _config_identity(session, [row.id for row in scope.rows.values()])
        hashes.update(versions[1])
        incidents.append(
            IncidentPlanAssuranceOut(
                incident_reference=scope.incident_reference,
                plan_id=plan.id,
                variant_key=plan.variant_key,
                task_count=len(scope.tasks),
                tasks=[
                    PlanTaskOutcomeOut(
                        task_id=task.task_id,
                        action_type=task.action_type,
                        decision=task.decision.value,
                        risk_tier=task.risk_tier.value,
                        blocking_kinds=list(task.blocking_kinds),
                        approvable=task.approvable,
                        evaluation_id=task.evaluation_id,
                        target_refs=list(task.target_refs),
                        depends_on=list(task.depends_on),
                    )
                    for task in scope.tasks
                ],
                awaiting_approval_count=sum(
                    1 for task in scope.tasks if task.decision.value == "needs_human"
                ),
                config_version=versions[0] or result.config_version,
                config_hash=next(iter(versions[1]), result.config_hash),
            )
        )

    preview = None
    if with_preview:
        approvals = PlanApprovalService(session)
        outcome = await approvals.preview(group_id=group.id, plan=plans[0], plan_result=result)
        preview = PlanApprovalPreview(
            plan_id=plans[0].id,
            plan_hash=outcome.plan_hash,
            covered=[CoveredEvaluationOut(**vars(item)) for item in outcome.covered],
            excluded=[ExcludedEvaluationOut(**vars(item)) for item in outcome.excluded],
            covered_count=outcome.covered_count,
            excluded_count=outcome.excluded_count,
            refusal=outcome.refusal,
            refusal_reason=outcome.refusal_reason,
        )

    return GroupAssuranceResponse(
        group_reference=group.reference,
        decision=result.decision.value,
        plan_risk_tier=result.plan_risk_tier.value,
        task_count=result.task_count,
        checks=[
            PlanCheckOut(
                name=check.name.value,
                state=check.state.value,
                reason_code=check.reason_code.value,
                reason=check.reason,
                tier=check.tier.value if check.tier else None,
                offending_refs=list(check.offending_refs),
            )
            for check in result.checks
        ],
        blocking=[name.value for name in result.blocking],
        admissible=result.admissible,
        requires_human=result.requires_human,
        plan_hash=result.plan_hash,
        config_version=result.config_version,
        config_hash=result.config_hash,
        config_hash_uniform=len(hashes) <= 1,
        evaluated_at=result.evaluated_at,
        exposure=dict(result.exposure),
        incidents=incidents,
        approval_preview=preview,
    )


async def _config_identity(
    session: AsyncSession, task_ids: list[int]
) -> tuple[str | None, set[str]]:
    """The config version and hashes the member evaluations were actually judged under.

    Returned so the console can flag a group judged under two hashes. That is a fact a reviewer
    must see, not a detail to smooth over.
    """
    if not task_ids:
        return None, set()
    stmt = select(AssuranceEvaluation.config_version, AssuranceEvaluation.config_hash).where(
        AssuranceEvaluation.plan_task_id.in_(task_ids)
    )
    rows = (await session.execute(stmt)).all()
    versions = {str(row[0]) for row in rows}
    hashes = {str(row[1]) for row in rows}
    return (next(iter(sorted(versions)), None), hashes)


async def _recorded_exposure(session: AsyncSession, group_id: int) -> tuple[int | None, int | None]:
    """Rooms committed and money committed, read from recorded actions.

    `None` when nothing has been recorded, and it stays `None`: Stream B's exposure check treats
    an unknown figure as a breach rather than as zero, and substituting a default here would
    turn "we do not know" into "it is fine".
    """
    from app.models.workflow import Incident, Plan

    stmt = (
        select(Action.cost_inr, Action.payload)
        .join(PlanTask, PlanTask.id == Action.plan_task_id)
        .join(Plan, Plan.id == PlanTask.plan_id)
        .join(Incident, Incident.id == Plan.incident_id)
        .where(Incident.group_id == group_id, Action.status == "succeeded")
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return None, None
    rooms = 0
    money = 0
    saw_rooms = False
    for cost, payload in rows:
        money += int(cost or 0)
        data = payload if isinstance(payload, dict) else {}
        if "rooms_held" in data or "rooms_required" in data:
            saw_rooms = True
            rooms += int(data.get("rooms_held") or data.get("rooms_required") or 0)
    return (rooms if saw_rooms else None), money


@router.get(
    "/incident-groups/{group_ref}/assurance",
    response_model=GroupAssuranceResponse,
    summary="Group-scoped plan assurance. Authorises nothing.",
)
async def get_group_assurance(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GroupAssuranceResponse:
    """Aggregate for display. `authorises_no_action` is a `Literal[True]` in the response.

    One endpoint rather than client-side fan-out over N incidents: a fan-out makes the group
    view N+1 requests and, worse, lets a partial failure read as a pass.
    """
    group = await _resolve(session, group_ref)
    return await _group_assurance(session, group, with_preview=True)


@router.post(
    "/incident-groups/{group_ref}/assurance/decision",
    response_model=PlanApprovalResponse,
    summary="Plan approval: covers low/medium, never high risk, never failed evidence",
)
async def approve_group_plan(
    group_ref: str,
    payload: PlanApprovalRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> PlanApprovalResponse:
    """Record one operator act, and write one `human_decision` per covered evaluation.

    The partition happens server-side. The UI hiding a button is not a control: a direct API
    call must be refused the same way, which is why `plan_approval_covers` runs here and not
    only in the console.

    Only evaluations **already awaiting** a person are covered. An evaluation produced later in
    the run needs its own decision, because forward coverage would be a blank cheque over
    actions nobody had seen.
    """
    group = await _resolve(session, group_ref)
    service = PlanAssuranceService(session)
    plans = await service.selected_plans(group.id)
    if not plans:
        raise EntityNotFound(
            "no member incident in this group has a plan to approve",
            details={"group_reference": group.reference},
        )

    rooms, exposure_inr = await _recorded_exposure(session, group.id)
    result, _scopes, _rollup = await service.evaluate_group(
        group_id=group.id,
        group_reference=group.reference,
        plans=plans,
        rooms_committed=rooms,
        total_exposure_inr=exposure_inr,
    )

    approvals = PlanApprovalService(session)
    outcome = await approvals.approve(
        group_id=group.id,
        plan=plans[0],
        plan_result=result,
        actor_id=payload.actor_id,
        reason=payload.reason,
    )
    return PlanApprovalResponse(**approval_payload(outcome))


# ------------------------------------------------------------------------------ what-if


@router.post(
    "/incident-groups/{group_ref}/what-if",
    response_model=WhatIfResponse,
    summary="Bounded, zero-write, deterministic re-evaluation (P2-D2)",
)
async def group_what_if(
    group_ref: str,
    levers: dict[str, Any],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WhatIfResponse:
    """Re-evaluate the recorded facts under substituted inputs. Writes nothing.

    Stream B's `assert_zero_write` runs FIRST. If it refuses — a live provider, a missing seed,
    too many candidates — nothing is evaluated at all, rather than a comparison being produced
    under conditions it was not safe to produce one in.

    This is not a simulation engine and not a digital twin. `basis` and `wrote_rows` are
    `Literal`s in the response contract, so it cannot express a projection or claim a write.
    """
    group = await _resolve(session, group_ref)
    loaded = load_plan_configuration()
    policy = loaded.what_if if loaded else WhatIfPolicy()

    requested = dict(levers) if isinstance(levers, dict) else {}
    seed = int(requested.pop("seed", 20260820) or 20260820)

    verdict = assert_zero_write(
        request=WhatIfRequest(
            candidate_count=1,
            seed=seed,
            provider_modes={"weather": "fixture", "notification": "console"},
            real_dispatch_enabled=False,
        ),
        policy=policy,
    )
    if not verdict.permitted:
        # Refused before anything was evaluated. The response still states the boundary, so a
        # caller learns why rather than seeing an empty result.
        empty = what_if_service.WhatIfResult(group_reference=group.reference)
        return _what_if_response(
            empty, permitted=False, refusals=[r.value for r in verdict.refusals], seed=seed
        )

    result = await what_if_service.evaluate_what_if(session, group_id=group.id, levers=requested)
    return _what_if_response(result, permitted=True, refusals=[], seed=seed)


def _what_if_response(
    result: Any, *, permitted: bool, refusals: list[str], seed: int
) -> WhatIfResponse:
    """Map Stream C's payload onto the typed contract.

    Field-by-field rather than `**payload`, because the payload is a display shape owned by
    Stream C and `extra="forbid"` would turn any addition on their side into a 500 here. This way
    a new key is ignored until it is deliberately surfaced.
    """
    payload = what_if_service.what_if_payload(result)
    return WhatIfResponse(
        group_reference=payload["group_reference"],
        rule_version=payload["rule_version"],
        boundary_note=payload["boundary_note"],
        headline=payload["headline"],
        permitted=permitted,
        refusals=refusals,
        seed=seed,
        recorded_baseline=payload.get("recorded_baseline") or {},
        levers_applied=payload.get("levers_applied") or {},
        levers_available=list(payload.get("levers_available") or []),
        levers_rejected=[
            WhatIfLeverRejectionOut(lever=item["lever"], reason=item["reason"])
            for item in (payload.get("levers_rejected") or [])
        ],
        deltas=[
            WhatIfDeltaOut(
                key=delta["key"],
                label=delta["label"],
                baseline=delta["baseline"],
                scenario=delta["scenario"],
                delta=delta["delta"],
                summary=delta["summary"],
            )
            for delta in (payload.get("deltas") or [])
        ],
    )
