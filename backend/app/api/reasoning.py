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
from app.errors import EntityNotFound, ProviderUnavailable
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


def _source_of(generator: str) -> str:
    """`fixture` or `live`, read off the generator the client already recorded.

    Formatting, not derivation: `LLMClient` writes `fixture:<agent>` for a replay and
    `groq:<model>` for a network call, so this only saves every client from parsing a prefix. A
    consumer needs it because a fixture artefact and a live one carry different weight in a review,
    and `llm_mode` alone does not distinguish them — live mode falls back to no output rather than
    to a fixture, but a reader cannot know that from the mode.
    """
    return "fixture" if generator.startswith("fixture:") else "live"


def _unavailable(exc: Exception, *, artifact: str, mode: str) -> ProviderUnavailable:
    """Turn a model-provider failure into 503 rather than letting it become a 500.

    `LLMUnavailable` is a plain `Exception`, not a `TravelOpsError`, and `app.main` only installs
    handlers for `TravelOpsError` and `RequestValidationError`. So before this, every live
    failure — no API key, a rate limit, a schema mismatch — escaped uncaught and Starlette
    turned it into a bare 500 with no error code and no mode information.

    Deliberately NOT a fixture fallback. In `live` mode the honest answer to "the model could not
    be reached" is to say so; replaying a committed fixture would put recorded prose behind a
    `source: live` label and quietly make the artifact untraceable.
    """
    return ProviderUnavailable(
        f"{artifact} unavailable: the reasoning model could not be reached",
        details={
            "llm_mode": mode,
            "provider_error": str(exc)[:300],
            "resolution": (
                "Check GROQ_API_KEY and provider status. "
                "Set LLM_MODE=fixture to serve the committed artefact instead."
            ),
        },
    )


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
    from app.llm.client import LLMUnavailable

    agent = ExplainerAgent()
    try:
        response, audit = await agent.explain(
            incident_reference=incident.reference,
            actions_summary=actions,
        )
    except LLMUnavailable as exc:
        raise _unavailable(exc, artifact="explanation", mode=settings.llm_mode.value) from exc

    return {
        "incident_reference": incident.reference,
        "generator": audit.generator,
        "prompt_version": audit.prompt_version,
        "source": _source_of(audit.generator),
        "llm_mode": settings.llm_mode.value,
        **response.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
        # On the contract, not in a comment. An explanation of a recovery that already happened
        # cannot authorise, reverse or modify any part of it.
        "authorises_no_action": True,
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
    from app.llm.client import LLMUnavailable

    agent = ReportGeneratorAgent()
    try:
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
    except LLMUnavailable as exc:
        raise _unavailable(exc, artifact="report", mode=settings.llm_mode.value) from exc

    return {
        "reference": reference,
        "generator": audit.generator,
        "prompt_version": audit.prompt_version,
        "source": _source_of(audit.generator),
        "llm_mode": settings.llm_mode.value,
        **response.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
        "authorises_no_action": True,
    }
