"""Workflow engine — STREAM A.

The orchestrator is the brain; the model is not. This module owns incident state, task
ordering, dependency resolution, idempotency, safety limits, human approvals and audit
correlation. It contains no open-ended language reasoning, evaluates no assurance check and
performs no domain work.

Three boundaries are enforced here and nowhere else, which is why they are worth stating
plainly:

1. **Authorisation.** A task reaches a service only through `app/orchestrator/assurance_adapter`,
   which asks Stream B's gate. There is no second path, no override and no bypass. An
   unavailable gate is a hard block.
2. **Execution.** Domain work happens in Stream C's deterministic services, reached through
   `app/orchestrator/dispatch`. When a service does not exist yet, dispatch refuses
   explicitly rather than reporting a success that did not happen.
3. **The record.** Every step appends to `decision_log` with actor, correlation ID and
   evidence, in the same transaction as the state change. The log is the durable audit
   trail; the event bus is a fan-out channel on top of it.

State transitions go through `assert_transition()` in `state.py`, so an illegal move raises
`409 INVALID_STATE_TRANSITION` instead of quietly corrupting an incident.

Owner: Stream A.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contract import PlanTask
from app.assurance.contract import AssuranceResult
from app.config import ResolvedModes, Settings, get_modes, get_settings
from app.errors import AssuranceBlocked, EntityNotFound, WorkflowLimitExceeded
from app.events.types import (
    ActionCompleted,
    AssuranceEvaluated,
    IncidentOpened,
    IncidentResolved,
    PlanProposed,
    RecoveryBlocked,
)
from app.models.enums import (
    ActionStatus,
    ActionType,
    AssuranceDecision,
    HumanDecisionType,
    IncidentState,
    TaskState,
    TriggerType,
)
from app.models.reference import Flight
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    DecisionLog,
    HumanDecision,
    Incident,
    Plan,
)
from app.models.workflow import PlanTask as PlanTaskRow
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator import assurance_adapter, dispatch
from app.orchestrator.limits import Limits, check_step_budget
from app.orchestrator.playbook import (
    FALLBACK_GENERATOR,
    FALLBACK_RATIONALE,
    playbook_for,
)
from app.orchestrator.state import assert_transition, is_terminal
from app.services.base import ServiceResult

log = get_logger(__name__)

ACTOR_ORCHESTRATOR = "orchestrator"
ACTOR_GATE = "assurance_gate"
PRODUCER = "orchestrator"

# Timeline stages. Matches the vocabulary the committed timeline fixture already uses.
STAGE_DETECT = "detect"
STAGE_ASSESS = "assess"
STAGE_PLAN = "plan"
STAGE_ASSURE = "assure"
STAGE_EXECUTE = "execute"
STAGE_RESOLVE = "resolve"

#: Task states that no longer need work.
_TASK_SETTLED = frozenset(
    {TaskState.succeeded, TaskState.failed, TaskState.skipped, TaskState.rejected}
)
#: Task states that count as a completed unit of work for resolution purposes.
_TASK_DONE_WELL = frozenset({TaskState.succeeded, TaskState.skipped})


@dataclass
class WorkflowContext:
    """Everything the engine needs to run one incident forward.

    Wave 0 fixed the first four fields; the rest are additive with defaults so existing
    callers keep working.
    """

    incident_id: int
    incident_reference: str
    state: IncidentState
    correlation_id: str
    steps_taken: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    flight_id: int | None = None
    trigger_type: str | None = None
    plan_id: int | None = None
    #: Set when a state change is refused or deferred, so a caller knows why a run stopped.
    last_note: str | None = None


@dataclass
class _AssuranceOutcome:
    """An evaluation plus whether the gate actually produced it.

    The distinction matters. A gate that says `needs_human` is working correctly and an
    operator can approve. A gate that could not run at all is a different situation: there
    is nobody who may approve past a broken authorisation boundary, so the incident is
    blocked rather than queued for approval. Collapsing the two would let a missing gate
    look like a routine approval request.
    """

    result: AssuranceResult
    gate_available: bool
    unavailable_reason: str | None = None


@dataclass
class ExecutionOutcome:
    """Result of one guarded dispatch, plus the rows that authorise it."""

    result: ServiceResult
    action_id: int
    assurance_id: int
    human_decision_id: int | None
    idempotency_key: str
    #: True when this returned a previously recorded result instead of acting again.
    replayed: bool = False


class Orchestrator:
    """Deterministic control plane. Contains no open-ended language reasoning."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        bus: Any | None = None,
        settings: Settings | None = None,
        modes: ResolvedModes | None = None,
        limits: Limits | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._bus = bus
        self._settings = settings or get_settings()
        self._modes = modes
        self._limits = limits or Limits.from_settings(self._settings)
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def modes(self) -> ResolvedModes:
        # Resolved lazily: constructing an Orchestrator must not fail on config the caller
        # may never touch, but reading modes must always give the real answer.
        if self._modes is None:
            self._modes = get_modes()
        return self._modes

    # ------------------------------------------------------------------ incident open

    async def open_incident(
        self,
        flight_id: int,
        trigger_type: str,
        *,
        severity: str = "high",
        group_id: int | None = None,
        prediction_id: int | None = None,
        correlation_id: str | None = None,
        demo_dataset_id: str | None = None,
        evidence_refs: Sequence[str] | None = None,
    ) -> WorkflowContext:
        """Create an incident, or return the existing active one for this flight.

        Deduplication has two layers, and both are needed. The query catches the ordinary
        case. The partial unique index `uq_incident_active_per_flight` catches the race —
        two pollers arriving inside the same millisecond — by turning the duplicate into a
        database error, which is recovered here rather than surfaced. A 60-second weather
        poll must not open 60 incidents an hour.
        """
        correlation = correlation_id or correlation_id_var.get() or _new_correlation_id()
        token = correlation_id_var.set(correlation)
        try:
            existing = await self._active_incident(flight_id)
            if existing is not None:
                return await self._suppressed(existing, correlation, reason="already_active")

            flight = await self._session.get(Flight, flight_id)
            if flight is None:
                raise EntityNotFound("flight not found", details={"flight_id": flight_id})

            trigger = _coerce_trigger(trigger_type)
            opened_at = self._now()
            incident = Incident(
                reference=await self._next_reference(flight, opened_at),
                group_id=group_id,
                flight_id=flight_id,
                prediction_id=prediction_id,
                trigger_type=trigger,
                severity=severity,
                state=IncidentState.detected,
                opened_at=opened_at,
                demo_dataset_id=demo_dataset_id or self._settings.demo_dataset_id,
            )

            savepoint = await self._session.begin_nested()
            try:
                self._session.add(incident)
                await self._session.flush()
                await savepoint.commit()
            except IntegrityError:
                # The index fired: another writer opened this incident first.
                await savepoint.rollback()
                existing = await self._active_incident(flight_id)
                if existing is None:
                    raise
                return await self._suppressed(existing, correlation, reason="lost_open_race")

            refs = list(evidence_refs or [])
            ctx = WorkflowContext(
                incident_id=incident.id,
                incident_reference=incident.reference,
                state=IncidentState.detected,
                correlation_id=correlation,
                evidence_refs=refs,
                flight_id=flight_id,
                trigger_type=trigger.value,
            )
            await self._journal(
                ctx,
                stage=STAGE_DETECT,
                actor=ACTOR_ORCHESTRATOR,
                event_type="INCIDENT_OPENED",
                summary=(
                    f"Opened {incident.reference} for "
                    f"{flight.flight_number} ({flight.origin_icao}→{flight.destination_icao})"
                ),
                detail={
                    "trigger_type": trigger.value,
                    "severity": severity,
                    "deduplicated": False,
                    "dedupe_mechanism": "uq_incident_active_per_flight",
                },
            )
            await self._publish(
                IncidentOpened(
                    producer=PRODUCER,
                    correlation_id=correlation,
                    incident_id=incident.id,
                    incident_group_id=group_id,
                    incident_reference=incident.reference,
                    flight_id=flight_id,
                    trigger_type=trigger,
                    affected_entity_refs=[f"flight:{flight_id}", *refs],
                ),
                ctx,
            )
            await self._session.commit()
            log.info(
                "incident_opened",
                incident_reference=incident.reference,
                flight_id=flight_id,
                trigger_type=trigger.value,
                outcome="success",
            )
            return ctx
        finally:
            correlation_id_var.reset(token)

    async def _suppressed(
        self, incident: Incident, correlation: str, *, reason: str
    ) -> WorkflowContext:
        """Record that a duplicate open was refused, and return the original incident."""
        ctx = self._context_for(incident, correlation)
        await self._journal(
            ctx,
            stage=STAGE_DETECT,
            actor=ACTOR_ORCHESTRATOR,
            event_type="INCIDENT_OPEN_SUPPRESSED",
            summary=(
                f"Duplicate open refused; {incident.reference} is already active "
                f"in '{incident.state}'"
            ),
            detail={"deduplicated": True, "reason": reason},
        )
        await self._session.commit()
        log.info(
            "incident_open_suppressed",
            incident_reference=incident.reference,
            reason=reason,
            outcome="duplicate",
        )
        return ctx

    async def _active_incident(self, flight_id: int) -> Incident | None:
        stmt = (
            select(Incident)
            .where(
                Incident.flight_id == flight_id,
                Incident.state.notin_([s.value for s in IncidentState.terminal()]),
            )
            .order_by(Incident.id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def _next_reference(self, flight: Flight, opened_at: datetime) -> str:
        """INC-YYYY-MMDD-ICAO-NN, matching the committed fixtures."""
        prefix = f"INC-{opened_at:%Y-%m%d}-{flight.origin_icao}"
        stmt = select(Incident.reference).where(Incident.reference.like(f"{prefix}-%"))
        highest = 0
        for reference in (await self._session.execute(stmt)).scalars():
            suffix = reference.rsplit("-", 1)[-1]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return f"{prefix}-{highest + 1:02d}"

    def _context_for(self, incident: Incident, correlation: str) -> WorkflowContext:
        return WorkflowContext(
            incident_id=incident.id,
            incident_reference=incident.reference,
            state=IncidentState(incident.state),
            correlation_id=correlation,
            flight_id=incident.flight_id,
            trigger_type=str(incident.trigger_type),
        )

    async def load_context(
        self, incident_id: int, *, correlation_id: str | None = None
    ) -> WorkflowContext:
        """Rebuild a context from durable state, so a run can resume after a restart."""
        incident = await self._session.get(Incident, incident_id)
        if incident is None:
            raise EntityNotFound("incident not found", details={"incident_id": incident_id})
        correlation = correlation_id or correlation_id_var.get() or _new_correlation_id()
        ctx = self._context_for(incident, correlation)
        plan = await self._current_plan(incident_id)
        if plan is not None:
            ctx.plan_id = plan.id
        return ctx

    # ------------------------------------------------------------------------- planning

    async def propose_tasks(self, ctx: WorkflowContext) -> list[PlanTask]:
        """Get a plan from the deterministic playbook, persisting it as a Plan row.

        The fallback comes FIRST and unconditionally in this slice. The Planner agent
        arrives later and will be an improvement on this, not a replacement for it: with
        `LLM_MODE=off` the playbook alone must still produce a usable plan.

        Every task's action is an `ActionType`, validated by the `PlanTask` contract before
        anything is persisted, so an unknown action type cannot reach assurance.
        """
        steps = playbook_for(ctx.trigger_type or TriggerType.other)
        tasks = [
            PlanTask(
                action=step.action,
                target_refs=self._target_refs(ctx),
                inputs=dict(step.inputs),
                depends_on=[dependency.value for dependency in step.depends_on],
            )
            for step in steps
        ]

        plan = Plan(
            incident_id=ctx.incident_id,
            generated_at=self._now(),
            generator=FALLBACK_GENERATOR,
            # No model was involved, so there is no prompt version and no self-report.
            prompt_version=None,
            model_self_report=None,
            rationale=FALLBACK_RATIONALE,
            raw_response=None,
            retrieved_incident_ids=[],
        )
        self._session.add(plan)
        await self._session.flush()

        rows: dict[str, PlanTaskRow] = {}
        for order, task in enumerate(tasks, start=1):
            row = PlanTaskRow(
                plan_id=plan.id,
                action_type=task.action.value,
                task_order=order,
                depends_on=[],
                target_refs=list(task.target_refs),
                inputs=dict(task.inputs),
                state=TaskState.proposed,
            )
            self._session.add(row)
            rows[task.action.value] = row
        await self._session.flush()

        # Resolve action-name dependencies to persisted task IDs, so the stored plan holds
        # no dangling references. Matches the fixture, which stores task-id strings.
        for task in tasks:
            row = rows[task.action.value]
            row.depends_on = [str(rows[name].id) for name in task.depends_on if name in rows]
        await self._session.flush()

        ctx.plan_id = plan.id
        task_ids = [rows[task.action.value].id for task in tasks]
        await self._journal(
            ctx,
            stage=STAGE_PLAN,
            actor=ACTOR_ORCHESTRATOR,
            event_type="PLAN_PROPOSED",
            summary=f"{len(tasks)} tasks proposed by the deterministic playbook",
            detail={
                "plan_id": plan.id,
                "generator": FALLBACK_GENERATOR,
                "prompt_version": None,
                "model_self_report": None,
                "llm_mode": self.modes.llm.value,
                "actions": [task.action.value for task in tasks],
            },
        )
        await self._publish(
            PlanProposed(
                producer=PRODUCER,
                correlation_id=ctx.correlation_id,
                incident_id=ctx.incident_id,
                plan_id=plan.id,
                generator=FALLBACK_GENERATOR,
                prompt_version=None,
                task_ids=task_ids,
                evidence_refs=list(ctx.evidence_refs),
            ),
            ctx,
        )
        return tasks

    def _target_refs(self, ctx: WorkflowContext) -> list[str]:
        refs = [f"incident:{ctx.incident_reference}"]
        if ctx.flight_id is not None:
            refs.append(f"flight:{ctx.flight_id}")
        return refs

    # ------------------------------------------------------------------------ assurance

    async def assure(self, ctx: WorkflowContext, task: PlanTask) -> AssuranceResult:
        """Run the Decision Assurance Gate. Delegates to Stream B's gate.

        Returns the gate's result, or a refusal record when the gate could not evaluate.
        Never returns an executable result the gate did not produce.
        """
        return (await self._assure(ctx, task)).result

    async def _assure(self, ctx: WorkflowContext, task: PlanTask) -> _AssuranceOutcome:
        evidence = list(ctx.evidence_refs)

        if not self.modes.workflow_executable:
            # config.py already resolved this. Missing gate config blocks execution
            # outright: docs/26-implementation-contracts.md, "configuration validation".
            reason = "assurance config is unavailable; no action may be authorised"
            return _AssuranceOutcome(
                result=assurance_adapter.refusal(reason, evidence_refs=evidence),
                gate_available=False,
                unavailable_reason=reason,
            )

        try:
            result = await assurance_adapter.evaluate(
                action_type=task.action.value,
                target_refs=list(task.target_refs),
                inputs=dict(task.inputs),
                evidence_refs=evidence,
                incident_state=ctx.state.value,
                config=assurance_adapter.load_config(),
            )
        except assurance_adapter.GateUnavailableError as exc:
            log.error(
                "assurance_gate_unavailable",
                outcome="error",
                incident_reference=ctx.incident_reference,
                action_type=task.action.value,
                detail=exc.detail,
                reason=exc.reason,
            )
            return _AssuranceOutcome(
                result=assurance_adapter.refusal(
                    exc.reason,
                    config_version=self.modes.assurance_config_version,
                    config_hash=self.modes.assurance_config_hash,
                    evidence_refs=evidence,
                ),
                gate_available=False,
                unavailable_reason=exc.reason,
            )
        return _AssuranceOutcome(result=result, gate_available=True)

    async def _record_assurance(
        self, ctx: WorkflowContext, task_row: PlanTaskRow, outcome: _AssuranceOutcome
    ) -> AssuranceEvaluation:
        """Persist the immutable evaluation. Never updated; a correction is a new row."""
        result = outcome.result
        evaluation = AssuranceEvaluation(
            plan_task_id=task_row.id,
            decision=result.decision,
            risk_tier=result.risk_tier,
            check_results={
                check.name.value: {
                    "state": check.state.value,
                    "reason_code": check.reason_code.value,
                    "reason": check.reason,
                    "tier": check.tier.value if check.tier else None,
                }
                for check in result.checks
            },
            blocking_reasons=[name.value for name in result.blocking],
            evidence_refs=list(result.evidence_refs),
            config_version=result.config_version,
            config_hash=result.config_hash,
            evaluated_at=result.evaluated_at,
        )
        self._session.add(evaluation)
        await self._session.flush()

        summary = f"{task_row.action_type} → {result.decision.value}"
        if not outcome.gate_available:
            summary = f"{task_row.action_type} → refused: {outcome.unavailable_reason}"
        await self._journal(
            ctx,
            stage=STAGE_ASSURE,
            actor=ACTOR_GATE,
            event_type="ASSURANCE_EVALUATED",
            summary=summary,
            detail={
                "evaluation_id": evaluation.id,
                "plan_task_id": task_row.id,
                "decision": result.decision.value,
                "risk_tier": result.risk_tier.value,
                "blocking": [name.value for name in result.blocking],
                "config_version": result.config_version,
                "config_hash": result.config_hash,
                "gate_available": outcome.gate_available,
            },
        )
        await self._publish(
            AssuranceEvaluated(
                producer=PRODUCER,
                correlation_id=ctx.correlation_id,
                incident_id=ctx.incident_id,
                evaluation_id=evaluation.id,
                plan_task_id=task_row.id,
                decision=result.decision,
                risk_tier=result.risk_tier.value,
                check_results=evaluation.check_results,
                blocking_reasons=evaluation.blocking_reasons,
                config_version=result.config_version,
                config_hash=result.config_hash,
            ),
            ctx,
        )
        return evaluation

    # ------------------------------------------------------------------------ execution

    async def execute(
        self,
        ctx: WorkflowContext,
        task: PlanTask,
        assurance: AssuranceResult,
        *,
        evaluation_id: int | None = None,
        plan_task_id: int | None = None,
    ) -> ExecutionOutcome:
        """Dispatch to the owning deterministic service, under two hard preconditions.

        1. `assurance.executable` is False -> refuse. No side effect without authorisation.
        2. The gate said `needs_human` -> an `approved` human decision for **that same
           evaluation** is required. A rejected decision cannot be reused, and an approval
           for a different evaluation does not transfer.

        A replay of an already-recorded idempotency key returns the original result rather
        than acting a second time.
        """
        task_row = await self._resolve_task_row(ctx, task, plan_task_id)
        evaluation = await self._resolve_evaluation(task_row.id, evaluation_id)

        human_decision_id: int | None = None
        if assurance.requires_human:
            decision = await self._human_decision(evaluation.id)
            if decision is None:
                raise AssuranceBlocked(
                    "action requires operator approval",
                    details={
                        "assurance_id": evaluation.id,
                        "action_type": task.action.value,
                        "blocking_checks": [name.value for name in assurance.blocking],
                    },
                )
            if decision.decision is not HumanDecisionType.approved and (
                str(decision.decision) != HumanDecisionType.approved.value
            ):
                raise AssuranceBlocked(
                    "operator rejected this action; a rejected decision cannot be reused",
                    details={
                        "assurance_id": evaluation.id,
                        "action_type": task.action.value,
                        "human_decision": str(decision.decision),
                    },
                )
            human_decision_id = decision.id
        elif not assurance.executable:
            raise AssuranceBlocked(
                f"assurance decision '{assurance.decision.value}' does not authorise execution",
                details={
                    "assurance_id": evaluation.id,
                    "action_type": task.action.value,
                    "blocking_checks": [name.value for name in assurance.blocking],
                },
            )

        key = self._idempotency_key(ctx, task, task_row.id)
        replay = await self._existing_action(key)
        if replay is not None:
            log.info(
                "action_replayed",
                incident_reference=ctx.incident_reference,
                action_type=task.action.value,
                idempotency_key=key,
                outcome="idempotent_replay",
            )
            return ExecutionOutcome(
                result=ServiceResult(
                    status=ActionStatus(replay.status),
                    reason=replay.reason,
                    payload=replay.payload or {},
                    cost_inr=replay.cost_inr,
                    provenance_kind=replay.provenance_kind,
                ),
                action_id=replay.id,
                assurance_id=replay.assurance_id,
                human_decision_id=replay.human_decision_id,
                idempotency_key=key,
                replayed=True,
            )

        task_row.state = TaskState.executing
        await self._session.flush()

        result = await dispatch.dispatch(
            task.action,
            target_refs=list(task.target_refs),
            inputs=dict(task.inputs),
            evidence_refs=list(ctx.evidence_refs),
        )

        action = Action(
            plan_task_id=task_row.id,
            assurance_id=evaluation.id,
            human_decision_id=human_decision_id,
            actor=ACTOR_ORCHESTRATOR,
            idempotency_key=key,
            status=result.status,
            reason=result.reason,
            cost_inr=result.cost_inr,
            payload=result.payload,
            provenance_kind=result.provenance_kind,
            executed_at=self._now(),
        )
        self._session.add(action)
        await self._session.flush()

        task_row.state = _task_state_for(result.status)
        await self._session.flush()

        await self._journal(
            ctx,
            stage=STAGE_EXECUTE,
            actor=ACTOR_ORCHESTRATOR,
            event_type="ACTION_COMPLETED",
            summary=f"{task_row.action_type} → {result.status.value}: {result.reason}",
            detail={
                "action_id": action.id,
                "plan_task_id": task_row.id,
                "assurance_id": evaluation.id,
                "human_decision_id": human_decision_id,
                "idempotency_key": key,
                "provenance_kind": result.provenance_kind,
                **result.payload,
            },
        )
        await self._publish(
            ActionCompleted(
                producer=PRODUCER,
                correlation_id=ctx.correlation_id,
                incident_id=ctx.incident_id,
                action_id=action.id,
                plan_task_id=task_row.id,
                status=result.status,
                actor=ACTOR_ORCHESTRATOR,
                cost_inr=result.cost_inr,
                provenance={"kind": result.provenance_kind},
            ),
            ctx,
        )
        return ExecutionOutcome(
            result=result,
            action_id=action.id,
            assurance_id=evaluation.id,
            human_decision_id=human_decision_id,
            idempotency_key=key,
        )

    def _idempotency_key(self, ctx: WorkflowContext, task: PlanTask, plan_task_id: int) -> str:
        """Scope: action type + target entities + incident + intended version.

        `plan_task_id` pins both the incident and the plan version, so a re-planned
        incident produces a genuinely new key rather than colliding with the old attempt.
        """
        material = "|".join(
            [
                ctx.incident_reference,
                task.action.value,
                str(plan_task_id),
                *sorted(task.target_refs),
            ]
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        return f"{task.action.value}:{plan_task_id}:{digest}"

    async def _existing_action(self, key: str) -> Action | None:
        stmt = select(Action).where(Action.idempotency_key == key).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def _human_decision(self, evaluation_id: int) -> HumanDecision | None:
        stmt = select(HumanDecision).where(HumanDecision.assurance_id == evaluation_id).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def _resolve_task_row(
        self, ctx: WorkflowContext, task: PlanTask, plan_task_id: int | None
    ) -> PlanTaskRow:
        if plan_task_id is not None:
            row = await self._session.get(PlanTaskRow, plan_task_id)
            if row is None:
                raise EntityNotFound("plan task not found", details={"plan_task_id": plan_task_id})
            return row
        plan = await self._current_plan(ctx.incident_id)
        if plan is None:
            raise EntityNotFound(
                "incident has no plan", details={"incident_reference": ctx.incident_reference}
            )
        stmt = (
            select(PlanTaskRow)
            .where(
                PlanTaskRow.plan_id == plan.id,
                PlanTaskRow.action_type == task.action.value,
            )
            .order_by(PlanTaskRow.task_order)
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise EntityNotFound(
                "plan task not found for action",
                details={"action_type": task.action.value, "plan_id": plan.id},
            )
        return row

    async def _resolve_evaluation(
        self, plan_task_id: int, evaluation_id: int | None
    ) -> AssuranceEvaluation:
        """An action cannot exist without the evaluation that authorised it."""
        if evaluation_id is not None:
            evaluation = await self._session.get(AssuranceEvaluation, evaluation_id)
            if evaluation is None:
                raise EntityNotFound(
                    "assurance evaluation not found", details={"assurance_id": evaluation_id}
                )
            return evaluation
        stmt = (
            select(AssuranceEvaluation)
            .where(AssuranceEvaluation.plan_task_id == plan_task_id)
            .order_by(AssuranceEvaluation.id.desc())
            .limit(1)
        )
        evaluation = (await self._session.execute(stmt)).scalars().first()
        if evaluation is None:
            raise AssuranceBlocked(
                "no assurance evaluation authorises this task",
                details={"plan_task_id": plan_task_id},
            )
        return evaluation

    # -------------------------------------------------------------------------- run loop

    async def advance(self, ctx: WorkflowContext) -> WorkflowContext:
        """Drive the incident one step, honouring the state machine and limits."""
        token = correlation_id_var.set(ctx.correlation_id)
        try:
            ctx.last_note = None
            if is_terminal(ctx.state):
                ctx.last_note = f"incident is terminal in '{ctx.state.value}'"
                return ctx

            try:
                check_step_budget(
                    ctx.steps_taken, self._limits, incident_ref=ctx.incident_reference
                )
            except WorkflowLimitExceeded as exc:
                # A breached limit blocks for human review. It never loops and never
                # silently continues.
                await self._block(ctx, reason=exc.message, detail=exc.details)
                return ctx

            handler = {
                IncidentState.detected: self._step_detected,
                IncidentState.assessing: self._step_assessing,
                IncidentState.planning: self._step_planning,
                IncidentState.assuring: self._step_assuring,
                IncidentState.awaiting_approval: self._step_awaiting_approval,
                IncidentState.executing: self._step_executing,
            }[ctx.state]

            ctx.steps_taken += 1
            await handler(ctx)
            await self._session.commit()
            return ctx
        finally:
            correlation_id_var.reset(token)

    async def run(
        self, ctx: WorkflowContext, *, max_iterations: int | None = None
    ) -> WorkflowContext:
        """Advance until the incident is terminal or cannot progress without a human.

        Stopping at `awaiting_approval` is a correct outcome, not a failure: the gate asked
        for a person and there is no path around that.
        """
        ceiling = max_iterations or (self._limits.max_workflow_steps + 1)
        for _ in range(ceiling):
            before = (ctx.state, ctx.steps_taken)
            await self.advance(ctx)
            if is_terminal(ctx.state):
                return ctx
            if (ctx.state, ctx.steps_taken) == before:
                # No progress and no transition: waiting on something external.
                return ctx
        return ctx

    # --------------------------------------------------------------------- step handlers

    async def _step_detected(self, ctx: WorkflowContext) -> None:
        await self._transition(
            ctx,
            IncidentState.assessing,
            stage=STAGE_ASSESS,
            summary="Assessing impact from recorded evidence",
            detail={"evidence_refs": list(ctx.evidence_refs)},
        )

    async def _step_assessing(self, ctx: WorkflowContext) -> None:
        await self._transition(
            ctx,
            IncidentState.planning,
            stage=STAGE_PLAN,
            summary="Impact assessed; generating a recovery plan",
            detail={"llm_mode": self.modes.llm.value},
        )

    async def _step_planning(self, ctx: WorkflowContext) -> None:
        plan = await self._current_plan(ctx.incident_id)
        if plan is None:
            await self.propose_tasks(ctx)
        else:
            ctx.plan_id = plan.id
        await self._transition(
            ctx,
            IncidentState.assuring,
            stage=STAGE_ASSURE,
            summary="Plan proposed; submitting the first task to the Assurance Gate",
            detail={"plan_id": ctx.plan_id},
        )

    async def _step_assuring(self, ctx: WorkflowContext) -> None:
        task_row = await self._next_actionable_task(ctx)
        if task_row is None:
            await self._block(
                ctx,
                reason="no task can proceed: every remaining task is blocked or rejected",
                detail={"plan_id": ctx.plan_id},
            )
            return

        task = _contract_task(task_row)
        outcome = await self._assure(ctx, task)
        evaluation = await self._record_assurance(ctx, task_row, outcome)
        ctx.metadata["current_plan_task_id"] = task_row.id
        ctx.metadata["current_assurance_id"] = evaluation.id

        if not outcome.gate_available:
            # Nobody may approve past an authorisation boundary that did not run.
            task_row.state = TaskState.needs_human
            await self._session.flush()
            await self._block(
                ctx,
                reason=(
                    "Decision Assurance Gate unavailable: "
                    f"{outcome.unavailable_reason}. Execution cannot be authorised."
                ),
                detail={
                    "plan_task_id": task_row.id,
                    "assurance_id": evaluation.id,
                    "action_type": task_row.action_type,
                    "gate_available": False,
                },
            )
            return

        if outcome.result.executable:
            task_row.state = TaskState.assured
            await self._session.flush()
            await self._transition(
                ctx,
                IncidentState.executing,
                stage=STAGE_EXECUTE,
                summary=f"{task_row.action_type} authorised ({outcome.result.decision.value})",
                detail={"plan_task_id": task_row.id, "assurance_id": evaluation.id},
            )
            return

        task_row.state = TaskState.needs_human
        await self._session.flush()
        await self._transition(
            ctx,
            IncidentState.awaiting_approval,
            stage=STAGE_ASSURE,
            summary=f"{task_row.action_type} held for operator approval",
            detail={
                "plan_task_id": task_row.id,
                "assurance_id": evaluation.id,
                "blocking": [name.value for name in outcome.result.blocking],
            },
        )

    async def _step_awaiting_approval(self, ctx: WorkflowContext) -> None:
        evaluation_id = ctx.metadata.get("current_assurance_id")
        plan_task_id = ctx.metadata.get("current_plan_task_id")
        if evaluation_id is None or plan_task_id is None:
            resolved = await self._pending_approval(ctx)
            if resolved is None:
                await self._block(
                    ctx,
                    reason="awaiting approval but no evaluation is pending a decision",
                    detail={"plan_id": ctx.plan_id},
                )
                return
            plan_task_id, evaluation_id = resolved
            ctx.metadata["current_plan_task_id"] = plan_task_id
            ctx.metadata["current_assurance_id"] = evaluation_id

        decision = await self._human_decision(int(evaluation_id))
        if decision is None:
            # Not an error. Waiting for a person is a legitimate resting state.
            ctx.steps_taken -= 1
            ctx.last_note = f"waiting for an operator decision on evaluation {evaluation_id}"
            return

        task_row = await self._session.get(PlanTaskRow, int(plan_task_id))
        if str(decision.decision) == HumanDecisionType.approved.value:
            if task_row is not None:
                task_row.state = TaskState.assured
                await self._session.flush()
            await self._transition(
                ctx,
                IncidentState.executing,
                stage=STAGE_EXECUTE,
                summary=f"Operator approved evaluation {evaluation_id}",
                detail={
                    "assurance_id": evaluation_id,
                    "plan_task_id": plan_task_id,
                    "actor_id": decision.actor_id,
                },
            )
            return

        if task_row is not None:
            task_row.state = TaskState.rejected
            await self._session.flush()
        await self._journal(
            ctx,
            stage=STAGE_ASSURE,
            actor="human",
            event_type="HUMAN_DECISION_RECORDED",
            summary=f"Operator rejected evaluation {evaluation_id}",
            detail={
                "assurance_id": evaluation_id,
                "plan_task_id": plan_task_id,
                "actor_id": decision.actor_id,
                "reason": decision.reason,
            },
        )
        ctx.metadata.pop("current_assurance_id", None)
        ctx.metadata.pop("current_plan_task_id", None)
        if await self._next_actionable_task(ctx) is None:
            await self._block(
                ctx,
                reason="operator rejected the remaining work",
                detail={"assurance_id": evaluation_id},
            )
            return
        await self._transition(
            ctx,
            IncidentState.assuring,
            stage=STAGE_ASSURE,
            summary="Continuing with the next task after a rejection",
            detail={"plan_id": ctx.plan_id},
        )

    async def _step_executing(self, ctx: WorkflowContext) -> None:
        plan_task_id = ctx.metadata.get("current_plan_task_id")
        if plan_task_id is None:
            await self._block(
                ctx,
                reason="executing without an authorised task",
                detail={"plan_id": ctx.plan_id},
            )
            return

        task_row = await self._session.get(PlanTaskRow, int(plan_task_id))
        if task_row is None:
            await self._block(
                ctx, reason="authorised task disappeared", detail={"plan_task_id": plan_task_id}
            )
            return

        evaluation = await self._resolve_evaluation(
            task_row.id, ctx.metadata.get("current_assurance_id")
        )
        assurance = _result_from_row(evaluation)
        outcome = await self.execute(
            ctx,
            _contract_task(task_row),
            assurance,
            evaluation_id=evaluation.id,
            plan_task_id=task_row.id,
        )
        ctx.metadata.pop("current_plan_task_id", None)
        ctx.metadata.pop("current_assurance_id", None)

        if outcome.result.status is not ActionStatus.success:
            # An explicit refusal or failure stops the plan. Nothing is invented to keep
            # the run looking healthy.
            await self._block(
                ctx,
                reason=outcome.result.reason,
                detail={
                    "action_id": outcome.action_id,
                    "plan_task_id": task_row.id,
                    "status": outcome.result.status.value,
                    **outcome.result.payload,
                },
            )
            return

        if await self._next_actionable_task(ctx) is not None:
            await self._transition(
                ctx,
                IncidentState.assuring,
                stage=STAGE_ASSURE,
                summary="Task complete; submitting the next task to the Assurance Gate",
                detail={"plan_id": ctx.plan_id},
            )
            return

        await self._resolve(ctx)

    # ------------------------------------------------------------------------- plan reads

    async def _current_plan(self, incident_id: int) -> Plan | None:
        stmt = select(Plan).where(Plan.incident_id == incident_id).order_by(Plan.id.desc()).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def _plan_tasks(self, ctx: WorkflowContext) -> list[PlanTaskRow]:
        plan_id = ctx.plan_id
        if plan_id is None:
            plan = await self._current_plan(ctx.incident_id)
            if plan is None:
                return []
            plan_id = ctx.plan_id = plan.id
        stmt = (
            select(PlanTaskRow)
            .where(PlanTaskRow.plan_id == plan_id)
            .order_by(PlanTaskRow.task_order)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def _next_actionable_task(self, ctx: WorkflowContext) -> PlanTaskRow | None:
        """First task in order whose dependencies have all completed successfully."""
        rows = await self._plan_tasks(ctx)
        by_id = {str(row.id): row for row in rows}
        for row in rows:
            if TaskState(row.state) in _TASK_SETTLED or TaskState(row.state) in {
                TaskState.needs_human,
                TaskState.executing,
            }:
                continue
            dependencies = [by_id.get(str(dep)) for dep in (row.depends_on or [])]
            if any(
                dependency is None or TaskState(dependency.state) not in _TASK_DONE_WELL
                for dependency in dependencies
            ):
                continue
            return row
        return None

    async def _pending_approval(self, ctx: WorkflowContext) -> tuple[int, int] | None:
        for row in await self._plan_tasks(ctx):
            if TaskState(row.state) is not TaskState.needs_human:
                continue
            stmt = (
                select(AssuranceEvaluation)
                .where(AssuranceEvaluation.plan_task_id == row.id)
                .order_by(AssuranceEvaluation.id.desc())
                .limit(1)
            )
            evaluation = (await self._session.execute(stmt)).scalars().first()
            if evaluation is not None:
                return row.id, evaluation.id
        return None

    # ------------------------------------------------------------------------ transitions

    async def _transition(
        self,
        ctx: WorkflowContext,
        target: IncidentState,
        *,
        stage: str,
        summary: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Move state, but only through a legal transition, and always with a record."""
        assert_transition(ctx.state, target, incident_ref=ctx.incident_reference)
        incident = await self._session.get(Incident, ctx.incident_id)
        if incident is None:
            raise EntityNotFound("incident not found", details={"incident_id": ctx.incident_id})
        previous = ctx.state
        incident.state = target
        if target in IncidentState.terminal():
            incident.closed_at = self._now()
        ctx.state = target
        await self._session.flush()
        await self._journal(
            ctx,
            stage=stage,
            actor=ACTOR_ORCHESTRATOR,
            event_type="STATE_CHANGED",
            summary=summary,
            detail={"from": previous.value, "to": target.value, **(detail or {})},
        )
        log.info(
            "incident_state_changed",
            incident_reference=ctx.incident_reference,
            **{"from": previous.value, "to": target.value},
            outcome="success",
        )

    async def _block(
        self, ctx: WorkflowContext, *, reason: str, detail: dict[str, Any] | None = None
    ) -> None:
        await self._transition(
            ctx,
            IncidentState.blocked,
            stage=STAGE_RESOLVE,
            summary=f"Blocked: {reason}",
            detail={"reason": reason, **(detail or {})},
        )
        ctx.last_note = reason
        await self._publish(
            RecoveryBlocked(
                producer=PRODUCER,
                correlation_id=ctx.correlation_id,
                incident_id=ctx.incident_id,
                incident_reference=ctx.incident_reference,
                blocking_reasons=[reason],
            ),
            ctx,
        )

    async def _resolve(self, ctx: WorkflowContext) -> None:
        rows = await self._plan_tasks(ctx)
        metrics = {
            "tasks_total": len(rows),
            "tasks_succeeded": sum(1 for r in rows if TaskState(r.state) is TaskState.succeeded),
            "tasks_skipped": sum(1 for r in rows if TaskState(r.state) is TaskState.skipped),
            "steps_taken": ctx.steps_taken,
        }
        await self._transition(
            ctx,
            IncidentState.resolved,
            stage=STAGE_RESOLVE,
            summary="Every task in the plan completed",
            detail=metrics,
        )
        await self._publish(
            IncidentResolved(
                producer=PRODUCER,
                correlation_id=ctx.correlation_id,
                incident_id=ctx.incident_id,
                incident_reference=ctx.incident_reference,
                # Derived from recorded rows only. No invented metric.
                outcome_metrics=metrics,
            ),
            ctx,
        )

    # -------------------------------------------------------------------------- recording

    async def _journal(
        self,
        ctx: WorkflowContext,
        *,
        stage: str,
        actor: str,
        event_type: str,
        summary: str,
        detail: dict[str, Any] | None = None,
    ) -> DecisionLog:
        """Append to decision_log. Same transaction as the change it describes.

        Append-only by construction: the engine never updates or deletes a row here.
        """
        entry = DecisionLog(
            incident_id=ctx.incident_id,
            occurred_at=self._now(),
            stage=stage,
            actor=actor,
            event_type=event_type,
            summary=summary,
            detail=detail or {},
            correlation_id=ctx.correlation_id,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def _publish(self, event: Any, ctx: WorkflowContext) -> None:
        """Fan the event out, without letting the bus decide whether work happened.

        `decision_log` is the authoritative record and is written in the same transaction
        as the state change. The bus is a notification channel on top of it, so a transport
        outage is recorded as a degradation rather than failing an otherwise correct and
        fully audited recovery. It is never swallowed silently.
        """
        if self._bus is None:
            return
        try:
            await self._bus.publish(event)
        except Exception as exc:
            log.error(
                "event_publication_failed",
                outcome="error",
                incident_reference=ctx.incident_reference,
                event_type=str(getattr(event, "event_type", "unknown")),
                error_code=getattr(exc, "code", type(exc).__name__),
                detail=str(exc),
            )
            await self._journal(
                ctx,
                stage=STAGE_DETECT,
                actor=ACTOR_ORCHESTRATOR,
                event_type="EVENT_PUBLICATION_FAILED",
                summary=(
                    f"{getattr(event, 'event_type', 'event')} could not be published to the "
                    "event bus; the decision log remains authoritative"
                ),
                detail={"error": type(exc).__name__},
            )


# ---------------------------------------------------------------------------- helpers


def _new_correlation_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _coerce_trigger(trigger_type: str) -> TriggerType:
    try:
        return TriggerType(trigger_type)
    except ValueError as exc:
        raise EntityNotFound(
            f"unknown trigger type '{trigger_type}'",
            details={"allowed": sorted(t.value for t in TriggerType)},
        ) from exc


def _contract_task(row: PlanTaskRow) -> PlanTask:
    """Rebuild the typed task from its persisted row.

    `ActionType(row.action_type)` is the closed-enum check: a value that is not a known
    action cannot be turned back into a task, so it can never reach assurance or a service.
    """
    return PlanTask(
        action=ActionType(row.action_type),
        target_refs=list(row.target_refs or []),
        inputs=dict(row.inputs or {}),
        depends_on=[str(dep) for dep in (row.depends_on or [])],
    )


def _task_state_for(status: ActionStatus) -> TaskState:
    return {
        ActionStatus.success: TaskState.succeeded,
        ActionStatus.failure: TaskState.failed,
        ActionStatus.skipped: TaskState.skipped,
        ActionStatus.needs_human: TaskState.needs_human,
    }[status]


def _result_from_row(row: AssuranceEvaluation) -> AssuranceResult:
    """Reconstruct the decision that authorised a task, from its immutable row.

    Replay must use the semantics recorded at decision time, which is why the evaluation
    stores its own config version and hash rather than reading today's config.
    """
    from app.assurance.contract import CheckName, CheckResult, ReasonCode
    from app.models.enums import CheckState, RiskTier

    checks: list[CheckResult] = []
    for name, payload in (row.check_results or {}).items():
        try:
            check_name = CheckName(name)
        except ValueError:
            continue
        data = payload if isinstance(payload, dict) else {}
        checks.append(
            CheckResult(
                name=check_name,
                state=CheckState(data.get("state", CheckState.failed.value)),
                reason_code=ReasonCode(data.get("reason_code", ReasonCode.OK.value)),
                reason=data.get("reason"),
                tier=RiskTier(data["tier"]) if data.get("tier") else None,
            )
        )
    blocking = []
    for name in row.blocking_reasons or []:
        try:
            blocking.append(CheckName(name))
        except ValueError:
            continue
    return AssuranceResult(
        decision=AssuranceDecision(row.decision),
        risk_tier=RiskTier(row.risk_tier),
        checks=checks,
        blocking=blocking,
        evidence_refs=list(row.evidence_refs or []),
        config_version=row.config_version,
        config_hash=row.config_hash,
        evaluated_at=row.evaluated_at,
    )
