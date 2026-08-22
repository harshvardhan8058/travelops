"""Plan approval — one operator act, many ordinary decisions.

**The single source of truth is `human_decision`.** One row per `assurance_evaluation`,
`assurance_id` still `UNIQUE NOT NULL`, and it remains the only thing `execute()` consults.
`execute()` does not know plan approvals exist. That is what keeps exactly one path to
execution while still letting an operator approve a network event in one action.

A plan approval therefore does not *authorise*; it **fans out**. At the moment the operator
signs, this module:

1. reads the evaluations that are **already awaiting** a human;
2. partitions them with Stream B's `plan_approval_covers`, which enforces P2-D3 mechanically;
3. writes one `human_decision` per covered evaluation, `scope='plan'`, sharing the actor and
   reason; and
4. returns the excluded ones with the reason each was refused.

**It never covers an evaluation produced later in the run.** Forward coverage would be a blank
cheque over actions nobody had seen, and it would contradict the operator being shown exactly
what their signature covers. A later `needs_human` needs a new decision.

The count written always equals the count claimed, because both come from the same partition.

Owner: Stream A. Coverage rules: Stream B.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.approval import (
    ApprovalRefusal,
    ApprovalScope,
    ApprovalScopeKind,
    may_approve_action,
    may_approve_plan,
    plan_approval_covers,
)
from app.assurance.plan_contract import (
    PlanApprovalPolicy,
    PlanAssuranceResult,
    TaskOutcome,
)
from app.config import Settings, get_settings
from app.errors import EntityNotFound, InvalidStateTransition
from app.models.cascade import PlanApproval, PlanApprovalTier
from app.models.enums import HumanDecisionType, RiskTier
from app.models.workflow import (
    AssuranceEvaluation,
    DecisionLog,
    HumanDecision,
    Incident,
    Plan,
    PlanTask,
)
from app.orchestrator import assurance_adapter
from app.orchestrator.plan_assurance import PlanAssuranceService, load_plan_configuration

log = structlog.get_logger(__name__)

ACTOR_HUMAN = "human"
STAGE_ASSURE = "assure"

SCOPE_ACTION = "action"
SCOPE_PLAN = "plan"

#: The refusals that mean "a human may not authorise this at all". Both say the gate blocked on
#: something a signature cannot supply: a missing or stale fact, or a collision with another
#: action. P2-D3's rule in code form.
_UNAPPROVABLE = frozenset(
    {ApprovalRefusal.NOT_APPROVABLE_EVIDENCE, ApprovalRefusal.NOT_APPROVABLE_CONFLICT}
)


@dataclass
class CoveredEvaluation:
    evaluation_id: int
    plan_task_id: int
    incident_reference: str
    action_type: str
    risk_tier: str
    human_decision_id: int


@dataclass
class ExcludedEvaluation:
    evaluation_id: int
    plan_task_id: int
    incident_reference: str
    action_type: str
    risk_tier: str
    #: `HIGH_RISK_NEEDS_OWN_DECISION`, `NOT_APPROVABLE_EVIDENCE`, `TIER_NOT_COVERED`, ...
    reason_code: str
    reason: str


@dataclass
class ApprovalOutcome:
    plan_approval_id: int | None
    plan_hash: str
    covered: list[CoveredEvaluation] = field(default_factory=list)
    excluded: list[ExcludedEvaluation] = field(default_factory=list)
    refusal: str | None = None
    refusal_reason: str | None = None
    replayed: bool = False

    @property
    def covered_count(self) -> int:
        return len(self.covered)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)


class PlanApprovalService:
    """Records a plan approval and materialises the decisions it covers."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._plans = PlanAssuranceService(session, settings=self._settings)

    # ----------------------------------------------------------------------- partitioning

    async def _outcome_for(
        self, evaluation: AssuranceEvaluation
    ) -> tuple[TaskOutcome, PlanTask, Incident]:
        task = await self._session.get(PlanTask, evaluation.plan_task_id)
        if task is None:
            raise EntityNotFound(
                "the task this evaluation belongs to is missing",
                details={"assurance_id": evaluation.id},
            )
        incident = await self._plans.incident_for_evaluation(evaluation)
        if incident is None:
            raise EntityNotFound(
                "the incident this evaluation belongs to is missing",
                details={"assurance_id": evaluation.id},
            )
        result = assurance_adapter.result_from_row(evaluation)
        from app.assurance.plan_gate import task_outcome_from

        outcome = task_outcome_from(
            task_id=str(task.id),
            action_type=task.action_type,
            result=result,
            target_refs=list(task.target_refs or []),
            evaluation_id=evaluation.id,
        )
        return outcome, task, incident

    async def preview(
        self, *, group_id: int, plan: Plan, plan_result: PlanAssuranceResult
    ) -> ApprovalOutcome:
        """What a plan approval *would* cover. Writes nothing.

        The console renders this before the operator commits, so the coverage list and the
        excluded list are both visible and the operator can see the control was *unable* to
        cover something rather than that it chose not to.
        """
        return await self._partition(
            group_id=group_id,
            plan=plan,
            plan_result=plan_result,
            commit=False,
            actor_id="preview",
            reason="preview",
        )

    async def _partition(
        self,
        *,
        group_id: int,
        plan: Plan,
        plan_result: PlanAssuranceResult,
        commit: bool,
        actor_id: str,
        reason: str,
    ) -> ApprovalOutcome:
        loaded = load_plan_configuration(self._settings)
        policy: PlanApprovalPolicy = loaded.plan.approval if loaded else PlanApprovalPolicy()
        plan_hash = plan_result.plan_hash

        outcome = ApprovalOutcome(plan_approval_id=None, plan_hash=plan_hash)

        # Stream B decides whether a plan is approvable at all. A plan blocked on anything
        # other than risk is refused: no decision is written.
        gate = may_approve_plan(plan_result)
        plan_refused = not gate.permitted
        if plan_refused:
            outcome.refusal = gate.refusal.value if gate.refusal else "NOT_APPROVABLE"
            outcome.refusal_reason = gate.reason
            log.info(
                "plan_approval_refused",
                group_id=group_id,
                refusal=outcome.refusal,
                unresolved=gate.unresolved,
            )
            if commit:
                return outcome
            # A preview still itemises what is waiting, with the reason each item cannot be
            # covered. Returning an empty list here would tell the operator "nothing to
            # approve" when the truth is "eight things, each needing its own decision" — and
            # the whole point of the excluded list is that a reviewer can see the control was
            # *unable* to cover something rather than assume it chose not to.

        awaiting = await self._plans.awaiting_evaluations(group_id)
        if not awaiting:
            outcome.refusal = outcome.refusal or "NOTHING_TO_APPROVE"
            outcome.refusal_reason = (
                outcome.refusal_reason or "no evaluation in this group is waiting on a person"
            )
            return outcome

        pairs: list[tuple[AssuranceEvaluation, TaskOutcome, PlanTask, Incident]] = []
        for evaluation in awaiting:
            task_outcome, task, incident = await self._outcome_for(evaluation)
            pairs.append((evaluation, task_outcome, task, incident))

        scope = ApprovalScope(
            scope=ApprovalScopeKind.plan,
            actor_id=actor_id,
            reason=reason,
            plan_hash=plan_hash,
            covers_tiers=list(policy.covers_tiers),
            covered_task_ids=[str(task.id) for _, _, task, _ in pairs],
        )

        covered_now: list[tuple[AssuranceEvaluation, TaskOutcome, PlanTask, Incident]] = []
        for evaluation, task_outcome, task, incident in pairs:
            check = plan_approval_covers(
                approval=scope, task=task_outcome, plan_hash=plan_hash, policy=policy
            )
            if check.permitted and not plan_refused:
                covered_now.append((evaluation, task_outcome, task, incident))
            else:
                reason_code = check.refusal.value if check.refusal else "REFUSED"
                reason = check.reason or "not covered by a plan approval"
                if plan_refused and check.permitted:
                    # The task itself is coverable; the PLAN is not. Say which, or an operator
                    # reads "high risk" onto a task that is nothing of the kind.
                    reason_code = outcome.refusal or "PLAN_NOT_APPROVABLE"
                    reason = (
                        "this task is within plan-approval scope, but the plan itself is not "
                        f"approvable: {outcome.refusal_reason}"
                    )
                outcome.excluded.append(
                    ExcludedEvaluation(
                        evaluation_id=evaluation.id,
                        plan_task_id=task.id,
                        incident_reference=incident.reference,
                        action_type=task.action_type,
                        risk_tier=task_outcome.risk_tier.value,
                        reason_code=reason_code,
                        reason=reason,
                    )
                )

        if not covered_now:
            outcome.refusal = outcome.refusal or "NOTHING_TO_APPROVE"
            outcome.refusal_reason = outcome.refusal_reason or (
                "every evaluation awaiting a person needs its own decision: "
                f"{len(outcome.excluded)} excluded"
            )
            return outcome

        if not commit:
            for evaluation, task_outcome, task, incident in covered_now:
                outcome.covered.append(
                    CoveredEvaluation(
                        evaluation_id=evaluation.id,
                        plan_task_id=task.id,
                        incident_reference=incident.reference,
                        action_type=task.action_type,
                        risk_tier=task_outcome.risk_tier.value,
                        human_decision_id=0,
                    )
                )
            return outcome

        approval_row = PlanApproval(
            plan_id=plan.id,
            incident_group_id=group_id,
            plan_hash=plan_hash,
            covered_task_ids=[str(task.id) for _, _, task, _ in covered_now],
            gate_config_version=plan_result.config_version,
            gate_config_hash=plan_result.config_hash,
            actor_id=actor_id,
            reason=reason,
            decided_at=datetime.now(UTC),
        )
        self._session.add(approval_row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise InvalidStateTransition(
                "this plan version already carries a plan approval",
                details={
                    "plan_id": plan.id,
                    "plan_hash": plan_hash,
                    "resolution": "a re-planned plan hashes differently and needs its own approval",
                },
            ) from exc

        for tier in policy.covers_tiers:
            # The CHECK constraint refuses `high` at the database level, so P2-D3's central
            # rule survives even a future code path that forgets it.
            if tier is RiskTier.high:
                continue
            self._session.add(
                PlanApprovalTier(plan_approval_id=approval_row.id, risk_tier=tier.value)
            )
        await self._session.flush()

        for evaluation, task_outcome, task, incident in covered_now:
            decision = HumanDecision(
                assurance_id=evaluation.id,
                decision=HumanDecisionType.approved,
                actor_id=actor_id,
                reason=reason,
                decided_at=approval_row.decided_at,
                scope=SCOPE_PLAN,
                plan_approval_id=approval_row.id,
            )
            self._session.add(decision)
            await self._session.flush()
            outcome.covered.append(
                CoveredEvaluation(
                    evaluation_id=evaluation.id,
                    plan_task_id=task.id,
                    incident_reference=incident.reference,
                    action_type=task.action_type,
                    risk_tier=task_outcome.risk_tier.value,
                    human_decision_id=decision.id,
                )
            )
            await self._journal(
                incident=incident,
                evaluation=evaluation,
                decision=decision,
                approval=approval_row,
                action_type=task.action_type,
            )

        outcome.plan_approval_id = approval_row.id
        log.info(
            "plan_approval_recorded",
            group_id=group_id,
            plan_id=plan.id,
            plan_approval_id=approval_row.id,
            actor=actor_id,
            covered=outcome.covered_count,
            excluded=outcome.excluded_count,
        )
        return outcome

    async def approve(
        self,
        *,
        group_id: int,
        plan: Plan,
        plan_result: PlanAssuranceResult,
        actor_id: str,
        reason: str,
    ) -> ApprovalOutcome:
        """Record the approval and write one decision per covered evaluation."""
        existing = (
            (
                await self._session.execute(
                    select(PlanApproval).where(
                        PlanApproval.plan_id == plan.id,
                        PlanApproval.plan_hash == plan_result.plan_hash,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return await self._replay(existing, plan_result)

        return await self._partition(
            group_id=group_id,
            plan=plan,
            plan_result=plan_result,
            commit=True,
            actor_id=actor_id,
            reason=reason,
        )

    async def _replay(
        self, approval: PlanApproval, plan_result: PlanAssuranceResult
    ) -> ApprovalOutcome:
        """Return the recorded approval rather than writing a second one."""
        outcome = ApprovalOutcome(
            plan_approval_id=approval.id, plan_hash=approval.plan_hash, replayed=True
        )
        stmt = select(HumanDecision).where(HumanDecision.plan_approval_id == approval.id)
        for decision in (await self._session.execute(stmt)).scalars():
            evaluation = await self._session.get(AssuranceEvaluation, decision.assurance_id)
            if evaluation is None:
                continue
            task = await self._session.get(PlanTask, evaluation.plan_task_id)
            incident = await self._plans.incident_for_evaluation(evaluation)
            outcome.covered.append(
                CoveredEvaluation(
                    evaluation_id=evaluation.id,
                    plan_task_id=evaluation.plan_task_id,
                    incident_reference=incident.reference if incident else "",
                    action_type=task.action_type if task else "",
                    risk_tier=evaluation.risk_tier,
                    human_decision_id=decision.id,
                )
            )
        return outcome

    # ------------------------------------------------------------------------- journalling

    async def _journal(
        self,
        *,
        incident: Incident,
        evaluation: AssuranceEvaluation,
        decision: HumanDecision,
        approval: PlanApproval,
        action_type: str,
    ) -> None:
        """Record the human's act, attributed to the human.

        `actor` is `human`, which `_actor_kind` maps to `human` — the Phase 1 fix, unchanged.
        The wording says the approval covered this action, so a reviewer can tell a plan-wide
        signature from a per-action one without reading the schema.
        """
        self._session.add(
            DecisionLog(
                incident_id=incident.id,
                occurred_at=decision.decided_at,
                stage=STAGE_ASSURE,
                actor=ACTOR_HUMAN,
                event_type="HUMAN_DECISION_RECORDED",
                summary=(
                    f"Operator {decision.actor_id} approved the recovery plan for "
                    f"{approval.plan_hash[:12]}; this covered {action_type} on "
                    f"{incident.reference}"
                ),
                detail={
                    "assurance_id": evaluation.id,
                    "human_decision_id": decision.id,
                    "plan_approval_id": approval.id,
                    "scope": SCOPE_PLAN,
                    "actor_id": decision.actor_id,
                    "reason": decision.reason,
                    "risk_tier": evaluation.risk_tier,
                    "action_type": action_type,
                },
                correlation_id=None,
            )
        )
        await self._session.flush()


def enforce_action_approval(evaluation: AssuranceEvaluation) -> None:
    """Refuse an action-level approval the gate does not permit.

    P2-D3: approval covers risk, never failed evidence, stale sources, unresolved entities or
    policy failure. Stream B's `may_approve_action` permits only when the block is risk-only,
    so this is the whole enforcement — the rule is not restated here, it is asked.

    Called from `POST /assurance/{id}/decision` so an operator cannot click past an
    evidence-blocked action even with a direct API call. The UI hiding the button is not a
    control.
    """
    result = assurance_adapter.result_from_row(evaluation)
    check = may_approve_action(result)
    if check.permitted:
        return

    # Scoped deliberately to the two refusals P2-D3 is about.
    #
    # `NOTHING_TO_APPROVE` — the gate already authorised this action — is NOT refused here. It is
    # redundant rather than dangerous, it was permitted throughout Phase 1, and turning it into a
    # 409 would be a behaviour change P2-D3 does not ask for. What P2-D3 forbids is an approval
    # standing in for evidence, and that is exactly what these two codes mean.
    if check.refusal not in _UNAPPROVABLE:
        log.info(
            "human_decision_redundant",
            assurance_id=evaluation.id,
            refusal=check.refusal.value if check.refusal else None,
            note="the gate had already authorised this action; recording the endorsement anyway",
        )
        return

    raise InvalidStateTransition(
        "this evaluation cannot be approved by a human",
        details={
            "assurance_id": evaluation.id,
            "refusal": check.refusal.value if check.refusal else "NOT_APPROVABLE",
            "reason": check.reason,
            "unresolved": check.unresolved,
            "resolution": (
                "approval covers risk, never failed evidence. Fix the evidence, or re-run the "
                "gate once the underlying fact is available."
            ),
        },
    )


def approval_payload(outcome: ApprovalOutcome) -> dict[str, Any]:
    """The response shape. `covered_count` always equals the decisions actually written."""
    return {
        "plan_approval_id": outcome.plan_approval_id,
        "plan_hash": outcome.plan_hash,
        "covered": [vars(item) for item in outcome.covered],
        "excluded": [vars(item) for item in outcome.excluded],
        "covered_count": outcome.covered_count,
        "excluded_count": outcome.excluded_count,
        "refusal": outcome.refusal,
        "refusal_reason": outcome.refusal_reason,
        "replayed": outcome.replayed,
    }
