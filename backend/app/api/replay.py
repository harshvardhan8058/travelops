"""Replay endpoints — STREAM A.

Read-only, ordered, complete. Frames are `decision_log` rows enriched with the assurance and
human decision each step referenced, so a reviewer can open any frame and see what authorised it.

No new state, no migration, no orchestrator change. The chronology already exists; this exposes
it. Group replay interleaves member incidents in true chronological order, tie-breaking on
`(occurred_at, id)` so two steps in the same millisecond still have a defined order.

Owner: Stream A.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.actors import actor_kind
from app.db.session import get_session
from app.errors import EntityNotFound
from app.models.workflow import DecisionLog, HumanDecision, Incident, IncidentGroup
from app.observability.logging import get_logger
from app.schemas.replay import ReplayFrame, ReplayResponse

router = APIRouter(tags=["replay"])
log = get_logger(__name__)

INCIDENT_NOTE = (
    "Every frame is a recorded `decision_log` row. `sequence` is the ordinal position in the "
    "`(occurred_at, id)` ordering rather than a stored column, so contiguity is a property of "
    "this response. Nothing here is recomputed and nothing is written."
)

GROUP_NOTE = (
    "Member incidents interleaved in true chronological order, plus the group's own entries. "
    "Ties break on `(occurred_at, id)`. Read-only: row counts are identical before and after."
)


async def _decision_scope(
    session: AsyncSession, human_decision_id: int | None
) -> tuple[str | None, int | None]:
    """`action` or `plan`, so a plan-covered action reads differently from a per-action approval.

    Both are a person's act — `actor_kind` stays `human` for either — but an auditor has to be
    able to tell "the operator approved this payout" from "this was covered by their plan-wide
    signature".
    """
    if human_decision_id is None:
        return None, None
    decision = await session.get(HumanDecision, human_decision_id)
    if decision is None:
        return None, None
    return decision.scope, decision.plan_approval_id


def _frame_fields(detail: dict) -> dict:
    return {
        "state_before": detail.get("from"),
        "state_after": detail.get("to"),
        "assurance_id": detail.get("assurance_id"),
        "human_decision_id": detail.get("human_decision_id"),
        "evidence_refs": list(detail.get("evidence_refs") or []),
    }


async def _frames(
    session: AsyncSession,
    rows: list[DecisionLog],
    *,
    references: dict[int, str] | None = None,
) -> list[ReplayFrame]:
    frames: list[ReplayFrame] = []
    for index, row in enumerate(rows, start=1):
        detail = row.detail if isinstance(row.detail, dict) else {}
        fields = _frame_fields(detail)
        scope, plan_approval_id = await _decision_scope(session, fields["human_decision_id"])
        frames.append(
            ReplayFrame(
                sequence=index,
                occurred_at=row.occurred_at,
                stage=row.stage,
                actor=row.actor,
                actor_kind=actor_kind(row.actor),
                event_type=row.event_type,
                summary=row.summary,
                incident_reference=(references or {}).get(row.incident_id)
                if row.incident_id
                else None,
                decision_scope=scope,
                plan_approval_id=plan_approval_id,
                detail=detail,
                **fields,
            )
        )
    return frames


@router.get(
    "/incidents/{incident_id}/replay",
    response_model=ReplayResponse,
    summary="Reconstruct one incident from its immutable record",
)
async def replay_incident(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReplayResponse:
    incident: Incident | None = None
    if incident_id.isdigit():
        incident = await session.get(Incident, int(incident_id))
    if incident is None:
        stmt = select(Incident).where(Incident.reference == incident_id)
        incident = (await session.execute(stmt)).scalars().first()
    if incident is None:
        raise EntityNotFound("incident not found", details={"incident": incident_id})

    stmt = (
        select(DecisionLog)
        .where(DecisionLog.incident_id == incident.id)
        .order_by(DecisionLog.occurred_at, DecisionLog.id)
    )
    rows = list((await session.execute(stmt)).scalars())
    frames = await _frames(session, rows, references={incident.id: incident.reference})
    return ReplayResponse(
        incident_reference=incident.reference,
        frame_count=len(frames),
        frames=frames,
        note=INCIDENT_NOTE,
    )


@router.get(
    "/incident-groups/{group_ref}/replay",
    response_model=ReplayResponse,
    summary="Reconstruct the whole cascade, interleaved chronologically",
)
async def replay_group(
    group_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReplayResponse:
    group: IncidentGroup | None = None
    if group_ref.isdigit():
        group = await session.get(IncidentGroup, int(group_ref))
    if group is None:
        stmt = select(IncidentGroup).where(IncidentGroup.reference == group_ref)
        group = (await session.execute(stmt)).scalars().first()
    if group is None:
        raise EntityNotFound("disruption group not found", details={"group": group_ref})

    members = list(
        (await session.execute(select(Incident).where(Incident.group_id == group.id))).scalars()
    )
    references = {incident.id: incident.reference for incident in members}

    stmt = (
        select(DecisionLog)
        .where(
            or_(
                DecisionLog.incident_id.in_(list(references)) if references else False,
                DecisionLog.correlation_id == group.reference,
            )
        )
        .order_by(DecisionLog.occurred_at, DecisionLog.id)
    )
    rows = list((await session.execute(stmt)).scalars())
    frames = await _frames(session, rows, references=references)
    return ReplayResponse(
        group_reference=group.reference,
        frame_count=len(frames),
        frames=frames,
        note=GROUP_NOTE,
    )
