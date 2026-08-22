"""Group-scope API — STREAM A.

Every route a network disruption needs, served from the database. These replace the two Wave 0
fixture routes, which were deleted in the same commit so there is never a period where two
implementations of one path exist.

The layer is a **pass-through**. Nothing here computes a figure: rollups come from
`scenario_queries.cascade_rollup`, the graph from `cascade_graph`, the blast radius from
`blast_radius`, plan assurance from Stream B's gate, what-if from `what_if`. If a number needed
calculating, it would mean business logic had leaked into the transport layer, and the same
number would then exist in two places with nothing to keep them equal.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.plan_contract import PlanAssuranceResult
from app.db.scenario_queries import CascadeRollup, cascade_rollup, group_affected_flights
from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.cascade import PlanApprovalTier
from app.models.reference import Booking, BookingSegment
from app.models.workflow import (
    AssuranceEvaluation,
    DecisionLog,
    HumanDecision,
    Incident,
    IncidentGroup,
    Plan,
    PlanTask,
)
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator import plan_lifecycle
from app.orchestrator.group_engine import GroupOrchestrator, GroupRunResult
from app.schemas.groups import (
    BlastRadiusOut,
    CascadeGraphOut,
    GroupDetailResponse,
    GroupFlightRow,
    GroupListResponse,
    GroupRollups,
    GroupRunResponse,
    GroupSummary,
    PlanApprovalOut,
    PlanApprovalRequest,
    PlanAssuranceListResponse,
    PlanAssuranceOut,
    ReplayFrame,
    ReplayResponse,
    RollupStatus,
    WhatIfRequest,
    WhatIfResponse,
)
from app.services.blast_radius import blast_radius_payload, compose_blast_radius
from app.services.cascade_graph import graph_payload, project_graph
from app.services.what_if import evaluate_what_if, what_if_payload

router = APIRouter(tags=["incident-groups"])
log = get_logger(__name__)

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]

GROUP_RUN_EVENT = "GROUP_RUN_REQUESTED"

#: Mirrors the committed fixture, and the enum it is asserted against. The direct mechanisms
#: only: `downstream_flight` is reachable through bounded expansion and is not in the default
#: cascade, so listing it here would document something the projection does not show.
MECHANISM_LEGEND: dict[str, str] = {
    "operating": "Crew are working the affected flight",
    "onward_duty": "A later leg of the same pairing is now infeasible",
    "second_pairing": "Cockpit and cabin crew sit on different pairings",
    "positioning": "Crew were travelling as passengers to operate another flight",
}


async def _load_group(session: AsyncSession, group_id: str) -> IncidentGroup:
    """Accept a `GRP-...` reference or a numeric id, matching the incident routes."""
    if group_id.isdigit():
        group = await session.get(IncidentGroup, int(group_id))
    else:
        group = (
            (
                await session.execute(
                    select(IncidentGroup).where(IncidentGroup.reference == group_id)
                )
            )
            .scalars()
            .first()
        )
    if group is None:
        raise EntityNotFound(f"incident group '{group_id}' not found")
    return group


def _rollups(rollup: CascadeRollup) -> GroupRollups:
    return GroupRollups(
        flights_affected=rollup.flights_affected,
        passengers_affected=rollup.passengers_affected,
        connections_at_risk=rollup.connections_at_risk,
        candidate_hotels=rollup.candidate_hotels,
        crew_pairings_affected=rollup.crew_pairings_affected,
    )


def _rollup_status(rollup: CascadeRollup) -> RollupStatus:
    if not rollup.membership_is_declared:
        note = (
            "This group declares no member flights, so the flight count is derived from open "
            "incidents rather than from data."
        )
    elif rollup.is_complete:
        note = (
            f"All {rollup.incidents_in_group} incidents across "
            f"{len(rollup.member_flight_ids)} declared flights have been assessed for both "
            "connections and crew."
        )
    else:
        gaps = []
        if rollup.flights_without_incident:
            gaps.append(
                f"{len(rollup.flights_without_incident)} declared flights have no incident open"
            )
        missing_connections = rollup.incidents_in_group - rollup.incidents_assessed_connections
        if missing_connections > 0:
            gaps.append(f"{missing_connections} incidents have no connection assessment")
        missing_crew = rollup.incidents_in_group - rollup.incidents_assessed_crew
        if missing_crew > 0:
            gaps.append(f"{missing_crew} incidents have no crew assessment")
        note = "This is a partial answer and must be rendered as one: " + "; ".join(gaps) + "."
    return RollupStatus(
        is_complete=rollup.is_complete,
        computed_at=datetime.now(UTC),
        incidents_in_group=rollup.incidents_in_group,
        incidents_assessed_connections=rollup.incidents_assessed_connections,
        incidents_assessed_crew=rollup.incidents_assessed_crew,
        member_flight_ids=list(rollup.member_flight_ids),
        flights_without_incident=list(rollup.flights_without_incident),
        membership_is_declared=rollup.membership_is_declared,
        note=note,
    )


async def _awaiting_count(session: AsyncSession, group_id: int) -> int:
    """Evaluations the gate held across the group with no decision recorded yet."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(AssuranceEvaluation)
                .join(PlanTask, PlanTask.id == AssuranceEvaluation.plan_task_id)
                .join(Plan, Plan.id == PlanTask.plan_id)
                .join(Incident, Incident.id == Plan.incident_id)
                .outerjoin(HumanDecision, HumanDecision.assurance_id == AssuranceEvaluation.id)
                .where(
                    Incident.group_id == group_id,
                    AssuranceEvaluation.decision == "needs_human",
                    HumanDecision.id.is_(None),
                )
            )
        ).scalar_one()
    )


def _provenance(group: IncidentGroup) -> dict[str, Any]:
    return {
        "kind": "synthetic",
        "provider": "seeded_dataset",
        "source_ref": f"incident_group:{group.reference}",
        "note": (
            "Derived from persisted rows. Every figure is recomputable from the actions and "
            "predictions it names."
        ),
    }


# --------------------------------------------------------------------------- reads


@router.get(
    "/incident-groups",
    response_model=GroupListResponse,
    summary="Network disruptions with derived rollups",
)
async def list_incident_groups(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GroupListResponse:
    """Every group, each with its rollups derived and its completeness stated.

    The completeness flag is not decoration: a group whose figures are partial is the normal
    state mid-recovery, and a console that renders a partial rollup as a final one is
    misreporting the disruption.
    """
    groups = (
        (await session.execute(select(IncidentGroup).order_by(IncidentGroup.opened_at.desc())))
        .scalars()
        .all()
    )

    summaries: list[GroupSummary] = []
    for group in groups:
        rollup = await cascade_rollup(session, group_id=int(group.id))
        summaries.append(
            GroupSummary(
                id=int(group.id),
                reference=group.reference,
                root_cause=str(group.root_cause),
                airport_icao=group.airport_icao,
                severity=group.severity,
                state=str(group.state),
                opened_at=_as_utc(group.opened_at),
                rollups=_rollups(rollup),
                rollup_status=_rollup_status(rollup),
                awaiting_approval_count=await _awaiting_count(session, int(group.id)),
                provenance=_provenance(group),
            )
        )
    return GroupListResponse(groups=summaries)


@router.get(
    "/incident-groups/{group_id}",
    response_model=GroupDetailResponse,
    summary="Cascade detail: flights, crew, graph and blast radius",
)
async def get_incident_group(
    group_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GroupDetailResponse:
    """Everything the Cascade Explorer renders, computed server-side.

    The graph is projected here rather than assembled in the browser. Node and edge topology is
    a statement about which rows are related, which makes it business logic — and business logic
    in the frontend is a second implementation nobody tests against the database.
    """
    group = await _load_group(session, group_id)
    rollup = await cascade_rollup(session, group_id=int(group.id))
    graph = await project_graph(session, group_id=int(group.id))
    members = await group_affected_flights(session, group_id=int(group.id))

    passengers = await _passengers_by_flight(session, [member.flight_id for member in members])
    incidents = {
        int(row.flight_id): row
        for row in (await session.execute(select(Incident).where(Incident.group_id == group.id)))
        .scalars()
        .all()
    }

    radius = compose_blast_radius(
        rollup=rollup,
        graph=graph,
        hotel_payload=await _group_hotel_payload(session, int(group.id)),
    )

    return GroupDetailResponse(
        id=int(group.id),
        reference=group.reference,
        root_cause=str(group.root_cause),
        airport_icao=group.airport_icao,
        severity=group.severity,
        state=str(group.state),
        opened_at=_as_utc(group.opened_at),
        rollups=_rollups(rollup),
        rollup_status=_rollup_status(rollup),
        flights=[
            GroupFlightRow(
                flight_id=member.flight_id,
                flight_number=member.flight_number,
                route=f"{member.origin_icao} -> {member.destination_icao}",
                origin_icao=member.origin_icao,
                destination_icao=member.destination_icao,
                role=member.role,
                delay_minutes=member.delay_minutes_at_injection,
                scheduled_departure_local=member.scheduled_departure_local,
                incident_id=member.incident_id,
                incident_reference=(
                    incidents[member.flight_id].reference if member.flight_id in incidents else None
                ),
                incident_state=member.incident_state,
                passengers=passengers.get(member.flight_id, 0),
            )
            for member in members
        ],
        crew_pairings=[
            {
                "pairing_reference": pairing.pairing_reference,
                "base_icao": pairing.base_icao,
                "source_flight": pairing.source_flight,
                "affected_leg": pairing.affected_leg,
                "mechanism": pairing.mechanism,
                "detail": pairing.detail,
                "at_risk": pairing.at_risk,
            }
            for pairing in rollup.pairings
        ],
        mechanism_legend=MECHANISM_LEGEND,
        why_nine_not_eight=_why_the_counts_differ(rollup),
        graph=CascadeGraphOut(**_graph_out(graph, rollup)),
        blast_radius=BlastRadiusOut(**blast_radius_payload(radius)),
        provenance=_provenance(group),
    )


def _graph_out(graph: Any, rollup: CascadeRollup) -> dict[str, Any]:
    """Flatten the projection's exclusive-arc provenance into one `derived_from` string.

    The API surface says `action:57` or `prediction:12` rather than exposing two nullable columns,
    because a renderer only needs to link to the evidence. The database keeps the two columns and
    the CHECK that exactly one is set — the constraint stays where it can be enforced.
    """
    payload = graph_payload(graph, rollup)
    payload["edges"] = [
        {
            "source_ref": edge["source_ref"],
            "target_ref": edge["target_ref"],
            "edge_kind": edge["edge_kind"],
            "mechanism": edge["mechanism"],
            "detail": edge["detail"],
            "depth": edge["depth"],
            "derived_from": (
                f"action:{edge['derived_from_action_id']}"
                if edge.get("derived_from_action_id") is not None
                else f"prediction:{edge['derived_from_prediction_id']}"
            ),
        }
        for edge in payload["edges"]
    ]
    return payload


def _why_the_counts_differ(rollup: CascadeRollup) -> str:
    """The structural sentence, computed from the rollup rather than asserted.

    Every number in it comes from `rollup`, so it cannot drift from the table beside it. When
    nothing has been assessed it says so instead of narrating a cascade that has not been found.
    """
    if not rollup.pairings:
        return (
            f"{rollup.flights_affected} flights are declared for this disruption. No crew "
            "assessment has been recorded yet, so the rotation count is not yet derivable."
        )
    spanning = ", ".join(
        f"{pairing.pairing_reference} via {pairing.mechanism}" for pairing in rollup.pairings[:3]
    )
    return (
        f"Crew are assigned to multi-leg pairings, not to individual flights, so "
        f"{rollup.crew_pairings_affected} rotations against {rollup.flights_affected} flights is "
        f"expected rather than an error. Each rotation names the mechanism that put it at risk "
        f"({spanning}, and so on), so the total can be counted from the graph instead of taken "
        f"on trust. Coordination and display only: duty-time legality is not validated anywhere "
        f"in this system."
    )


async def _passengers_by_flight(session: AsyncSession, flight_ids: list[int]) -> dict[int, int]:
    if not flight_ids:
        return {}
    rows = (
        await session.execute(
            select(BookingSegment.flight_id, func.count(func.distinct(Booking.passenger_id)))
            .join(Booking, Booking.id == BookingSegment.booking_id)
            .where(BookingSegment.flight_id.in_(flight_ids))
            .group_by(BookingSegment.flight_id)
        )
    ).all()
    return {int(flight_id): int(count) for flight_id, count in rows}


async def _group_hotel_payload(session: AsyncSession, group_id: int) -> dict[str, Any] | None:
    """Group-wide accommodation totals.

    Delegated to the group orchestrator so the API and the run report the same figures. Summing
    rather than taking the newest action matters here: eight flights draw on one finite inventory,
    so the last allocation to run sees only what is left. Reporting its figures as the group's
    showed "9 rooms required, 0 short" for a disruption needing 302 rooms against 71 available.
    """
    return await GroupOrchestrator(session).hotel_totals(group_id)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------- run


@router.post(
    "/incident-groups/{group_id}/run",
    response_model=GroupRunResponse,
    summary="Advance every member incident of the disruption",
)
async def run_incident_group(
    group_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> GroupRunResponse:
    """Open the declared incidents if needed, advance each, and record what was derived.

    Stopping short of terminal is normal: `note` says how many member incidents are waiting for
    a person. The group is terminal only when **every** member is — seven resolved and one
    awaiting approval is not a finished recovery, and reporting it as one is the exact failure the
    completeness flag exists to prevent.

    Authorisation is untouched. Each member incident goes through its own per-flight gate, so
    running the group never approves anything.
    """
    group = await _load_group(session, group_id)

    if idempotency_key:
        replay = await _recorded_group_run(session, int(group.id), idempotency_key)
        if replay is not None:
            log.info(
                "group_run_replayed",
                group_reference=group.reference,
                idempotency_key=idempotency_key,
                outcome="idempotent_replay",
            )
            return replay

    orchestrator = GroupOrchestrator(session)
    result = await orchestrator.run_group(int(group.id), correlation_id=correlation_id_var.get())
    response = _run_response(result, idempotency_key=idempotency_key)

    if idempotency_key:
        session.add(
            DecisionLog(
                incident_id=None,
                occurred_at=datetime.now(UTC),
                stage="run",
                actor="orchestrator",
                event_type=GROUP_RUN_EVENT,
                summary=f"Group run requested with idempotency key {idempotency_key}",
                detail={
                    "idempotency_key": idempotency_key,
                    "incident_group_id": int(group.id),
                    "result": response.model_dump(mode="json"),
                },
                correlation_id=correlation_id_var.get(),
            )
        )
        await session.flush()
    return response


def _run_response(
    result: GroupRunResult, *, idempotency_key: str | None, replayed: bool = False
) -> GroupRunResponse:
    rollup = result.rollup
    if rollup is None:
        raise EntityNotFound(
            f"incident group {result.group_id} produced no rollup, so it cannot be reported"
        )
    return GroupRunResponse(
        group_reference=result.group_reference,
        is_terminal=result.is_terminal,
        states=result.states,
        note=result.note,
        incidents=[
            {
                "flight_id": item.flight_id,
                "flight_number": item.flight_number,
                "role": item.role,
                "incident_id": item.incident_id,
                "incident_reference": item.incident_reference,
                "state": item.state,
                "steps_taken": item.steps_taken,
                "is_terminal": item.is_terminal,
                "note": item.note,
                "plan_id": item.plan_id,
                "plan_hash": item.plan_hash,
                "awaiting_evaluation_ids": item.awaiting_evaluation_ids,
            }
            for item in result.incidents
        ],
        rollups=_rollups(rollup),
        rollup_status=_rollup_status(rollup),
        snapshot_hash=result.snapshot_hash,
        edges_recorded=result.edges_recorded,
        replayed=replayed,
        idempotency_key=idempotency_key,
    )


async def _recorded_group_run(
    session: AsyncSession, group_id: int, key: str
) -> GroupRunResponse | None:
    """Replay a recorded group run from `decision_log`.

    The same ledger the per-incident route uses, for the same reason: a dedicated idempotency
    table would be a migration, and the append-only journal already answers the question.
    """
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.event_type == GROUP_RUN_EVENT)
        .order_by(DecisionLog.id)
    )
    for entry in (await session.execute(stmt)).scalars():
        detail = entry.detail or {}
        if (
            detail.get("idempotency_key") == key
            and detail.get("incident_group_id") == group_id
            and isinstance(detail.get("result"), dict)
        ):
            return GroupRunResponse(**{**detail["result"], "replayed": True})
    return None


# ------------------------------------------------------------------- plan assurance


@router.get(
    "/incident-groups/{group_id}/plan-assurance",
    response_model=PlanAssuranceListResponse,
    summary="Group-scoped plan assurance for every member plan",
)
async def get_plan_assurance(
    group_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanAssuranceListResponse:
    """Plan-level assurance for each member incident's selected plan (P2-D1).

    Read-only, and structurally incapable of authorising anything: every result carries
    `authorises_no_action: true`. It exists so an operator can see the aggregate before deciding,
    not so the aggregate can decide for them.
    """
    group = await _load_group(session, group_id)
    rollup = await cascade_rollup(session, group_id=int(group.id))
    orchestrator = GroupOrchestrator(session)
    results = await orchestrator.plan_assurance(int(group.id), rollup=rollup)

    plans: list[PlanAssuranceOut] = []
    for result in results:
        plans.append(await _plan_assurance_out(session, result, group.reference))

    loaded = plan_lifecycle.load_plan_gate_config()
    return PlanAssuranceListResponse(
        group_reference=group.reference,
        plans=plans,
        config_version=loaded.version if loaded else "unavailable",
        config_hash=loaded.digest if loaded else "unavailable",
        note=(
            "One plan-level evaluation per member incident, all scoped to this disruption. A "
            "plan approval may cover low and medium risk tasks; high risk always needs its own "
            "decision, and no approval releases a task blocked on failed evidence."
            if loaded
            else "No plan section is present in the configured assurance config, so the plan "
            "gate refuses rather than defaulting to an exposure budget nobody approved."
        ),
    )


async def _plan_assurance_out(
    session: AsyncSession, result: PlanAssuranceResult, group_reference: str
) -> PlanAssuranceOut:
    summary = plan_lifecycle.summarise(
        result,
        needing_own_decision=(
            await plan_lifecycle.tasks_needing_own_decision(
                session, plan_id=int(result.plan_id), group_reference=group_reference
            )
            if result.plan_id
            else []
        ),
    )
    incident_reference = None
    incident_id = None
    if result.plan_id:
        plan = await session.get(Plan, int(result.plan_id))
        if plan is not None:
            incident = await session.get(Incident, int(plan.incident_id))
            if incident is not None:
                incident_reference = incident.reference
                incident_id = int(incident.id)

    approval = None
    if result.plan_id:
        approval = await _approval_out(session, plan_id=int(result.plan_id))

    return PlanAssuranceOut(
        **summary,
        incident_reference=incident_reference,
        incident_id=incident_id,
        approval=approval,
    )


async def _approval_out(session: AsyncSession, *, plan_id: int) -> PlanApprovalOut | None:
    approval = await plan_lifecycle.approval_for_plan(session, plan_id=plan_id)
    if approval is None:
        return None
    tiers = (
        (
            await session.execute(
                select(PlanApprovalTier.risk_tier).where(
                    PlanApprovalTier.plan_approval_id == approval.id
                )
            )
        )
        .scalars()
        .all()
    )
    return PlanApprovalOut(
        id=int(approval.id),
        plan_id=int(approval.plan_id),
        plan_hash=str(approval.plan_hash),
        covered_task_ids=[str(item) for item in (approval.covered_task_ids or [])],
        covers_tiers=sorted(str(tier) for tier in tiers),
        actor_id=str(approval.actor_id),
        reason=str(approval.reason),
        decided_at=_as_utc(approval.decided_at),
        gate_config_version=str(approval.gate_config_version),
        gate_config_hash=str(approval.gate_config_hash),
        tasks_needing_own_decision=[],
        note=(
            "Bound to this plan hash. A re-planned incident voids it rather than carrying it "
            "forward, and a high-risk task is never covered."
        ),
    )


@router.post(
    "/incident-groups/{group_id}/plans/{plan_id}/approval",
    response_model=PlanApprovalOut,
    summary="Approve a plan's low and medium risk tasks",
)
async def approve_plan(
    group_id: str,
    plan_id: int,
    payload: PlanApprovalRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlanApprovalOut:
    """Record a plan approval, bound to the plan's hash.

    Four things this cannot do, and all four are enforced by Stream B's rules rather than by this
    route: cover a high-risk task, release a task blocked on failed evidence, survive a re-plan,
    or be edited. A revised decision is a new plan.

    The approval is written only if `may_approve_plan` permits it, so an inadmissible plan is
    refused with the gate's own reason rather than a generic 400.
    """
    group = await _load_group(session, group_id)
    rollup = await cascade_rollup(session, group_id=int(group.id))
    result = await plan_lifecycle.assure_plan(
        session,
        plan_id=plan_id,
        rollup=rollup,
        hotel_payload=await _group_hotel_payload(session, int(group.id)),
    )

    approval = await plan_lifecycle.record_plan_approval(
        session,
        plan_id=plan_id,
        incident_group_id=int(group.id),
        result=result,
        actor_id=payload.actor_id,
        reason=payload.reason,
        decided_at=datetime.now(UTC),
    )

    plan = await session.get(Plan, plan_id)
    if plan is not None:
        session.add(
            DecisionLog(
                incident_id=int(plan.incident_id),
                occurred_at=datetime.now(UTC),
                stage="assure",
                actor="human",
                event_type="PLAN_APPROVAL_RECORDED",
                summary=(
                    f"Operator approved plan {approval.plan_hash} covering "
                    f"{len(approval.covered_task_ids or [])} tasks"
                ),
                detail={
                    "plan_approval_id": int(approval.id),
                    "plan_id": plan_id,
                    "plan_hash": approval.plan_hash,
                    "covered_task_ids": list(approval.covered_task_ids or []),
                    "actor_id": payload.actor_id,
                    "reason": payload.reason,
                    "incident_group_id": int(group.id),
                    "group_reference": group.reference,
                },
                correlation_id=correlation_id_var.get(),
            )
        )
        await session.flush()

    out = await _approval_out(session, plan_id=plan_id)
    if out is None:  # pragma: no cover - just written
        raise EntityNotFound(f"plan approval for plan {plan_id} could not be read back")
    return out.model_copy(
        update={
            "tasks_needing_own_decision": await plan_lifecycle.tasks_needing_own_decision(
                session, plan_id=plan_id, group_reference=group.reference
            )
        }
    )


# -------------------------------------------------------------------------- what-if


@router.post(
    "/incident-groups/{group_id}/what-if",
    response_model=WhatIfResponse,
    summary="Bounded zero-write re-evaluation of the disruption",
)
async def post_what_if(
    group_id: str,
    payload: WhatIfRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WhatIfResponse:
    """Re-evaluate the group's figures under substituted inputs. Writes nothing (P2-D2).

    Two independent guards, on purpose. Stream B's `assert_zero_write` refuses the *request* when
    the configuration would make a what-if unsafe — a live provider, armed dispatch, a missing
    deterministic seed. Stream C's `evaluate_what_if` then performs only reads. A guard that
    passed and an implementation that wrote would be the worst possible combination, so the
    response carries the verdict alongside the figures and `wrote_rows` is a `Literal[False]`.
    """
    from app.assurance.whatif import WhatIfRequest as GuardRequest
    from app.assurance.whatif import assert_zero_write
    from app.config import get_modes

    group = await _load_group(session, group_id)
    loaded = plan_lifecycle.load_plan_gate_config()
    modes = get_modes()

    verdict = assert_zero_write(
        request=GuardRequest(
            candidate_count=1,
            seed=0,
            provider_modes={
                "weather": modes.weather.value,
                "notifications": modes.notification.value,
            },
            real_dispatch_enabled=False,
            writes_records=False,
            commits_inventory=False,
            creates_actions=False,
            figures_treated_as_authoritative=False,
        ),
        policy=loaded.what_if if loaded else _default_what_if_policy(),
    )

    result = await evaluate_what_if(session, group_id=int(group.id), levers=payload.levers)
    body = what_if_payload(result)
    body["guard"] = {
        "permitted": verdict.permitted,
        "refusals": [item.value for item in verdict.refusals],
        "reasons": list(verdict.reasons),
        "seed": verdict.seed,
        "provenance": verdict.provenance,
        "authoritative": verdict.authoritative,
        "note": (
            "The guard checks whether a what-if is safe to run at all; the re-evaluation then "
            "only reads. Both are reported so neither can be assumed."
        ),
    }
    return WhatIfResponse(**body)


def _default_what_if_policy():
    from app.assurance.plan_contract import WhatIfPolicy

    return WhatIfPolicy()


# --------------------------------------------------------------------------- replay


@router.get(
    "/incident-groups/{group_id}/replay",
    response_model=ReplayResponse,
    summary="Immutable record of the disruption, in order",
)
async def get_replay(
    group_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReplayResponse:
    """Every journal entry for the group's incidents, oldest first.

    A fold over records, not a re-run. Nothing is recomputed here, which is what makes replay
    trustworthy: it cannot produce a figure the original run did not, because it has no way to
    produce a figure at all.
    """
    group = await _load_group(session, group_id)
    incidents = {
        int(row.id): row.reference
        for row in (await session.execute(select(Incident).where(Incident.group_id == group.id)))
        .scalars()
        .all()
    }

    stmt = (
        select(DecisionLog)
        .where(DecisionLog.incident_id.in_(list(incidents) or [0]))
        .order_by(DecisionLog.occurred_at, DecisionLog.id)
    )
    entries = (await session.execute(stmt)).scalars().all()

    frames = [
        ReplayFrame(
            index=index,
            id=int(entry.id),
            incident_id=int(entry.incident_id) if entry.incident_id else None,
            incident_reference=incidents.get(int(entry.incident_id or 0)),
            occurred_at=_as_utc(entry.occurred_at),
            stage=entry.stage,
            actor=entry.actor,
            actor_kind=_actor_kind(entry.actor),
            event_type=entry.event_type,
            summary=entry.summary,
            detail=entry.detail or {},
            correlation_id=entry.correlation_id,
        )
        for index, entry in enumerate(entries)
    ]
    return ReplayResponse(
        group_reference=group.reference,
        frame_count=len(frames),
        frames=frames,
        note=(
            f"{len(frames)} immutable entries across {len(incidents)} member incidents. Replay "
            "is a fold over these records; nothing is recomputed, so no figure here can differ "
            "from what the run produced."
        ),
    )


def _actor_kind(actor: str) -> str:
    """Human, gate or system. Kept identical to the incident timeline's mapping.

    Actor identity is deliberately separate from status in the API as well as the UI: "who did
    this" and "how did it turn out" are different questions, and collapsing them is how an
    automated action comes to look like an approved one.
    """
    if actor == "human":
        return "human"
    if actor == "assurance_gate":
        return "gate"
    return "system"
