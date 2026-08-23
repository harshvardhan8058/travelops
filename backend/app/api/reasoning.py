"""Phase 3 reasoning-agent endpoints — Explanation and Report.

Both are read-only artifacts generated on demand from recorded evidence. Neither enters assurance,
triggers an action, or modifies any row. They are the model's contribution to the audit trail and
the executive display, not to the recovery itself.

When `LLM_MODE=off` these return 404 with a message naming the mode, not an error. A missing
artifact is a configuration fact, not a system failure.

Owner: Stream C.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import LLMMode, get_settings
from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.enums import ActionStatus
from app.models.workflow import Action, Incident, IncidentGroup, Plan, PlanTask
from app.observability.logging import get_logger

router = APIRouter(tags=["reasoning"])
log = get_logger(__name__)


async def _resolve_incident(session: AsyncSession, reference: str) -> Incident:
    incident: Incident | None = None
    if reference.isdigit():
        incident = await session.get(Incident, int(reference))
    if incident is None:
        stmt = select(Incident).where(Incident.reference == reference)
        incident = (await session.execute(stmt)).scalars().first()
    if incident is None:
        raise EntityNotFound("incident not found", details={"incident": reference})
    return incident


async def _resolve_group(session: AsyncSession, reference: str) -> IncidentGroup:
    group: IncidentGroup | None = None
    if reference.isdigit():
        group = await session.get(IncidentGroup, int(reference))
    if group is None:
        stmt = select(IncidentGroup).where(IncidentGroup.reference == reference)
        group = (await session.execute(stmt)).scalars().first()
    if group is None:
        raise EntityNotFound("disruption group not found", details={"group": reference})
    return group


async def _actions_summary(session: AsyncSession, incident_id: int) -> list[dict[str, Any]]:
    """Summary of completed actions for the explainer's context."""
    stmt = (
        select(Action, PlanTask.action_type)
        .join(PlanTask, PlanTask.id == Action.plan_task_id)
        .join(Plan, Plan.id == PlanTask.plan_id)
        .where(
            Plan.incident_id == incident_id,
            Action.status.in_([ActionStatus.success.value, ActionStatus.needs_human.value]),
        )
        .order_by(Action.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "action_type": action_type,
            "status": action.status,
            "reason": action.reason or "",
        }
        for action, action_type in rows
    ]


@router.get(
    "/incidents/{incident_id}/explanation",
    summary="Natural-language explanation of the recovery (Phase 3 reasoning agent)",
)
async def get_explanation(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """On-demand explanation from the Explainer agent.

    Generated from recorded evidence each time — not cached, because the value is in the
    explanation reflecting the current state of the evidence, and caching introduces a
    staleness question the system has no mechanism to answer.

    Returns 404 with mode information when `LLM_MODE=off`.
    """
    settings = get_settings()
    if settings.llm_mode == LLMMode.off:
        raise EntityNotFound(
            "explanation not available: LLM_MODE=off",
            details={
                "llm_mode": "off",
                "resolution": "Set LLM_MODE=fixture or LLM_MODE=live to enable reasoning agents.",
            },
        )

    incident = await _resolve_incident(session, incident_id)
    actions = await _actions_summary(session, incident.id)

    if not actions:
        raise EntityNotFound(
            "no completed actions to explain",
            details={
                "incident_reference": incident.reference,
                "resolution": "Run the incident to completion first.",
            },
        )

    from app.agents.explainer import ExplainerAgent

    agent = ExplainerAgent()
    response, audit = await agent.explain(
        incident_reference=incident.reference,
        actions_summary=actions,
    )

    return {
        "incident_reference": incident.reference,
        "generator": audit.generator,
        "prompt_version": audit.prompt_version,
        "llm_mode": settings.llm_mode.value,
        **response.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
    }


@router.get(
    "/reports/{report_id}",
    summary="Executive report for a resolved incident or group (Phase 3 reasoning agent)",
)
async def get_report(
    report_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """On-demand executive report from the Report Generator agent.

    `report_id` is an incident reference or a group reference.

    Returns 404 with mode information when `LLM_MODE=off`.
    """
    settings = get_settings()
    if settings.llm_mode == LLMMode.off:
        raise EntityNotFound(
            "report not available: LLM_MODE=off",
            details={
                "llm_mode": "off",
                "resolution": "Set LLM_MODE=fixture or LLM_MODE=live to enable reasoning agents.",
            },
        )

    # Try as a group first (the primary Phase 3 use case), then as an incident
    group: IncidentGroup | None = None
    incident: Incident | None = None

    stmt = select(IncidentGroup).where(IncidentGroup.reference == report_id)
    group = (await session.execute(stmt)).scalars().first()

    if group is None:
        incident = await _resolve_incident(session, report_id)

    # Build context from the cascade rollup
    from app.db.scenario_queries import cascade_rollup
    from app.services.hotel import group_hotel_totals

    if group:
        rollup = await cascade_rollup(session, group_id=group.id)
        hotel = await group_hotel_totals(session, group_id=group.id)
        reference = group.reference
    elif incident and incident.group_id:
        rollup = await cascade_rollup(session, group_id=incident.group_id)
        hotel = await group_hotel_totals(session, group_id=incident.group_id)
        reference = incident.reference
    else:
        raise EntityNotFound(
            "no group context for report generation",
            details={"report_id": report_id},
        )

    from app.agents.reporter import ReportGeneratorAgent

    agent = ReportGeneratorAgent()
    response, audit = await agent.generate(
        group_reference=reference,
        rollup={
            "flights_affected": rollup.flights_affected,
            "passengers_affected": rollup.passengers_affected,
            "connections_at_risk": rollup.connections_at_risk,
            "crew_pairings_affected": rollup.crew_pairings_affected,
            "candidate_hotels": rollup.candidate_hotels,
        },
        hotel_summary=hotel,
    )

    return {
        "reference": reference,
        "generator": audit.generator,
        "prompt_version": audit.prompt_version,
        "llm_mode": settings.llm_mode.value,
        **response.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
    }
