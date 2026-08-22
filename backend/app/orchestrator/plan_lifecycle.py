"""Plan lifecycle — STREAM A.

The seam between a persisted `plan` and Stream B's plan-level gate. Four jobs, and nothing else:

1. **Project** a persisted plan and its action-level evaluations into `PlanUnderReview`.
2. **Stamp** `plan.plan_hash` with `PlanUnderReview.hash()`.
3. **Evaluate** it through `plan_gate.evaluate_plan`, with coverage and exposure built from
   Stream C's rollup rather than from anything invented here.
4. **Record and enforce** a plan approval: persist `plan_approval` + `plan_approval_tier`, and
   answer "does that approval cover this task" through Stream B's `plan_approval_covers`.

There is deliberately **one plan hash in the system**. `PlanUnderReview.hash()` is it, because it
is what the approval gate compares — persisting a second, differently-canonicalised hash in
`plan.plan_hash` would create two identities for one plan, and the first time they disagreed the
approval would either cover work nobody reviewed or refuse work someone did. Stream C's
`app/db/plan_identity.py` was a second implementation of the same rule and has been deleted for
exactly that reason.

Coverage and exposure are **read from evidence, never estimated**. `CoverageDeclaration.declared`
is false unless the rollup is complete, and every `ExposureInputs` field left as `None` is treated
by Stream B as a breach rather than a zero. That asymmetry is the whole safety property: an
unknown exposure must never look like a small one.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.approval import (
    ApprovalCheck,
    ApprovalScope,
    ApprovalScopeKind,
    plan_approval_covers,
    tasks_still_needing_own_decision,
)
from app.assurance.blocking import blocking_kinds, is_approvable
from app.assurance.plan_contract import (
    PLAN_CONFIG_UNAVAILABLE,
    CoverageDeclaration,
    ExposureInputs,
    PlanAssuranceResult,
    PlanConfig,
    PlanUnderReview,
    TaskOutcome,
)
from app.assurance.plan_gate import LoadedPlanConfig, evaluate_plan, load_plan_config
from app.config import get_settings, resolve_repo_path
from app.db.scenario_queries import CascadeRollup
from app.errors import InvalidStateTransition
from app.models.cascade import PlanApproval, PlanApprovalTier
from app.models.enums import AssuranceDecision, RiskTier, TaskState
from app.models.workflow import AssuranceEvaluation, Plan
from app.models.workflow import PlanTask as PlanTaskRow
from app.observability.logging import get_logger

log = get_logger(__name__)

#: Selection states on `plan.selection_state`. A plan the operator has not chosen is a
#: `candidate`; exactly one per incident may be `selected`, enforced by a partial unique index.
SELECTION_CANDIDATE = "candidate"
SELECTION_SELECTED = "selected"
SELECTION_SUPERSEDED = "superseded"


# --------------------------------------------------------------------------- config


def load_plan_gate_config() -> LoadedPlanConfig | None:
    """Load the plan section of the configured assurance config, or None if it has none.

    Returns None rather than raising so a deployment still on `assurance.v1.yaml` keeps working:
    the action gate is unaffected and the plan gate reports `PLAN_CONFIG_MISSING`, which is the
    honest outcome. Defaulting the limits would invent an exposure budget nobody approved.
    """
    path = resolve_repo_path(get_settings().assurance_config_path)
    try:
        return load_plan_config(path)
    except Exception as exc:
        log.info("plan_config_unavailable", path=str(path), error=str(exc))
        return None


# ------------------------------------------------------------------- projection


def task_outcome_from_row(
    *,
    row: PlanTaskRow,
    evaluation: AssuranceEvaluation | None,
) -> TaskOutcome:
    """Project a persisted task and its recorded evaluation into a `TaskOutcome`.

    An unevaluated task is projected as `needs_human` with no blocking kinds. That is not a
    placeholder: a task nobody has assured genuinely cannot be authorised, and reporting it as
    executable because no evaluation said otherwise would authorise on absence of evidence.
    """
    if evaluation is None:
        return TaskOutcome(
            task_id=str(row.id),
            action_type=str(row.action_type),
            target_refs=list(row.target_refs or []),
            depends_on=[str(item) for item in (row.depends_on or [])],
            decision=AssuranceDecision.needs_human,
            risk_tier=RiskTier.high,
            blocking_kinds=[],
            approvable=False,
            evaluation_id=None,
        )

    # The engine's own replay helper, not a second copy of it. Authorising semantics must come
    # from what was recorded at decision time, and one implementation of that cannot drift.
    from app.orchestrator.engine import _result_from_row

    result = _result_from_row(evaluation)
    return TaskOutcome(
        task_id=str(row.id),
        action_type=str(row.action_type),
        target_refs=list(row.target_refs or []),
        depends_on=[str(item) for item in (row.depends_on or [])],
        decision=AssuranceDecision(str(evaluation.decision)),
        risk_tier=RiskTier(str(evaluation.risk_tier)),
        blocking_kinds=blocking_kinds(result),
        # Stream B's taxonomy decides this, not a property on the result: "approvable" means
        # "blocked only on risk", and risk-versus-evidence is exactly what `blocking` classifies.
        approvable=is_approvable(result),
        evaluation_id=int(evaluation.id),
    )


async def project_plan(
    session: AsyncSession, *, plan_id: int, group_reference: str
) -> PlanUnderReview:
    """Build `PlanUnderReview` from the persisted plan, in task order.

    Task order is preserved because it is meaningful — a plan that books a hotel before checking
    connections is a different plan — and `PlanUnderReview.hash()` depends on it.
    """
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise LookupError(f"plan {plan_id} not found")

    rows = (
        (
            await session.execute(
                select(PlanTaskRow)
                .where(PlanTaskRow.plan_id == plan_id)
                .order_by(PlanTaskRow.task_order, PlanTaskRow.id)
            )
        )
        .scalars()
        .all()
    )

    evaluations = {
        int(row.plan_task_id): row
        for row in (
            await session.execute(
                select(AssuranceEvaluation)
                .where(AssuranceEvaluation.plan_task_id.in_([r.id for r in rows] or [0]))
                .order_by(AssuranceEvaluation.plan_task_id, AssuranceEvaluation.id)
            )
        )
        .scalars()
        .all()
    }

    return PlanUnderReview(
        plan_id=plan_id,
        group_reference=group_reference,
        generator=str(plan.generator) if plan.generator else None,
        tasks=[
            task_outcome_from_row(row=row, evaluation=evaluations.get(int(row.id))) for row in rows
        ],
    )


async def stamp_plan_hash(session: AsyncSession, *, plan_id: int, group_reference: str) -> str:
    """Persist `PlanUnderReview.hash()` onto `plan.plan_hash` and return it.

    Called after the tasks exist, because a hash over an empty task list would be a signature on
    nothing that later appeared to cover whatever got added.
    """
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise LookupError(f"plan {plan_id} not found")

    review = await project_plan(session, plan_id=plan_id, group_reference=group_reference)
    plan.plan_hash = review.hash()
    await session.flush()
    return str(plan.plan_hash)


# --------------------------------------------------------------- coverage + exposure


def coverage_from_rollup(
    rollup: CascadeRollup, *, deferred: dict[str, str] | None = None
) -> CoverageDeclaration:
    """Build the coverage declaration from Stream C's rollup.

    `declared` is false unless the rollup is complete. A partial cascade genuinely does not know
    its own impacted set, and declaring coverage over a set that is still growing is how a plan
    comes to look like it addressed everything.
    """
    impacted = [f"flight:{flight_id}" for flight_id in rollup.member_flight_ids]
    impacted += [f"booking:{booking_id}" for booking_id in rollup.at_risk_booking_ids]
    impacted += [f"pairing:{pairing.pairing_reference}" for pairing in rollup.pairings]
    return CoverageDeclaration(
        declared=rollup.is_complete,
        impacted_refs=sorted(set(impacted)),
        deferred=dict(deferred or {}),
    )


def exposure_from_evidence(
    *,
    rollup: CascadeRollup,
    hotel_payload: dict[str, Any] | None = None,
    external_effects: int | None = None,
) -> ExposureInputs:
    """Build exposure from recorded evidence, leaving unknowns as `None`.

    `None` is not a gap to be filled with a convenient zero — Stream B treats it as a breach. An
    unknown cost must never read as a small cost, which is the failure this asymmetry exists to
    prevent.
    """
    rooms = None
    cost = None
    if hotel_payload:
        rooms = int(hotel_payload.get("rooms_allocated") or 0)
        cost = int(hotel_payload.get("total_cost_inr") or 0)

    return ExposureInputs(
        total_exposure_inr=cost,
        passengers_affected=rollup.passengers_affected,
        rooms_committed=rooms,
        external_effects=external_effects,
        unresolved_cohorts=[f"flight:{flight_id}" for flight_id in rollup.flights_without_incident],
    )


# ------------------------------------------------------------------ plan assurance


async def assure_plan(
    session: AsyncSession,
    *,
    plan_id: int,
    rollup: CascadeRollup,
    hotel_payload: dict[str, Any] | None = None,
    deferred: dict[str, str] | None = None,
    loaded: LoadedPlanConfig | None = None,
) -> PlanAssuranceResult:
    """Evaluate a persisted plan through Stream B's gate. Reads only; authorises nothing.

    `PlanAssuranceResult.authorises_no_action` is a `Literal[True]` in Stream B's contract, and
    that is the point: this call produces a summary an operator can approve, never permission to
    act. Execution still goes through the action gate for every task.
    """
    config: PlanConfig | None = None
    version = PLAN_CONFIG_UNAVAILABLE
    digest = PLAN_CONFIG_UNAVAILABLE
    active = loaded if loaded is not None else load_plan_gate_config()
    if active is not None:
        config = active.plan
        version = active.version
        digest = active.digest

    review = await project_plan(session, plan_id=plan_id, group_reference=rollup.group_reference)
    return evaluate_plan(
        plan=review,
        coverage=coverage_from_rollup(rollup, deferred=deferred),
        exposure=exposure_from_evidence(rollup=rollup, hotel_payload=hotel_payload),
        config=config,
        config_version=version,
        config_hash=digest,
    )


# ------------------------------------------------------------------ plan approval


async def record_plan_approval(
    session: AsyncSession,
    *,
    plan_id: int,
    incident_group_id: int | None,
    result: PlanAssuranceResult,
    actor_id: str,
    reason: str,
    decided_at: datetime,
) -> PlanApproval:
    """Persist a plan approval and the tiers it covers.

    Two refusals happen before anything is written, and both are refusals of the *request*, not
    of a task:

    * A plan blocked on failed evidence or a conflict cannot be approved. `may_approve_plan` names
      why: only risk is ever approvable.
    * A plan with no hash cannot be approved, because nothing could later prove what was signed.

    **One reconciliation, made explicit.** `may_approve_plan` refuses a plan whose own decision is
    `execute`, on the grounds that there is nothing at plan level awaiting a human — which is
    correct for the question it asks. But P2-D3 exists so a plan approval can cover the *action*
    level holds, and a plan can be admissible as an aggregate while several of its tasks are each
    held for a person. Refusing there would make plan approval unreachable in exactly the case it
    was designed for.

    So an approval is permitted when either:

    * the plan itself requires a human, and the only thing blocking it is risk; or
    * the plan is admissible **and** carries at least one held task that a plan approval could
      cover.

    What does not change is the evidence rule. A task blocked on evidence or conflict makes
    `tasks_authorised` FAIL, which puts a non-risk check in `result.blocking`, which
    `may_approve_plan` refuses — so the second branch cannot be used to approve around failed
    evidence. `plan_approval_tier` is additionally constrained by the database to low and medium,
    so P2-D3 is a storage guarantee rather than something this function has to remember.
    """
    from app.assurance.approval import may_approve_plan
    from app.assurance.plan_contract import PlanCheckName

    review_for_scope = await project_plan(
        session, plan_id=plan_id, group_reference=result.group_reference
    )
    coverable = [
        task
        for task in review_for_scope.tasks
        if task.needs_human
        and not task.blocked_on_evidence_or_conflict
        and task.risk_tier is not RiskTier.high
    ]
    non_risk_blocks = [name for name in result.blocking if name is not PlanCheckName.plan_risk]

    permission = may_approve_plan(result)
    if not permission.permitted and not (result.admissible and coverable and not non_risk_blocks):
        raise InvalidStateTransition(
            f"this plan cannot be approved: {permission.reason}",
            details={
                "refusal": permission.refusal.value if permission.refusal else None,
                "unresolved": permission.unresolved,
                "plan_id": plan_id,
            },
        )

    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise LookupError(f"plan {plan_id} not found")
    if not plan.plan_hash:
        raise InvalidStateTransition(
            "this plan has no hash, so an approval could not later prove what was signed",
            details={"plan_id": plan_id},
        )
    if result.plan_hash and result.plan_hash != plan.plan_hash:
        raise InvalidStateTransition(
            "the plan changed since it was evaluated, so this approval would cover a "
            "different plan than the one reviewed",
            details={
                "plan_id": plan_id,
                "evaluated_hash": result.plan_hash,
                "current_hash": plan.plan_hash,
            },
        )

    existing = (
        (await session.execute(select(PlanApproval).where(PlanApproval.plan_id == plan_id)))
        .scalars()
        .first()
    )
    if existing is not None:
        # Immutable, exactly like `human_decision`. A change of mind is a new plan, not an
        # edited signature.
        raise InvalidStateTransition(
            "this plan already carries an approval; a revised decision needs a new plan",
            details={"plan_id": plan_id, "plan_approval_id": existing.id},
        )

    policy = _approval_policy()
    review = await project_plan(session, plan_id=plan_id, group_reference=result.group_reference)
    covered = _covered_task_ids(review, policy_tiers=policy)

    approval = PlanApproval(
        plan_id=plan_id,
        incident_group_id=incident_group_id,
        plan_hash=str(plan.plan_hash),
        covered_task_ids=covered,
        gate_config_version=result.config_version,
        gate_config_hash=result.config_hash,
        actor_id=actor_id,
        reason=reason,
        decided_at=decided_at,
    )
    session.add(approval)
    await session.flush()

    for tier in policy:
        session.add(PlanApprovalTier(plan_approval_id=approval.id, risk_tier=tier.value))
    await session.flush()

    log.info(
        "plan_approval_recorded",
        plan_id=plan_id,
        plan_hash=plan.plan_hash,
        covered_task_ids=covered,
        tiers=[tier.value for tier in policy],
        actor_id=actor_id,
    )
    return approval


def _approval_policy() -> list[RiskTier]:
    """Tiers a plan approval may cover. Low and medium, never high — P2-D3.

    `high` is filtered out even if a config declared it, because the database would reject the
    row anyway. Two layers, because this is the rule most costly to get wrong.
    """
    loaded = load_plan_gate_config()
    if loaded is not None and loaded.plan is not None:
        return [tier for tier in loaded.plan.approval.covers_tiers if tier is not RiskTier.high]
    return [RiskTier.low, RiskTier.medium]


def _covered_task_ids(review: PlanUnderReview, *, policy_tiers: list[RiskTier]) -> list[str]:
    """Task ids the approval covers: in the plan, in a covered tier, and not blocked on evidence.

    **Task** ids, not evaluation ids — Stream B's `plan_approval_covers` compares
    `task.task_id`, and storing evaluation ids here would make every coverage check fail with a
    confusing "not in the approval's task list".

    Computed once at signing time and stored. Recomputing it at execution would let a task
    re-tiered from high to medium after signing slide silently inside the approval's scope.
    """
    covered = set(policy_tiers)
    return [
        task.task_id
        for task in review.tasks
        if task.risk_tier in covered and not task.blocked_on_evidence_or_conflict
    ]


async def approval_for_plan(session: AsyncSession, *, plan_id: int) -> PlanApproval | None:
    return (
        (await session.execute(select(PlanApproval).where(PlanApproval.plan_id == plan_id)))
        .scalars()
        .first()
    )


async def scope_for(session: AsyncSession, *, plan_id: int) -> ApprovalScope | None:
    """The persisted approval as Stream B's `ApprovalScope`, or None if unapproved."""
    approval = await approval_for_plan(session, plan_id=plan_id)
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
    return ApprovalScope(
        scope=ApprovalScopeKind.plan,
        actor_id=str(approval.actor_id),
        reason=str(approval.reason),
        granted_at=approval.decided_at,
        plan_hash=str(approval.plan_hash),
        covers_tiers=[RiskTier(str(tier)) for tier in tiers],
        covered_task_ids=[str(item) for item in (approval.covered_task_ids or [])],
        evaluation_id=None,
    )


async def plan_approval_for_task(
    session: AsyncSession,
    *,
    plan_id: int,
    plan_task_id: int,
    group_reference: str,
) -> tuple[PlanApproval | None, ApprovalCheck | None]:
    """Whether a plan approval authorises one task. Returns `(approval, check)`.

    `(None, None)` means no plan approval exists — the caller falls back to requiring the task's
    own human decision, which is the Phase 1 behaviour and stays the default.

    The four conditions are Stream B's, applied by Stream B's code. This function only supplies
    the persisted values, so there is one implementation of P2-D3 in the system.
    """
    scope = await scope_for(session, plan_id=plan_id)
    if scope is None:
        return None, None

    approval = await approval_for_plan(session, plan_id=plan_id)
    plan = await session.get(Plan, plan_id)
    review = await project_plan(session, plan_id=plan_id, group_reference=group_reference)
    task = next((item for item in review.tasks if item.task_id == str(plan_task_id)), None)
    if task is None or plan is None:
        return approval, None

    loaded = load_plan_gate_config()
    policy = (
        loaded.plan.approval
        if loaded is not None and loaded.plan is not None
        else _default_policy()
    )
    check = plan_approval_covers(
        approval=scope,
        task=task,
        plan_hash=str(plan.plan_hash or ""),
        policy=policy,
    )
    return approval, check


def _default_policy():
    from app.assurance.plan_contract import PlanApprovalPolicy

    return PlanApprovalPolicy()


async def tasks_needing_own_decision(
    session: AsyncSession, *, plan_id: int, group_reference: str
) -> list[str]:
    """Tasks a plan approval cannot cover, so the console can say so before anyone clicks.

    Empty when no plan approval exists — with no plan approval every held task needs its own
    decision anyway, and listing all of them would be noise rather than information.
    """
    scope = await scope_for(session, plan_id=plan_id)
    if scope is None:
        return []

    plan = await session.get(Plan, plan_id)
    review = await project_plan(session, plan_id=plan_id, group_reference=group_reference)
    loaded = load_plan_gate_config()
    policy = (
        loaded.plan.approval
        if loaded is not None and loaded.plan is not None
        else _default_policy()
    )
    return tasks_still_needing_own_decision(
        approval=scope,
        tasks=review.tasks,
        plan_hash=str(plan.plan_hash or "") if plan else "",
        policy=policy,
    )


# ---------------------------------------------------------------- plan selection


async def mark_selected(
    session: AsyncSession, *, plan_id: int, actor_id: str, selected_at: datetime
) -> None:
    """Mark one plan as the selected one for its incident, superseding any previous choice.

    A partial unique index allows exactly one selected plan per incident, so the previous
    selection is moved to `superseded` rather than deleted: the plan that was chosen and then
    replaced is part of the record of how the decision was reached.
    """
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise LookupError(f"plan {plan_id} not found")

    siblings = (
        (
            await session.execute(
                select(Plan).where(
                    Plan.incident_id == plan.incident_id,
                    Plan.id != plan_id,
                    Plan.selection_state == SELECTION_SELECTED,
                )
            )
        )
        .scalars()
        .all()
    )
    for sibling in siblings:
        sibling.selection_state = SELECTION_SUPERSEDED

    plan.selection_state = SELECTION_SELECTED
    plan.selected_at = selected_at
    plan.selected_by = actor_id
    await session.flush()


def held_task_ids(review: PlanUnderReview) -> list[str]:
    """Tasks the action gate held for a human. The set a plan approval could reduce."""
    return [task.task_id for task in review.tasks if task.needs_human]


async def open_task_states(session: AsyncSession, *, plan_id: int) -> dict[str, str]:
    """`task_id -> state` for every task in a plan, for the console's plan panel."""
    rows = (
        await session.execute(
            select(PlanTaskRow.id, PlanTaskRow.state)
            .where(PlanTaskRow.plan_id == plan_id)
            .order_by(PlanTaskRow.task_order)
        )
    ).all()
    return {str(task_id): str(state) for task_id, state in rows}


def summarise(result: PlanAssuranceResult, *, needing_own_decision: Sequence[str] = ()) -> dict:
    """The payload shape the console renders. Kept here so the API stays a pass-through."""
    return {
        "decision": result.decision.value,
        "plan_risk_tier": result.plan_risk_tier.value,
        "plan_id": result.plan_id,
        "plan_hash": result.plan_hash,
        "group_reference": result.group_reference,
        "task_count": result.task_count,
        "admissible": result.admissible,
        "requires_human": result.requires_human,
        "authorises_no_action": result.authorises_no_action,
        "checks": [
            {
                "name": check.name.value,
                "state": check.state.value,
                "reason_code": check.reason_code.value,
                "reason": check.reason,
                "tier": check.tier.value if check.tier else None,
                "offending_refs": list(check.offending_refs),
                "is_blocking": check.is_blocking,
            }
            for check in result.checks
        ],
        "blocking": list(result.blocking),
        "exposure": dict(result.exposure),
        "config_version": result.config_version,
        "config_hash": result.config_hash,
        "evaluated_at": result.evaluated_at.isoformat() if result.evaluated_at else None,
        "tasks_needing_own_decision": list(needing_own_decision),
        "note": (
            "A plan-level summary. It authorises no action: every task still passes the "
            "action gate, and a high-risk task always needs its own decision."
        ),
    }


def task_state_labels() -> dict[str, str]:
    return {state.value: state.value.replace("_", " ") for state in TaskState}
