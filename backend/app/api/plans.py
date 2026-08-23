"""Candidate plan endpoints — STREAM A.

Propose, compare and select recovery plans for one incident. The plans stay **per incident**,
which is the Phase 1 invariant; only the review surface and the operator's act are group scoped
(see `incident_groups.py`).

Comparison is a re-evaluation over the same recorded facts — P2-D2. Nothing is written, nothing
is projected, and the response carries no rank: Stream B provides no `recommended` flag on
purpose, because choosing between recovery plans is a judgement and a judgement has an owner.

Owner: Stream A.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.workflow import Incident, IncidentGroup, PlanTask
from app.observability.logging import get_logger
from app.orchestrator.candidates import CandidateService, comparison_payload
from app.schemas.plans import (
    CandidateComparisonResponse,
    CandidatePlanOut,
    CandidatePlansResponse,
    PlanTaskOut,
    SelectPlanRequest,
)

router = APIRouter(tags=["plans"])
log = get_logger(__name__)

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", description="A repeat returns the recorded selection."),
]


async def _load_incident(session: AsyncSession, reference: str) -> Incident:
    incident: Incident | None = None
    if reference.isdigit():
        incident = await session.get(Incident, int(reference))
    if incident is None:
        stmt = select(Incident).where(Incident.reference == reference)
        incident = (await session.execute(stmt)).scalars().first()
    if incident is None:
        raise EntityNotFound("incident not found", details={"incident": reference})
    return incident


async def _group_reference(session: AsyncSession, incident: Incident) -> str:
    if incident.group_id is None:
        return incident.reference
    group = await session.get(IncidentGroup, incident.group_id)
    return group.reference if group else incident.reference


async def _plan_out(session: AsyncSession, plan, incident_reference: str) -> CandidatePlanOut:
    stmt = select(PlanTask).where(PlanTask.plan_id == plan.id).order_by(PlanTask.task_order)
    tasks = list((await session.execute(stmt)).scalars())
    return CandidatePlanOut(
        id=plan.id,
        incident_reference=incident_reference,
        variant_key=plan.variant_key,
        generator=plan.generator,
        prompt_version=plan.prompt_version,
        generated_at=plan.generated_at,
        rationale=plan.rationale,
        selection_state=plan.selection_state,
        selected_at=plan.selected_at,
        selected_by=plan.selected_by,
        plan_hash=plan.plan_hash,
        tasks=[
            PlanTaskOut(
                id=row.id,
                action_type=row.action_type,
                task_order=row.task_order,
                state=str(row.state),
                target_refs=list(row.target_refs or []),
                depends_on=[str(dep) for dep in (row.depends_on or [])],
            )
            for row in tasks
        ],
    )


@router.get(
    "/incidents/{incident_id}/plans",
    response_model=CandidatePlansResponse,
    summary="Candidate recovery plans for one incident",
)
async def list_plans(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidatePlansResponse:
    incident = await _load_incident(session, incident_id)
    service = CandidateService(session)
    plans = await service.propose_candidates(incident)
    selected = next((plan for plan in plans if plan.selection_state == "selected"), None)
    return CandidatePlansResponse(
        incident_reference=incident.reference,
        plans=[await _plan_out(session, plan, incident.reference) for plan in plans],
        selected_plan_id=selected.id if selected else None,
    )


@router.get(
    "/incidents/{incident_id}/plans/comparison",
    response_model=CandidateComparisonResponse,
    summary="Compare candidates against the same recorded facts. Writes nothing.",
)
async def compare_plans(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CandidateComparisonResponse:
    """What-if as plan comparison — P2-D2.

    Every figure was re-evaluated against evidence already recorded for this incident. No world
    state is modelled, no outcome is claimed, and `basis` is a `Literal` so the contract cannot
    express a projection.
    """
    incident = await _load_incident(session, incident_id)
    service = CandidateService(session)
    result, plans = await service.compare(
        incident, group_reference=await _group_reference(session, incident)
    )
    payload = comparison_payload(result, plans)
    return CandidateComparisonResponse(
        incident_reference=incident.reference,
        not_a_forecast=payload["not_a_forecast"],
        decision=payload["decision"],
        admissible=payload["admissible"],
        blocking_reasons=payload["blocking_reasons"],
        seed=payload["seed"],
        what_if=payload["what_if"],
        candidates=payload["candidates"],
    )


@router.post(
    "/incidents/{incident_id}/plans/{plan_id}/select",
    response_model=CandidatePlansResponse,
    summary="Record which candidate an operator chose. Immutable.",
)
async def select_plan(
    incident_id: str,
    plan_id: int,
    payload: SelectPlanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: IdempotencyKey = None,
) -> CandidatePlansResponse:
    """A second, different selection is a 409.

    The same shape `human_decision` already uses: a choice is a record of what somebody
    decided, not a mutable setting. The partial unique index
    `uq_plan_selected_per_incident` enforces it in the database as well, so a race becomes a
    conflict rather than a lost write.
    """
    incident = await _load_incident(session, incident_id)
    service = CandidateService(session)
    await service.select(
        incident, plan_id=plan_id, actor_id=payload.actor_id, reason=payload.reason
    )
    plans = await service.plans_for_incident(incident.id)
    selected = next((plan for plan in plans if plan.selection_state == "selected"), None)
    return CandidatePlansResponse(
        incident_reference=incident.reference,
        plans=[await _plan_out(session, plan, incident.reference) for plan in plans],
        selected_plan_id=selected.id if selected else None,
    )
