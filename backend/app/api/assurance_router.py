"""Assurance endpoints — STREAM A.

`GET /incidents/{ref}/assurance` reads the immutable evaluations the gate produced.
`POST /assurance/{id}/decision` records an operator's approve or reject.

Stream B owns the decisions; Stream A owns the route and the shape. Nothing here evaluates a
check, re-runs the gate or overrides a decision.

Two invariants shape the write endpoint:

* An operator response is an **immutable** `human_decision` keyed to one evaluation. It
  never mutates the gate record, so approving does not rewrite what the gate decided —
  the audit trail keeps both.
* A rejected decision cannot be reused, and a decision cannot be changed once recorded.
  A second, different decision for the same evaluation is a `409`, not an update.

Owner: Stream A (route) / Stream B (gate semantics).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.incidents import _as_utc, _load_incident
from app.assurance.contract import CheckName
from app.config import get_modes
from app.db.session import get_session
from app.errors import EntityNotFound, InvalidStateTransition
from app.events.types import HumanDecisionRecorded
from app.models.enums import AssuranceDecision, CheckState, HumanDecisionType
from app.models.workflow import AssuranceEvaluation, DecisionLog, HumanDecision, Plan
from app.models.workflow import PlanTask as PlanTaskRow
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator.engine import ACTOR_HUMAN, STAGE_ASSURE
from app.orchestrator.plan_approval import enforce_action_approval
from app.schemas.assurance_api import (
    AssuranceEvaluationOut,
    AssuranceResponse,
    CheckResultOut,
    DecisionRequest,
    DecisionResponse,
    HumanDecisionOut,
)

router = APIRouter(tags=["assurance"])
log = get_logger(__name__)

UNAVAILABLE = "unavailable"


@router.get(
    "/incidents/{incident_id}/assurance",
    response_model=AssuranceResponse,
    summary="Gate evaluations and their failed checks",
)
async def get_assurance(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AssuranceResponse:
    incident = await _load_incident(session, incident_id)

    stmt = (
        select(AssuranceEvaluation, PlanTaskRow)
        .join(PlanTaskRow, AssuranceEvaluation.plan_task_id == PlanTaskRow.id)
        .join(Plan, PlanTaskRow.plan_id == Plan.id)
        .where(Plan.incident_id == incident.id)
        .order_by(AssuranceEvaluation.id)
    )
    rows = (await session.execute(stmt)).all()

    decisions = await _decisions_by_evaluation(
        session, [evaluation.id for evaluation, _task in rows]
    )

    evaluations: list[AssuranceEvaluationOut] = []
    awaiting = 0
    for evaluation, task_row in rows:
        checks = _checks(evaluation)
        has_warn = any(check.state is CheckState.warn for check in checks)
        decision = AssuranceDecision(evaluation.decision)
        human = decisions.get(evaluation.id)
        if decision is AssuranceDecision.needs_human and human is None:
            awaiting += 1
        evaluations.append(
            AssuranceEvaluationOut(
                id=evaluation.id,
                plan_task_id=evaluation.plan_task_id,
                action_type=task_row.action_type,
                decision=decision,
                risk_tier=evaluation.risk_tier,
                evaluated_at=_as_utc(evaluation.evaluated_at),
                checks=checks,
                blocking=_blocking(evaluation),
                evidence_refs=list(evaluation.evidence_refs or []),
                config_version=evaluation.config_version,
                config_hash=evaluation.config_hash,
                # Only meaningful when a warning was actually recorded.
                warn_permitted_by_config=(
                    decision is AssuranceDecision.execute_flagged if has_warn else None
                ),
                human_decision=human,
            )
        )

    # The version and hash the most recent evaluation was made under, so the panel header
    # describes the same semantics as the rows beneath it. Falls back to the running config
    # only when there is nothing recorded yet.
    modes = get_modes()
    latest = evaluations[-1] if evaluations else None
    return AssuranceResponse(
        incident_reference=incident.reference,
        config_version=(
            latest.config_version if latest else (modes.assurance_config_version or UNAVAILABLE)
        ),
        config_hash=latest.config_hash if latest else (modes.assurance_config_hash or UNAVAILABLE),
        evaluations=evaluations,
        awaiting_approval_count=awaiting,
    )


def _checks(evaluation: AssuranceEvaluation) -> list[CheckResultOut]:
    """Rebuild the six checks in contractual order.

    Order comes from the enum, not from dict iteration, so a panel and an audit record can
    never disagree about presentation.
    """
    stored = evaluation.check_results or {}
    results: list[CheckResultOut] = []
    for name in CheckName:
        payload = stored.get(name.value)
        if not isinstance(payload, dict):
            continue
        results.append(
            CheckResultOut(
                name=name,
                state=payload.get("state", CheckState.failed.value),
                reason_code=payload.get("reason_code", "OK"),
                reason=payload.get("reason"),
                tier=payload.get("tier"),
                evidence_refs=list(payload.get("evidence_refs") or []),
            )
        )
    return results


def _blocking(evaluation: AssuranceEvaluation) -> list[CheckName]:
    blocking: list[CheckName] = []
    for name in evaluation.blocking_reasons or []:
        try:
            blocking.append(CheckName(name))
        except ValueError:
            continue
    return blocking


async def _decisions_by_evaluation(
    session: AsyncSession, evaluation_ids: list[int]
) -> dict[int, HumanDecisionOut]:
    if not evaluation_ids:
        return {}
    stmt = select(HumanDecision).where(HumanDecision.assurance_id.in_(evaluation_ids))
    return {
        row.assurance_id: HumanDecisionOut(
            id=row.id,
            decision=HumanDecisionType(row.decision),
            actor_id=row.actor_id,
            reason=row.reason,
            decided_at=_as_utc(row.decided_at),
        )
        for row in (await session.execute(stmt)).scalars()
    }


@router.post(
    "/assurance/{assurance_id}/decision",
    response_model=DecisionResponse,
    summary="Operator approve or reject, with a reason",
)
async def record_decision(
    assurance_id: int,
    payload: DecisionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="A replay returns the original decision."),
    ] = None,
) -> DecisionResponse:
    """Record an immutable operator response for one evaluation.

    Idempotency here comes from the schema rather than a cache: `human_decision.assurance_id`
    is unique, so one evaluation can only ever carry one decision. Re-posting the same
    decision returns the original; posting a different one is a conflict, because an
    operator response is a record of what someone decided and not a mutable setting.
    """
    evaluation = await session.get(AssuranceEvaluation, assurance_id)
    if evaluation is None:
        raise EntityNotFound(
            "assurance evaluation not found", details={"assurance_id": assurance_id}
        )

    # P2-D3, enforced server-side: approval covers risk, never failed evidence, stale sources,
    # unresolved entities or policy failure. A rejection is always allowed — refusing to act is
    # never gated. The console hiding a button is not a control; a direct API call must be
    # refused the same way.
    if payload.decision is HumanDecisionType.approved:
        enforce_action_approval(evaluation)

    existing = (
        (
            await session.execute(
                select(HumanDecision).where(HumanDecision.assurance_id == assurance_id).limit(1)
            )
        )
        .scalars()
        .first()
    )

    if existing is not None:
        recorded = HumanDecisionType(existing.decision)
        if recorded is not payload.decision:
            raise InvalidStateTransition(
                "this evaluation already carries an operator decision",
                details={
                    "assurance_id": assurance_id,
                    "recorded_decision": recorded.value,
                    "requested_decision": payload.decision.value,
                    "resolution": "a corrected decision requires a new evaluation",
                },
            )
        log.info(
            "human_decision_replayed",
            assurance_id=assurance_id,
            idempotency_key=idempotency_key,
            outcome="idempotent_replay",
        )
        return DecisionResponse(
            assurance_id=assurance_id,
            decision=recorded,
            actor_id=existing.actor_id,
            reason=existing.reason,
            decided_at=_as_utc(existing.decided_at),
            replayed=True,
        )

    decided_at = datetime.now(UTC)
    decision = HumanDecision(
        assurance_id=assurance_id,
        decision=payload.decision,
        actor_id=payload.actor_id,
        reason=payload.reason,
        decided_at=decided_at,
    )
    session.add(decision)
    await session.flush()

    correlation = correlation_id_var.get()
    await _journal_decision(
        session,
        evaluation=evaluation,
        decision=decision,
        decided_at=decided_at,
        correlation=correlation,
    )
    log.info(
        "human_decision_recorded",
        assurance_id=assurance_id,
        actor=payload.actor_id,
        outcome=payload.decision.value,
    )
    await _publish_decision(evaluation, decision, correlation)

    return DecisionResponse(
        assurance_id=assurance_id,
        decision=payload.decision,
        actor_id=payload.actor_id,
        reason=payload.reason,
        decided_at=decided_at,
        replayed=False,
    )


async def _journal_decision(
    session: AsyncSession,
    *,
    evaluation: AssuranceEvaluation,
    decision: HumanDecision,
    decided_at: datetime,
    correlation: str | None,
) -> None:
    """Record the operator's decision on the timeline, attributed to a person.

    Written here rather than by the orchestrator, for two reasons.

    **Attribution.** The orchestrator's own entry for an approval is a `STATE_CHANGED` from
    `awaiting_approval` to `executing`, and that is correctly the orchestrator acting — it is
    the thing that moved the incident. But it was the *only* record of an approval, so the
    timeline showed the operator's decision as `actor_kind=orchestrator`. A human authorising a
    bulk external effect is the single most important actor in this system to attribute
    correctly, and the audit trail said a machine did it.

    **Timing.** This is the moment the person decided. The orchestrator only learns about it on
    the next `POST /run`, which may be much later, so a journal entry written there would carry
    an `occurred_at` that is not when the decision happened.

    Rejections previously got their entry from the engine instead. That is now removed, so both
    outcomes are recorded once, in the same place, at the time they occurred.
    """
    resolved = (
        await session.execute(
            select(Plan.incident_id, PlanTaskRow.action_type)
            .join(PlanTaskRow, PlanTaskRow.plan_id == Plan.id)
            .where(PlanTaskRow.id == evaluation.plan_task_id)
            .limit(1)
        )
    ).first()
    incident_id = resolved[0] if resolved else None
    action_type = resolved[1] if resolved else f"plan task {evaluation.plan_task_id}"

    recorded = HumanDecisionType(decision.decision)
    verb = "approved" if recorded is HumanDecisionType.approved else "rejected"
    session.add(
        DecisionLog(
            incident_id=incident_id,
            occurred_at=decided_at,
            stage=STAGE_ASSURE,
            # `_actor_kind` maps this to `human`, which is what the UI groups on.
            actor=ACTOR_HUMAN,
            event_type="HUMAN_DECISION_RECORDED",
            summary=f"Operator {verb} {action_type} (evaluation {evaluation.id})",
            detail={
                "assurance_id": evaluation.id,
                "human_decision_id": decision.id,
                "plan_task_id": evaluation.plan_task_id,
                "action_type": action_type,
                # Pseudonymous, per the contract. Never a name or an address.
                "actor_id": decision.actor_id,
                "decision": recorded.value,
                "reason": decision.reason,
            },
            correlation_id=correlation,
        )
    )
    await session.flush()


async def _publish_decision(
    evaluation: AssuranceEvaluation, decision: HumanDecision, correlation: str | None
) -> None:
    """Fan out HUMAN_DECISION_RECORDED.

    The durable record is already written and committed by the session dependency, so a
    transport outage is logged rather than failing an approval the operator has made. It is
    never swallowed silently.
    """
    from app.events.bus import get_event_bus

    try:
        bus = get_event_bus()
        await bus.publish(
            HumanDecisionRecorded(
                producer="api",
                correlation_id=correlation,
                evaluation_id=evaluation.id,
                decision=HumanDecisionType(decision.decision),
                actor_id=decision.actor_id,
                reason=decision.reason,
            )
        )
    except Exception as exc:
        log.error(
            "event_publication_failed",
            outcome="error",
            event_type="HUMAN_DECISION_RECORDED",
            assurance_id=evaluation.id,
            error_code=getattr(exc, "code", type(exc).__name__),
            detail=str(exc),
        )
