"""Explanation and report endpoints — read-only model artefacts.

Neither enters the assurance gate, triggers an action, nor modifies a row. Both are generated on
demand from recorded evidence.

The absence protocol is Stream A's: the agents return `None` when there is no usable model output,
and that covers `LLM_MODE=off`, a missing key, a timeout and malformed JSON alike. This module turns
`None` into a 404 that names the mode, because "no model ran" is a configuration fact rather than a
failure, and the deterministic record is complete without it.

Owner: Stream C.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_modes
from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.enums import ActionStatus
from app.models.workflow import Action, Incident, IncidentGroup, Plan, PlanTask
from app.observability.logging import get_logger

router = APIRouter(tags=["reasoning"])
log = get_logger(__name__)


def _unavailable(kind: str) -> EntityNotFound:
    """A 404 that names why, so a client can render a state rather than an error.

    `details.llm_mode` is what the console keys off to distinguish "the model is switched off" from
    "the model failed", which are different things to show an operator.
    """
    mode = get_modes().llm.value
    return EntityNotFound(
        f"no {kind} is available",
        details={
            "llm_mode": mode,
            "reason": (
                "LLM_MODE=off, so no reasoning agent ran. The deterministic record is complete "
                "without it."
                if mode == "off"
                else "The reasoning agent returned no usable output. The deterministic record is "
                "unaffected."
            ),
            "resolution": (
                "Set LLM_MODE=fixture or LLM_MODE=live to enable reasoning agents."
                if mode == "off"
                else "Retry, or read the recorded actions directly."
            ),
        },
    )


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


async def _recorded_actions(session: AsyncSession, incident_id: int) -> list[dict[str, Any]]:
    """The actions that actually ran, with the reason each recorded.

    Includes `needs_human`: a partial hotel allocation committed real rooms and is part of what
    happened. Excluding it would let an explanation describe a cleaner recovery than occurred.
    """
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
    return [
        {"action_type": action_type, "status": action.status, "reason": action.reason or ""}
        for action, action_type in (await session.execute(stmt)).all()
    ]


@router.get(
    "/incidents/{incident_id}/explanation",
    summary="Natural-language explanation of a completed recovery. Authorises nothing.",
)
async def get_explanation(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Explain a recovery from its recorded actions.

    Generated per request rather than stored. An explanation's value is that it reflects the
    evidence as it stands, and caching one introduces a staleness question this system has no
    mechanism to answer.
    """
    from app.agents import explainer

    incident = await _resolve_incident(session, incident_id)
    actions = await _recorded_actions(session, incident.id)
    if not actions:
        raise EntityNotFound(
            "no completed action to explain",
            details={
                "incident_reference": incident.reference,
                "resolution": "Run the incident first.",
            },
        )

    artefact = await explainer.explain(incident_reference=incident.reference, actions=actions)
    if artefact is None:
        raise _unavailable("explanation")

    return {
        "incident_reference": incident.reference,
        "generator": artefact.audit.generator,
        "prompt_version": artefact.audit.prompt_version,
        "source": artefact.source,
        "llm_mode": get_modes().llm.value,
        **artefact.response.model_dump(mode="json"),
        "audit": artefact.audit.model_dump(mode="json"),
        "authorises_no_action": True,
    }


@router.get(
    "/reports/{report_id}",
    summary="Executive report for a disruption. Authorises nothing.",
)
async def get_report(
    report_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Summarise a disruption from its derived rollup. `report_id` is a group or incident reference.

    Every figure the model may use comes from `cascade_rollup` and `group_hotel_totals`, which are
    the same figures the console shows. The report cites them; it never computes one.
    """
    from app.agents import reporter
    from app.db.scenario_queries import cascade_rollup
    from app.services.hotel import group_hotel_totals

    stmt = select(IncidentGroup).where(IncidentGroup.reference == report_id)
    group = (await session.execute(stmt)).scalars().first()

    if group is not None:
        group_id, reference = group.id, group.reference
    else:
        incident = await _resolve_incident(session, report_id)
        if incident.group_id is None:
            raise EntityNotFound(
                "this incident belongs to no disruption group, so there is no rollup to report",
                details={"incident_reference": incident.reference},
            )
        group_id, reference = incident.group_id, incident.reference

    rollup = await cascade_rollup(session, group_id=group_id)
    artefact = await reporter.generate(
        reference=reference,
        rollup={
            "flights_affected": rollup.flights_affected,
            "passengers_affected": rollup.passengers_affected,
            "connections_at_risk": rollup.connections_at_risk,
            "crew_pairings_affected": rollup.crew_pairings_affected,
            "candidate_hotels": rollup.candidate_hotels,
        },
        hotel_summary=await group_hotel_totals(session, group_id=group_id),
    )
    if artefact is None:
        raise _unavailable("report")

    return {
        "reference": reference,
        "generator": artefact.audit.generator,
        "prompt_version": artefact.audit.prompt_version,
        "source": artefact.source,
        "llm_mode": get_modes().llm.value,
        **artefact.response.model_dump(mode="json"),
        "audit": artefact.audit.model_dump(mode="json"),
        "authorises_no_action": True,
    }
