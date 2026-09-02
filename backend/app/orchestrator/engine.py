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

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contract import PlanTask
from app.assurance.blocking import is_approvable, unapprovable_reasons
from app.assurance.contract import AssuranceResult
from app.config import LLMMode, ResolvedModes, Settings, get_modes, get_settings
from app.db.scenario_queries import load_delay_risk_inputs
from app.errors import AssuranceBlocked, EntityNotFound, WorkflowLimitExceeded
from app.events.types import (
    ActionCompleted,
    AssuranceEvaluated,
    HighRiskDelay,
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
from app.models.reference import Flight, WeatherObservation
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    DecisionLog,
    HumanDecision,
    Incident,
    IncidentGroup,
    Plan,
    Prediction,
)
from app.models.workflow import PlanTask as PlanTaskRow
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator import assurance_adapter, dispatch
from app.orchestrator.limits import Limits, check_step_budget
from app.orchestrator.playbook import (
    FALLBACK_GENERATOR,
    FALLBACK_RATIONALE,
    PlaybookStep,
    playbook_for,
)
from app.orchestrator.state import assert_transition, is_terminal
from app.orchestrator.weather_adapter import (
    DETAIL_KEY,
    EVENT_LIVE_NOT_SCORED,
    EVENT_LIVE_UNAVAILABLE,
    WeatherIngestOutcome,
    ingest_live_weather,
)
from app.services.base import ServiceResult
from app.services.delay_risk import DelayRiskService

log = get_logger(__name__)

ACTOR_ORCHESTRATOR = "orchestrator"
ACTOR_GATE = "assurance_gate"
#: A person. Recorded when an operator decides, never for the orchestrator acting on it.
ACTOR_HUMAN = "human"
PRODUCER = "orchestrator"

# Timeline stages. Matches the vocabulary the committed timeline fixture already uses.
STAGE_DETECT = "detect"
STAGE_ASSESS = "assess"
STAGE_PLAN = "plan"
STAGE_ASSURE = "assure"
STAGE_EXECUTE = "execute"
STAGE_RESOLVE = "resolve"

#: Smallest shared-pool slice worth starting a live planner call with. Below this the call would be
#: abandoned before a provider could realistically answer, so it is skipped and recorded instead —
#: spending the cascade's remaining time to manufacture a timeout helps nobody.
MIN_PLANNER_SLICE_SECONDS = 5.0

#: Grace added to the allowance when arming the orchestrator's hard backstop.
#:
#: The allowance is enforced INSIDE the client, which can size attempts and refuse a retry that
#: will not fit. `asyncio.wait_for` stays as a backstop against a client that ignores its budget,
#: but it is armed slightly later so that in every ordinary case the client's own honest diagnosis —
#: the provider's status, phase and finish reason — is what reaches the decision log, instead of an
#: opaque cancellation that only names the orchestrator's timer.
PLANNER_BACKSTOP_GRACE_SECONDS = 5.0

#: The grace is also capped at this fraction of the allowance, so a deliberately tight allowance
#: still produces a tight hard bound rather than one dominated by a fixed margin.
_PLANNER_BACKSTOP_GRACE_FRACTION = 0.2


def planner_backstop_seconds(budget_seconds: float) -> float:
    """The hard `wait_for` bound for a planner allowance. Always above the allowance itself."""
    grace = min(PLANNER_BACKSTOP_GRACE_SECONDS, budget_seconds * _PLANNER_BACKSTOP_GRACE_FRACTION)
    return budget_seconds + grace


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
    #: The GateInputs the orchestrator gathered, so coverage can be recorded.
    gathered: dict[str, Any] = field(default_factory=dict)
    #: Stream B's GateRequirements, recorded as the provenance of the two policy checks.
    requirements: Any = None
    #: The GateInputs the orchestrator gathered, so coverage can be recorded.
    gathered: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannerAllowance:
    """How much wall-clock this incident's optional planner candidate may spend, and from where.

    Three sources, and the difference between them is the whole point:

    * the declared demo primary holds a **reserved** allowance that no other member can consume;
    * every other live member draws a slice from a **shared pool** charged by actual elapsed time,
      so a healthy warm call costs the few seconds it took rather than a 20-second reservation;
    * fixture and off mode are unpooled and unchanged.
    """

    seconds: float
    #: The declared primary of the configured demo dataset, on its reserved allowance.
    primary_demo: bool
    #: Drawn from the shared non-primary pool, and therefore charged back to it.
    pooled: bool
    #: Pool left before this allowance was taken. `None` when the pool does not apply.
    pool_remaining: float | None
    #: False when the pool cannot fund a viable call. The candidate is skipped, never faked.
    attempt: bool


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
        # Transient diagnostic only. Set after the candidate and PLAN_PROPOSED journal are staged,
        # then consumed by `advance()` after the transaction commit makes both durable.
        self._pending_planner_plan_id: int | None = None
        # Shared non-primary live planner pool, in seconds, lazily filled on first use.
        #
        # Scoped to this Orchestrator instance, which is exactly the right scope:
        # `GroupOrchestrator` builds one and reuses it for every member of a run, and each HTTP
        # request builds its own — so the pool bounds precisely the thing the 300-second request
        # budget applies to, and a single-incident `POST /incidents/{ref}/run` is unaffected by
        # what another request spent.
        self._planner_pool_seconds: float | None = None

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
        opened_at: datetime | None = None,
    ) -> WorkflowContext:
        """Create an incident, or return the existing active one for this flight.

        Deduplication has two layers, and both are needed. The query catches the ordinary
        case. The partial unique index `uq_incident_active_per_flight` catches the race —
        two pollers arriving inside the same millisecond — by turning the duplicate into a
        database error, which is recovered here rather than surfaced. A 60-second weather
        poll must not open 60 incidents an hour.

        `opened_at` is when the disruption occurred, which for an injected scenario is the
        fixture's anchor rather than the moment somebody ran the command. It becomes the
        incident's reference clock: evidence is selected as of that time and freshness is
        judged against it. Audit timestamps are never backdated with it — every
        `decision_log` entry records the real time its step ran, because a falsified
        `occurred_at` would corrupt the one record this system asks to be trusted.
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
            opened_at = _as_utc(opened_at) or self._now()
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
                    f"{flight.flight_number} ({flight.origin_icao}->{flight.destination_icao})"
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
        # Steps already taken are recovered from the durable record, not restarted at zero.
        # Otherwise the step budget resets on every POST /run and stops being a budget: a
        # caller could drive an incident indefinitely, one HTTP call at a time.
        ctx.steps_taken = await self._recorded_steps(incident_id)
        return ctx

    async def _recorded_steps(self, incident_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(DecisionLog)
            .where(
                DecisionLog.incident_id == incident_id,
                DecisionLog.event_type == "STATE_CHANGED",
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    # ------------------------------------------------------------------------- planning

    async def propose_tasks(self, ctx: WorkflowContext) -> list[PlanTask]:
        """Get a plan from the deterministic playbook, persisting it as a Plan row.

        The fallback comes FIRST and unconditionally in this slice. The Planner agent
        arrives later and will be an improvement on this, not a replacement for it: with
        `LLM_MODE=off` the playbook alone must still produce a usable plan.

        Every task's action is an `ActionType`, validated by the `PlanTask` contract before
        anything is persisted, so an unknown action type cannot reach assurance.
        """
        steps, deferred = self._executable_steps(ctx)
        tasks = [
            PlanTask(
                action=step.action,
                target_refs=self._target_refs(ctx),
                inputs=dict(step.inputs),
                depends_on=[
                    dependency.value
                    for dependency in step.depends_on
                    # A dependency that was deferred cannot gate anything, so the edge is
                    # dropped rather than left dangling and permanently unsatisfiable.
                    if dependency not in deferred
                ],
            )
            for step in steps
        ]

        rationale = FALLBACK_RATIONALE
        if deferred:
            # Stated on the plan itself, so the omission is part of the record a reviewer
            # reads rather than something they have to notice is missing.
            rationale = (
                f"{FALLBACK_RATIONALE} Not proposed, because no deterministic service is "
                f"available to carry them out yet: "
                f"{', '.join(sorted(action.value for action in deferred))}."
            )

        plan = Plan(
            incident_id=ctx.incident_id,
            generated_at=self._now(),
            generator=FALLBACK_GENERATOR,
            # No model was involved, so there is no prompt version and no self-report.
            prompt_version=None,
            model_self_report=None,
            rationale=rationale,
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
                "deferred_actions": sorted(action.value for action in deferred),
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

        # ----- Phase 3: Planner agent produces a second candidate alongside the playbook -----
        await self._propose_planner_candidate(ctx)

        return tasks

    def _executable_steps(
        self, ctx: WorkflowContext
    ) -> tuple[tuple[PlaybookStep, ...], set[ActionType]]:
        """Narrow the playbook to steps a deterministic service can actually carry out.

        Proposing a task nothing can execute has two bad outcomes and no good one: the run
        stops dead at the first unavailable capability, and the plan implies work the system
        cannot do. So an action with no registered service is *deferred* — left out of the
        plan and named in the rationale and the decision log, rather than proposed and failed.

        This is not the same as pretending the work was done. Nothing is marked succeeded,
        no count is invented, and the omission is written into the record.

        The filter reads the dispatch registry, so a plan widens by itself as Stream C lands
        each service. There is no second list to keep in step.

        If nothing at all is registered, the full playbook is kept: an empty plan would let
        an incident resolve without a single task, which is a far worse failure than a
        visible refusal.
        """
        steps = playbook_for(ctx.trigger_type or TriggerType.other)
        available = tuple(step for step in steps if dispatch.is_implemented(step.action))
        if not available:
            return steps, set()
        deferred = {step.action for step in steps if step not in available}
        return available, deferred

    def _target_refs(self, ctx: WorkflowContext) -> list[str]:
        refs = [f"incident:{ctx.incident_reference}"]
        if ctx.flight_id is not None:
            refs.append(f"flight:{ctx.flight_id}")
        return refs

    async def _is_declared_demo_primary(self, incident: Incident | None) -> bool:
        """True for the declared primary member of the configured demo dataset.

        Primary is recorded data, not a reference convention: the inbound VAAH member also ends in
        ``-01``. It therefore requires both the configured demo dataset id and the unique
        ``incident_group_flight.role = 'primary'`` row. A direct incident run and a group run make
        the same decision because both read the same persisted membership. The dataset check keeps
        a production primary from silently receiving demo tuning.
        """
        if (
            incident is None
            or incident.group_id is None
            or incident.flight_id is None
            or incident.demo_dataset_id != self._settings.demo_dataset_id
        ):
            return False

        # Imported lazily with the other Phase 2/3 paths. This is a read of existing declared
        # membership, not a second role model and not a schema change.
        from app.models.cascade import IncidentGroupFlight

        role = await self._session.scalar(
            select(IncidentGroupFlight.role).where(
                IncidentGroupFlight.incident_group_id == incident.group_id,
                IncidentGroupFlight.flight_id == incident.flight_id,
            )
        )
        return role == "primary"

    async def _planner_candidate_allowance(self, incident: Incident | None) -> PlannerAllowance:
        """Decide this incident's planner allowance: reserved for the primary, pooled for the rest.

        The failure this replaces was an allocation failure, not a number that was merely too
        small. Members advance sequentially and the planner runs only while an incident is
        ``planning``, so the primary's single opportunity is always the coldest call of the run —
        and it was being handed a ceiling *below* the transport's own 60-second per-attempt ceiling.
        It therefore got less than one complete provider attempt and no usable retry, while the
        warm members behind it succeeded on their smaller budgets. Raising every member's ceiling
        instead would have multiplied the cost by eight.

        So the primary's allowance is reserved and large enough for a complete attempt, and the
        non-primary members share a pool charged by the time they actually use. A healthy warm call
        costs single-digit seconds, so in practice all seven still get candidates; a pathological
        run exhausts the pool and the remaining members skip their model call with a recorded
        reason, leaving the deterministic playbook — already persisted and selected — in charge.
        """
        ordinary = self._settings.planner_candidate_budget_seconds

        if await self._is_declared_demo_primary(incident):
            return PlannerAllowance(
                seconds=self._settings.primary_demo_planner_candidate_budget_seconds,
                primary_demo=True,
                pooled=False,
                pool_remaining=None,
                attempt=True,
            )

        # Fixture replay is deterministic and costs no wall-clock, and off mode never reaches here.
        # Pooling either would add nondeterminism to paths whose whole value is being repeatable.
        if self.modes.llm is not LLMMode.live:
            return PlannerAllowance(
                seconds=ordinary,
                primary_demo=False,
                pooled=False,
                pool_remaining=None,
                attempt=True,
            )

        if self._planner_pool_seconds is None:
            self._planner_pool_seconds = self._settings.planner_group_pool_seconds
        remaining = self._planner_pool_seconds

        if remaining < MIN_PLANNER_SLICE_SECONDS:
            return PlannerAllowance(
                seconds=0.0,
                primary_demo=False,
                pooled=True,
                pool_remaining=remaining,
                attempt=False,
            )

        return PlannerAllowance(
            seconds=min(ordinary, remaining),
            primary_demo=False,
            pooled=True,
            pool_remaining=remaining,
            attempt=True,
        )

    def _charge_planner_pool(self, allowance: PlannerAllowance, elapsed: float) -> None:
        """Debit the shared pool by the time actually spent, never by the nominal allowance.

        Charging the allowance would make seven fast calls cost 140 seconds of budget they never
        used, which is the accounting error that left nothing spare for the member that mattered.
        """
        if not allowance.pooled or self._planner_pool_seconds is None:
            return
        self._planner_pool_seconds = max(0.0, self._planner_pool_seconds - max(0.0, elapsed))

    async def _propose_planner_candidate(self, ctx: WorkflowContext) -> None:
        """Phase 3: produce a second candidate plan from the Planner reasoning agent.

        The playbook plan is already persisted and selected. This is an ADDITIONAL candidate
        with `generator='planner-agent'`. If it fails for any reason — mode=off, network error,
        malformed output, validation failure — the playbook plan is unaffected and the incident
        continues. A model failure must never block recovery.

        Budget-minimal for Stream A: all reasoning logic lives in Stream C's agent/client layer.
        This method calls it, persists the result, journals it, and moves on.
        """
        # Imported inside the function, like the planner below: the frozen guard
        # `test_no_llm_in_services` forbids reasoning-layer imports at orchestrator module scope,
        # so that a deterministic run cannot even load the agent path.
        from app.agents.planner import GENERATOR as PLANNER_GENERATOR
        from app.agents.planner import PROMPT_VERSION, PlannerAgent
        from app.agents.reflection import reflect
        from app.config import LLMMode
        from app.llm.client import LLMUnavailable
        from app.memory.retrieval import find_precedents

        if self.modes.llm is LLMMode.off:
            return

        phase = "context"
        # Bound before the try, so the diagnostics in the handlers below are always reportable even
        # when the failure happens while gathering context — earlier than any allowance is decided.
        allowance: PlannerAllowance | None = None
        budget: float | None = None
        primary_demo_budget = False
        log.info(
            "planner_candidate_started",
            incident_reference=ctx.incident_reference,
            llm_mode=self.modes.llm.value,
        )
        try:
            # Get airport from the group (which carries airport_icao) or from the flight
            incident = await self._session.get(Incident, ctx.incident_id)
            if incident and incident.group_id:
                group = await self._session.get(IncidentGroup, incident.group_id)
                airport_icao = group.airport_icao if group else "VOBL"
            elif ctx.flight_id:
                flight_for_airport = await self._session.get(Flight, ctx.flight_id)
                airport_icao = flight_for_airport.origin_icao if flight_for_airport else "VOBL"
            else:
                airport_icao = "VOBL"
            trigger_type = ctx.trigger_type or "weather"
            severity = incident.severity if incident else "high"

            precedents = await find_precedents(
                self._session,
                airport_icao=airport_icao,
                trigger_type=trigger_type,
                severity=severity,
                exclude_incident_id=ctx.incident_id,
            )

            # Get flight info for prompt context
            flight = await self._session.get(Flight, ctx.flight_id) if ctx.flight_id else None

            phase = "allocation"
            allowance = await self._planner_candidate_allowance(incident)
            budget = allowance.seconds
            primary_demo_budget = allowance.primary_demo
            log.info(
                "planner_candidate_budget_selected",
                incident_reference=ctx.incident_reference,
                budget_seconds=budget,
                primary_demo_budget=primary_demo_budget,
                pooled=allowance.pooled,
                pool_remaining_seconds=allowance.pool_remaining,
                attempted=allowance.attempt,
            )
            if not allowance.attempt:
                # The shared pool is spent. Said plainly rather than dressed up as a provider
                # fault: nothing was asked of the model, so nothing about the model is reported.
                raise LLMUnavailable(
                    "The shared planner allowance for this run was already spent by earlier "
                    "incidents, so no model call was attempted for this one.",
                    phase="orchestrator_pool_exhausted",
                )

            agent = PlannerAgent()
            phase = "request"
            # The allowance is enforced INSIDE the client, and `wait_for` is only a backstop.
            #
            # It used to be the other way round, and that inverted the two ceilings: the
            # orchestrator's allowance was smaller than the client's own 60-second per-attempt
            # timeout, so a healthy-but-slow call was cancelled mid-flight before it could finish
            # or be retried. Cancelling from outside can only ever destroy an in-flight attempt —
            # it cannot make the provider faster, and it reports our timer instead of the
            # provider's behaviour. Handing the budget down lets the client size each attempt to
            # the time left and refuse a retry that cannot fit, so the outcome is always a real
            # one: an answer, a named provider failure, or an explicit exhausted budget.
            #
            # `wait_for` stays, armed a few seconds later, so a client that ignored its budget
            # still cannot hold the cascade open. Only the `await` is wrapped: every DB write in
            # this method happens after it, so a cancelled call cannot leave the session
            # mid-flush. Either bound converts into the one existing skip route rather than a new
            # one — a model that never answers is unavailable, which is what that path means.
            started = asyncio.get_running_loop().time()
            try:
                planner_response, audit = await asyncio.wait_for(
                    agent.propose(
                        incident_reference=ctx.incident_reference,
                        flight_id=ctx.flight_id,
                        flight_number=flight.flight_number if flight else None,
                        route=(
                            f"{flight.origin_icao}->{flight.destination_icao}" if flight else None
                        ),
                        delay_minutes=None,  # not stored on the Flight model
                        trigger_type=trigger_type,
                        severity=severity,
                        airport_icao=airport_icao,
                        precedents=[p.to_dict() for p in precedents],
                        budget_seconds=budget,
                    ),
                    timeout=planner_backstop_seconds(budget),
                )
            except TimeoutError as exc:
                raise LLMUnavailable(
                    f"The planner agent did not answer within its {budget:g}s budget.",
                    phase="orchestrator_budget",
                ) from exc
            finally:
                # Charged on every route out — success, provider failure or backstop — because the
                # cascade spent that time either way and the members behind this one must see it.
                self._charge_planner_pool(allowance, asyncio.get_running_loop().time() - started)

            # Reflect before persisting. The agent proposes; this narrows to what can actually be
            # executed and records every drop with its reason.
            #
            # It is not a second gate and authorises nothing — it can only remove. The Decision
            # Assurance Gate still evaluates every surviving task, and a person still approves
            # anything high risk. What it prevents is a candidate that cannot be run being offered
            # as one: an action with no registered service, a duplicate, a dependency on a task
            # that was dropped, or a target reference the orchestrator never supplied.
            phase = "reflection"
            reflection = reflect(
                list(planner_response.tasks),
                available_actions={
                    action for action in ActionType if dispatch.is_implemented(action)
                },
                allowed_target_refs=self._target_refs(ctx),
            )
            if reflection.rejected:
                # Nothing survived. Offering an empty candidate would be worse than offering none,
                # so it is recorded and skipped; the playbook plan is untouched.
                log.info(
                    "planner_candidate_rejected_by_reflection",
                    incident_reference=ctx.incident_reference,
                    dropped=reflection.dropped_actions,
                    reason=reflection.rejection_reason,
                )
                await self._journal(
                    ctx,
                    stage=STAGE_PLAN,
                    actor=ACTOR_ORCHESTRATOR,
                    event_type="PLANNER_CANDIDATE_REJECTED",
                    summary=(
                        "The planner's proposal contained nothing executable, so no candidate was "
                        "recorded. The deterministic playbook plan is unaffected."
                    ),
                    detail={
                        "generator": PLANNER_GENERATOR,
                        "prompt_version": PROMPT_VERSION,
                        "llm_mode": self.modes.llm.value,
                        **reflection.as_detail(),
                    },
                )
                return

            # Persist as a second Plan row
            phase = "plan_insert"
            planner_plan = Plan(
                incident_id=ctx.incident_id,
                generated_at=self._now(),
                generator=PLANNER_GENERATOR,
                prompt_version=PROMPT_VERSION,
                model_self_report=audit.model_self_report,
                rationale=planner_response.reason,
                raw_response=planner_response.model_dump(mode="json"),
                retrieved_incident_ids=[p.incident_id for p in precedents],
                variant_key="planner",
                selection_state="candidate",
            )
            self._session.add(planner_plan)
            await self._session.flush()

            # Persist tasks
            phase = "task_insert"
            planner_rows: dict[str, PlanTaskRow] = {}
            for order, task in enumerate(reflection.tasks, start=1):
                row = PlanTaskRow(
                    plan_id=planner_plan.id,
                    action_type=task.action.value,
                    task_order=order,
                    depends_on=[],
                    target_refs=list(task.target_refs),
                    inputs=dict(task.inputs),
                    state=TaskState.proposed,
                )
                self._session.add(row)
                planner_rows[task.action.value] = row
            await self._session.flush()

            # Resolve dependencies
            phase = "dependency_resolution"
            for task in reflection.tasks:
                row = planner_rows[task.action.value]
                row.depends_on = [
                    str(planner_rows[name].id) for name in task.depends_on if name in planner_rows
                ]
            await self._session.flush()

            phase = "journal"
            await self._journal(
                ctx,
                stage=STAGE_PLAN,
                actor=ACTOR_ORCHESTRATOR,
                event_type="PLAN_PROPOSED",
                summary=(f"{len(reflection.tasks)} tasks proposed by the planner agent"),
                detail={
                    "plan_id": planner_plan.id,
                    "generator": PLANNER_GENERATOR,
                    "prompt_version": PROMPT_VERSION,
                    "model_self_report": audit.model_self_report,
                    "llm_mode": self.modes.llm.value,
                    # WHICH endpoint actually answered.
                    #
                    # `plan.generator` records the AGENT (`planner-agent`), which is the right
                    # thing for the gate to branch on — authorship, not vendor. But it meant the
                    # only durable trace of the transport was nothing at all: `ProviderTransport`
                    # composes `openrouter:openai/gpt-oss-120b`, `ModelCallAudit` carries it, and
                    # persistence dropped it. A reviewer asking "which model wrote this candidate?"
                    # had no answer, and the console had no honest field to render.
                    #
                    # Recorded here rather than as a new column: `decision_log.detail` already
                    # carries this call's latency, tokens and self-report, so the transport belongs
                    # beside them and no migration is needed for a diagnostic fact. A fixture
                    # replay records `fixture:planner`, which is equally the truth about what
                    # answered.
                    "transport_generator": audit.generator,
                    "actions": [t.action.value for t in reflection.tasks],
                    "precedents_used": len(precedents),
                    # What the model proposed, what was removed, and why. In the record a
                    # reviewer reads, not only in a log line.
                    "reflection": reflection.as_detail(),
                    "latency_ms": audit.latency_ms,
                    "input_tokens": audit.input_tokens,
                    "output_tokens": audit.output_tokens,
                },
            )
            self._pending_planner_plan_id = planner_plan.id
            log.info(
                "planner_candidate_staged",
                incident_reference=ctx.incident_reference,
                plan_id=planner_plan.id,
                phase=phase,
                tasks=len(reflection.tasks),
                dropped=reflection.dropped_actions,
                precedents=len(precedents),
            )

        except LLMUnavailable as exc:
            # Expected in fixture-missing or rate-limited scenarios. The playbook plan
            # is already persisted; this failure is informational, never fatal.
            log.info(
                "planner_candidate_skipped",
                incident_reference=ctx.incident_reference,
                phase=phase,
                llm_phase=getattr(exc, "phase", "unknown"),
                status_code=getattr(exc, "status_code", None),
                finish_reason=getattr(exc, "finish_reason", None),
                content_length=getattr(exc, "content_length", None),
                budget_seconds=budget,
                primary_demo_budget=primary_demo_budget,
                pooled=allowance.pooled if allowance else None,
                pool_remaining_seconds=(self._planner_pool_seconds if allowance else None),
                reason=str(exc)[:200],
            )
            await self._journal(
                ctx,
                stage=STAGE_PLAN,
                actor=ACTOR_ORCHESTRATOR,
                event_type="PLANNER_AGENT_UNAVAILABLE",
                summary=(
                    "The planner agent could not produce a candidate. "
                    "The deterministic playbook plan is unaffected."
                ),
                detail={
                    "reason": str(exc)[:300],
                    "llm_mode": self.modes.llm.value,
                    "phase": phase,
                    "llm_phase": getattr(exc, "phase", "unknown"),
                    "status_code": getattr(exc, "status_code", None),
                    "finish_reason": getattr(exc, "finish_reason", None),
                    "content_length": getattr(exc, "content_length", None),
                    "budget_seconds": budget,
                    "primary_demo_budget": primary_demo_budget,
                    "pooled": allowance.pooled if allowance else None,
                    "pool_remaining_seconds": (self._planner_pool_seconds if allowance else None),
                },
            )
        except Exception as exc:
            # Any other failure must not block recovery.
            log.error(
                "planner_candidate_failed",
                incident_reference=ctx.incident_reference,
                phase=phase,
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
            await self._journal(
                ctx,
                stage=STAGE_PLAN,
                actor=ACTOR_ORCHESTRATOR,
                event_type="PLANNER_AGENT_FAILED",
                summary=(
                    "The planner agent failed unexpectedly. "
                    "The deterministic playbook plan is unaffected."
                ),
                detail={
                    "error": type(exc).__name__,
                    "reason": str(exc)[:300],
                    "phase": phase,
                    "llm_mode": self.modes.llm.value,
                },
            )

    # ------------------------------------------------------------------------ assurance

    async def assure(self, ctx: WorkflowContext, task: PlanTask) -> AssuranceResult:
        """Run the Decision Assurance Gate. Delegates to Stream B's gate.

        Returns the gate's result, or a refusal record when the gate could not evaluate.
        Never returns an executable result the gate did not produce.
        """
        return (await self._assure(ctx, task)).result

    async def _incident_clock(self, ctx: WorkflowContext) -> datetime:
        """The incident's reference time: when the disruption happened.

        One clock, used everywhere evidence is selected or aged — the Delay Risk `as_of` and
        the gate's freshness reference. Two clocks for those two jobs is how a replayed
        scenario ends up scoring a storm against the next day's clear-weather observation.

        This is deliberately NOT the clock used for audit timestamps. Those are real time.
        """
        incident = await self._session.get(Incident, ctx.incident_id)
        if incident is None or incident.opened_at is None:
            return self._now()
        return _as_utc(incident.opened_at) or self._now()

    async def _gate_inputs(
        self, ctx: WorkflowContext, task: PlanTask
    ) -> tuple[dict[str, Any], Any]:
        """Gather everything the six checks need.

        Stream B's checks are pure functions — "no check reaches back for a database row, a
        provider response or the clock" — so the orchestrator, which owns the context, is
        where those facts are collected. Gathering a fact is not judging it: nothing here
        decides a check outcome.

        `required_facts` and `constraints` come from Stream B's
        `policy.requirements.gate_requirements`, which is the contract this stream was
        waiting on. They are still never assembled here: the facts a rule needs are a
        property of the rule, so asking is correct and deriving would be Stream A writing
        policy. For an action the pack has nothing to say about, B answers
        `policy_bearing=False` with empty lists — an authoritative answer rather than the
        guess this stream had before.

        `provided_facts` carries only facts a caller supplied explicitly, under
        `inputs["facts"]`. Nothing is synthesised to satisfy a requirement, because a fact
        invented to make `evidence_complete` pass is the exact failure that check exists to
        catch.

        Everything else is the orchestrator's own knowledge: resolved entities from real
        lookups, prior actions so conflicts are visible, and the observation the assessment
        actually reasoned from.
        """
        referenced = list(task.target_refs)
        facts = self._policy_facts(task)
        requirements = await self._policy_requirements(ctx, task, facts)
        gathered = {
            "action_type": task.action.value,
            "target_refs": referenced,
            "referenced_refs": referenced,
            "resolved_entities": await self._resolve_entities(referenced),
            "payload": dict(task.inputs),
            "pending_or_executed": await self._prior_actions(ctx),
            "sources": await self._source_timestamps(ctx),
            "required_facts": list(requirements.required_facts),
            "provided_facts": facts,
            "constraints": list(requirements.constraints),
            "extra_evidence_refs": list(ctx.evidence_refs),
        }
        return gathered, requirements

    def _policy_facts(self, task: PlanTask) -> dict[str, Any]:
        """Trip-context facts a caller supplied, and only those.

        The orchestrator does not assemble a trip context. When one is absent for a
        policy-bearing action, Stream B's requirements fail closed and the gate refuses,
        which is the right outcome: an entitlement decided on facts nobody supplied would be
        an unreviewed legal claim.
        """
        supplied = task.inputs.get("facts")
        return dict(supplied) if isinstance(supplied, dict) else {}

    async def _policy_requirements(
        self, ctx: WorkflowContext, task: PlanTask, facts: dict[str, Any]
    ) -> Any:
        """Ask Stream B what this action must satisfy. Never raises, by their contract.

        Supplies the proposal's **authorship** as well, which is what lets Stream B refuse two
        things a deterministic proposal can never do: assert a field only the system may author,
        and cite a reference nobody recorded. Without it those constraints are never generated, so
        a model could put `assurance_decision` in a payload or invent an evidence ref and nothing
        would object — the gate would evaluate a claim it had no reason to distrust.

        For a deterministic plan this is `Authorship.deterministic`, for which Stream B returns no
        constraints at all. Phase 1 and Phase 2 behaviour is therefore byte-identical, which is the
        property that lets the frozen gate stay frozen.

        The refusal arrives as `POLICY_CONSTRAINT_BREACH` on `policy_compliant`, classified as a
        conflict rather than an approval request — an operator cannot make a fabricated assertion
        true by agreeing with it.
        """
        from app.policy.requirements import gate_requirements

        authorship, proposed_refs = await self._proposal_authorship(ctx)
        return gate_requirements(
            action_type=task.action.value,
            facts=facts,
            settings=self._settings,
            authorship=authorship,
            payload=dict(task.inputs),
            proposed_evidence_refs=proposed_refs,
            known_evidence_refs=self._corroboration_baseline(ctx, task),
        )

    def _corroboration_baseline(self, ctx: WorkflowContext, task: PlanTask) -> list[str]:
        """Every reference the system can independently trace, for the citation check.

        Two sources, and leaving either out breaks the check in a different direction:

        * `ctx.evidence_refs` — what the services recorded: the observation, the airport, the
          runway, the ruleset hash.
        * `task.target_refs` — the incident and flight the **orchestrator itself supplied** to the
          planner. Reflection replaces a model's invented refs with these before anything persists,
          so they are traceable by construction.

        Omitting the target refs was a real defect: the fixture planner cites exactly the refs it
        was handed, and the gate refused every one as uncorroborated. A well-behaved model citing
        what the orchestrator gave it must pass, or the check is noise and gets ignored — which is
        worse than not having it, because the one time it fires for real nobody will look.

        This does not weaken the check. An invented `metar:NOWHERE` is in neither set and is still
        refused; that is what the check exists for.
        """
        baseline = list(ctx.evidence_refs)
        for ref in task.target_refs:
            if ref not in baseline:
                baseline.append(ref)
        return baseline

    async def _proposal_authorship(self, ctx: WorkflowContext) -> tuple[Any, list[str]]:
        """Who wrote the plan being assured, and which refs it claimed.

        Read from the plan row rather than tracked in memory, because the plan is the durable
        record of authorship and a resumed run has no in-memory history. A plan that cannot be
        loaded is treated as model-authored: the conservative direction, since the alternative is
        extending deterministic trust to a proposal whose origin is unknown.
        """
        from app.assurance.authorship import ProposalAuthorship

        plan = (
            await self._current_plan(ctx.incident_id)
            if ctx.plan_id is None
            else (await self._session.get(Plan, ctx.plan_id))
        )
        if plan is None:
            return ProposalAuthorship.from_model(generator="unknown"), []

        generator = plan.generator or FALLBACK_GENERATOR
        if generator == FALLBACK_GENERATOR:
            return ProposalAuthorship.deterministic(generator=generator), []

        # Model-authored. The refs it cited live in the stored response, which is the only place
        # they exist — the plan's task rows carry the orchestrator's refs, not the model's.
        raw = plan.raw_response if isinstance(plan.raw_response, dict) else {}
        cited = raw.get("evidence_refs")
        proposed = [str(ref) for ref in cited] if isinstance(cited, list) else []
        return (
            ProposalAuthorship.from_model(generator=generator, prompt_version=plan.prompt_version),
            proposed,
        )

    async def _resolve_entities(self, refs: Sequence[str]) -> dict[str, Any]:
        """Resolve each `kind:id` reference against the database.

        An unresolvable reference is **omitted**, not recorded as empty. That is what lets
        `entities_valid` fail on a hallucinated or stale entity instead of waving it through.
        """
        resolved: dict[str, Any] = {}
        for ref in refs:
            kind, _, identifier = ref.partition(":")
            if kind == "flight" and identifier.isdigit():
                flight = await self._session.get(Flight, int(identifier))
                if flight is not None:
                    resolved[ref] = {
                        "id": flight.id,
                        "flight_number": flight.flight_number,
                        "status": flight.status,
                        "origin_icao": flight.origin_icao,
                        "destination_icao": flight.destination_icao,
                    }
            elif kind == "incident":
                stmt = select(Incident).where(Incident.reference == identifier).limit(1)
                incident = (await self._session.execute(stmt)).scalars().first()
                if incident is not None:
                    resolved[ref] = {
                        "id": incident.id,
                        "reference": incident.reference,
                        "state": str(incident.state),
                    }
        return resolved

    async def _prior_actions(self, ctx: WorkflowContext) -> list[dict[str, Any]]:
        """Actions already recorded for this incident, so `no_conflicts` can see them.

        This is what lets the gate catch a duplicate booking or a second notification for
        the same passengers, rather than relying on the idempotency key alone.
        """
        stmt = (
            select(Action, PlanTaskRow)
            .join(PlanTaskRow, Action.plan_task_id == PlanTaskRow.id)
            .join(Plan, PlanTaskRow.plan_id == Plan.id)
            .where(Plan.incident_id == ctx.incident_id)
            .order_by(Action.id)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "action_type": task_row.action_type,
                "target_refs": list(task_row.target_refs or []),
                "status": str(action.status),
                "plan_task_id": task_row.id,
            }
            for action, task_row in rows
        ]

    async def _source_timestamps(self, ctx: WorkflowContext) -> dict[str, datetime | None]:
        """Observation timestamps for `sources_fresh`, keyed `<kind>:<identifier>`.

        This must report the age of the observation the system **actually reasoned from**,
        which is the same one Delay Risk selected: the latest actual report at or before the
        incident clock. Reporting the newest row in the table instead means judging the
        freshness of evidence no decision used — and for a replayed scenario that row is
        dated *after* the incident, which is not a freshness question at all.

        Forecasts are excluded for the same reason: a TAF is not an observation of what
        happened, and ageing one against `metar_minutes` compares unlike things.

        Only sources that exist are reported. A source is never invented with `now` as its
        timestamp, because that would manufacture freshness — precisely what this check
        exists to detect.
        """
        sources: dict[str, datetime | None] = {}
        if ctx.flight_id is None:
            return sources
        flight = await self._session.get(Flight, ctx.flight_id)
        if flight is None:
            return sources

        as_of = await self._incident_clock(ctx)
        stmt = (
            select(WeatherObservation)
            .where(
                WeatherObservation.airport_icao == flight.origin_icao,
                WeatherObservation.is_forecast.is_(False),
                WeatherObservation.observed_at <= as_of,
            )
            .order_by(WeatherObservation.observed_at.desc())
            .limit(1)
        )
        observation = (await self._session.execute(stmt)).scalars().first()
        if observation is not None:
            sources[f"metar:{flight.origin_icao}"] = _as_utc(observation.observed_at)
        return sources

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

        config, config_hash = assurance_adapter.load_config()
        gathered, requirements = await self._gate_inputs(ctx, task)
        try:
            result = await assurance_adapter.evaluate(
                **gathered,
                config=config,
                config_hash=config_hash or self.modes.assurance_config_hash,
                # The incident's own clock, not the wall clock.
                #
                # `now` is the reference `sources_fresh` measures an observation against. Using
                # wall time means a scenario replayed the next day reports every source stale,
                # which is not an operational risk — it is an artefact of when the demo ran.
                # Anchoring to the incident asks the question that matters: was this evidence
                # current when the disruption happened?
                #
                # This does not soften the check. An observation already stale at `opened_at`
                # still fails. And it does not touch the audit trail: `AssuranceResult.
                # evaluated_at` is set by the gate from the real clock, so the record still
                # says when the decision was actually made.
                now=await self._incident_clock(ctx),
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
                gathered=gathered,
                requirements=requirements,
            )
        return _AssuranceOutcome(
            result=result, gate_available=True, gathered=gathered, requirements=requirements
        )

    async def _record_assurance(
        self,
        ctx: WorkflowContext,
        task_row: PlanTaskRow,
        outcome: _AssuranceOutcome,
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

        summary = f"{task_row.action_type} -> {result.decision.value}"
        if not outcome.gate_available:
            summary = f"{task_row.action_type} -> refused: {outcome.unavailable_reason}"
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
                # What each check had to work with, and where the policy inputs came from.
                "gate_inputs": _gate_input_coverage(outcome.gathered, outcome.requirements),
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

           A plan approval satisfies this without any special case: the approval service writes a
           `human_decision` with `scope='plan'` for each evaluation it covers, so it arrives here
           as an ordinary decision. The engine therefore never needs to know that plan approvals
           exist, and `test_phase2_guards` asserts it does not.

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
            # Stream C's services are pure; their loaders need the session. The orchestrator
            # owns the transaction, so it is the one that can hand it over.
            session=self._session,
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
            summary=f"{task_row.action_type} -> {result.status.value}: {result.reason}",
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
            step_state = ctx.state
            if step_state is IncidentState.planning:
                self._pending_planner_plan_id = None
            await handler(ctx)
            try:
                await self._session.commit()
            except Exception as exc:
                if (
                    step_state is IncidentState.planning
                    and self._pending_planner_plan_id is not None
                ):
                    log.error(
                        "planner_candidate_persistence_failed",
                        incident_reference=ctx.incident_reference,
                        plan_id=self._pending_planner_plan_id,
                        phase="commit",
                        error=type(exc).__name__,
                    )
                raise
            if step_state is IncidentState.planning and self._pending_planner_plan_id is not None:
                log.info(
                    "planner_candidate_created",
                    incident_reference=ctx.incident_reference,
                    plan_id=self._pending_planner_plan_id,
                    phase="committed",
                )
                self._pending_planner_plan_id = None
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
        assessment = await self._assess_delay_risk(ctx)
        await self._transition(
            ctx,
            IncidentState.planning,
            stage=STAGE_PLAN,
            summary="Impact assessed; generating a recovery plan",
            detail={"llm_mode": self.modes.llm.value, **(assessment or {})},
        )

    async def _risk_airport(self, incident: Incident | None, flight: Flight) -> str:
        """Which airport's weather explains this flight's disruption.

        The group's airport when the incident belongs to one, because the group declares where the
        disruption is. Otherwise the flight's origin.

        This matters for arrivals. UK 705 is VAAH to VOBL: the storm is at VOBL, its destination.
        Reading the origin's weather asked "what is the weather in Ahmedabad", found no observation
        for it, and left the one inbound flight in the cascade with no risk assessment — so the
        cascade graph drew seven root-cause edges for eight declared flights and the flight
        appeared in the picture with nothing explaining why it was there.
        """
        if incident is not None and incident.group_id is not None:
            group = await self._session.get(IncidentGroup, int(incident.group_id))
            if group is not None and group.airport_icao:
                return str(group.airport_icao)
        return str(flight.origin_icao)

    async def _record_weather_source(
        self, ctx: WorkflowContext, ingest: WeatherIngestOutcome, *, scored: Any
    ) -> None:
        """Say so, loudly, when a live-configured run did not actually score live data.

        There are two ways that happens, and both are legitimate — but neither may be silent,
        because a run badged live that reasoned from an archived row is exactly the
        misrepresentation this system exists to avoid.

        1. **Nothing was retrieved.** The provider timed out, was rate limited, or has no current
           METAR for the airport. The ledger keeps what it had and the score is computed from it.
        2. **Something was retrieved but not selected.** The observation is newer than the
           incident's own clock, so the existing rule declines it — correctly, since scoring a
           past disruption against a later reading is the leakage this pipeline guards against.
           Replaying a historical scenario in live mode always lands here.

        Nothing is degraded and nothing is substituted: the selection rule is unchanged and the
        archived row was always the right answer for a historical incident. What is added is the
        record that live was tried, and what it produced.
        """
        if not ingest.consulted and ingest.reason is None:
            # Fixture mode. No provider was asked, so there is nothing to report and the Phase 1-4
            # journal is byte-for-byte what it was.
            return

        scored_ref = getattr(scored, "source_ref", None)
        if ingest.retrieved and scored_ref == ingest.source_ref:
            # The live reading is the one that was scored. Recorded in the risk entry's
            # `weather_source` block; no separate entry needed.
            return

        if not ingest.retrieved:
            await self._journal(
                ctx,
                stage=STAGE_ASSESS,
                actor="delay_risk_service",
                event_type=EVENT_LIVE_UNAVAILABLE,
                summary=(
                    f"No live observation could be obtained for {ingest.airport_icao}; the risk "
                    "was scored from the observation already in the ledger."
                ),
                detail={
                    DETAIL_KEY: ingest.as_detail(),
                    "scored_provenance_kind": getattr(scored, "provenance_kind", None),
                    "scored_source_ref": scored_ref,
                },
            )
            return

        await self._journal(
            ctx,
            stage=STAGE_ASSESS,
            actor="delay_risk_service",
            event_type=EVENT_LIVE_NOT_SCORED,
            summary=(
                f"A live observation for {ingest.airport_icao} was recorded but not scored: it is "
                "later than this incident's reference time, so the observation that was current "
                "when the incident opened was used instead."
            ),
            detail={
                DETAIL_KEY: ingest.as_detail(),
                "scored_provenance_kind": getattr(scored, "provenance_kind", None),
                "scored_source_ref": scored_ref,
                "scored_observed_at": getattr(scored, "observed_at", None),
                "resolution": (
                    "Expected when replaying a historical scenario. An incident opened now scores "
                    "the live observation, because the incident clock is then current."
                ),
            },
        )

    async def _assess_delay_risk(self, ctx: WorkflowContext) -> dict[str, Any] | None:
        """Score disruption risk from the recorded observation, and persist a Prediction.

        This is evidence gathering, not an action, so it does not pass through the Decision
        Assurance Gate — there is no external side effect and nothing to authorise. It
        matches the flow in docs/02-disruption-flow.md, where the Delay Risk service runs
        before the incident is worked.

        **The `as_of` timestamp is the incident's own `opened_at`, not the wall clock.** The
        archive holds later, clear-weather observations for VOBL, so asking "what is the
        latest METAR now" scores a storm at zero. Asking "what was known when this incident
        opened" is both correct and reproducible, which is what makes a replay meaningful.
        """
        if ctx.flight_id is None:
            return None
        flight = await self._session.get(Flight, ctx.flight_id)
        if flight is None:
            return None

        incident = await self._session.get(Incident, ctx.incident_id)
        as_of = await self._incident_clock(ctx)
        airport_icao = await self._risk_airport(incident, flight)

        # Live weather, when configured, adds the current observation to the ledger that
        # `load_delay_risk_inputs` already reads. It does not choose which observation is scored —
        # the existing "newest actual reading at or before the incident clock" rule still does,
        # so there is one selection rule rather than a second one for live mode. In fixture mode
        # nothing is consulted and nothing is written.
        weather_ingest = await ingest_live_weather(
            self._session,
            airport_icao,
            as_of=as_of,
            settings=self._settings,
            mode=self.modes.weather,
        )

        try:
            weather, runways, ruleset = await load_delay_risk_inputs(
                self._session, airport_icao, as_of=as_of
            )
        except LookupError as exc:
            # No observation to reason from. Recorded, and the risk stays absent rather than
            # being defaulted to a number nobody measured. When live mode was on, why the live
            # lookup did not supply one belongs in the same entry — otherwise the operator sees
            # "no observation" with no indication that a live source was even tried.
            await self._journal(
                ctx,
                stage=STAGE_ASSESS,
                actor="delay_risk_service",
                event_type="DELAY_RISK_UNAVAILABLE",
                summary=f"No weather observation available for {airport_icao}",
                detail={
                    "airport_icao": airport_icao,
                    "detail": str(exc),
                    DETAIL_KEY: weather_ingest.as_detail(),
                },
            )
            return None

        await self._record_weather_source(ctx, weather_ingest, scored=weather)

        result = await DelayRiskService().execute(
            weather=weather,
            runways=runways,
            ruleset=ruleset,
            event_threshold=self._settings.delay_risk_event_threshold,
        )
        payload = result.payload

        prediction = Prediction(
            flight_id=flight.id,
            airport_icao=airport_icao,
            predicted_at=as_of or self._now(),
            risk_index=int(payload["risk_index"]),
            risk_level=payload["risk_level"],
            rule_version=str(payload["rule_version"]),
            factors=payload.get("factors") or [],
            evidence_refs=list(result.evidence_refs),
        )
        self._session.add(prediction)
        await self._session.flush()

        if incident is not None and incident.prediction_id is None:
            incident.prediction_id = prediction.id
            await self._session.flush()

        for ref in result.evidence_refs:
            if ref not in ctx.evidence_refs:
                ctx.evidence_refs.append(ref)

        await self._journal(
            ctx,
            stage=STAGE_ASSESS,
            actor="delay_risk_service",
            event_type="HIGH_RISK_DELAY"
            if payload.get("event_recommended")
            else "DELAY_RISK_SCORED",
            summary=(
                f"Risk index {payload['risk_index']} ({payload['risk_level']}) "
                f"against threshold {payload.get('event_threshold')}"
            ),
            detail={
                "prediction_id": prediction.id,
                "as_of": as_of.isoformat() if as_of else None,
                "rule_version": payload.get("rule_version"),
                "ruleset_version": payload.get("ruleset_version"),
                "factors": [factor.get("name") for factor in (payload.get("factors") or [])],
                "observation_age_minutes": payload.get("observation_age_minutes"),
                "is_stale": payload.get("is_stale"),
                "missing_inputs": payload.get("missing_inputs") or [],
                # Which observation was actually scored, and where it came from. The score alone
                # cannot answer "was this live data?", and that is the first question asked of any
                # number a live-configured run produces.
                "weather_source": {
                    "mode": self.modes.weather.value,
                    "provenance_kind": weather.provenance_kind,
                    "source_ref": weather.source_ref,
                    "observed_at": weather.observed_at,
                },
                DETAIL_KEY: weather_ingest.as_detail(),
            },
        )

        if payload.get("event_recommended"):
            await self._publish(
                HighRiskDelay(
                    producer="delay_risk_service",
                    correlation_id=ctx.correlation_id,
                    incident_id=ctx.incident_id,
                    flight_id=flight.id,
                    risk_index=int(payload["risk_index"]),
                    risk_level=payload["risk_level"],
                    rule_version=str(payload["rule_version"]),
                    factors=[
                        str(factor.get("name"))
                        for factor in (payload.get("factors") or [])
                        if factor.get("name")
                    ],
                    evidence_refs=list(result.evidence_refs),
                ),
                ctx,
            )

        return {
            "prediction_id": prediction.id,
            "risk_index": payload["risk_index"],
            "risk_level": payload["risk_level"],
        }

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

        # A gate that asks for a person is an approval request. A gate blocked on evidence or on a
        # conflict is not one, and must not be filed as one: `may_approve_action` forbids approving
        # past a missing fact, so parking here would wait for a decision the system itself refuses
        # to accept. `POST /assurance/{id}/decision` answers every such attempt with 409, the
        # incident never leaves `awaiting_approval`, and a cascade whose other members resolved sits
        # in `executing` for ever with `awaiting_approval_count` stuck above zero. That is the
        # Phase 3 stall this branch exists to end.
        #
        # Same principle as the unavailable-gate branch above: an authorisation boundary nobody may
        # cross is a block, and it says which fact to fix rather than waiting for a signature that
        # cannot help. Nothing is approved automatically and no risk-only hold is affected.
        if not is_approvable(outcome.result):
            await self._block(
                ctx,
                reason=_unapprovable_reason(task_row.action_type, outcome.result),
                detail={
                    "plan_task_id": task_row.id,
                    "assurance_id": evaluation.id,
                    "action_type": task_row.action_type,
                    "blocking": [name.value for name in outcome.result.blocking],
                    "unapprovable_reasons": unapprovable_reasons(outcome.result),
                },
            )
            return

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
                # Nothing the gate held is outstanding. If work remains, go and do it rather than
                # blocking: an incident whose only `needs_human` task is a service's surfaced
                # decision can never be cleared by an approval, so waiting would never end.
                if await self._next_actionable_task(ctx) is not None:
                    await self._transition(
                        ctx,
                        IncidentState.assuring,
                        stage=STAGE_ASSURE,
                        summary="No decision outstanding; submitting the next task to the gate",
                        detail={"plan_id": ctx.plan_id},
                    )
                    return
                # Genuinely nothing left and nothing pending. `awaiting_approval -> resolved` is
                # not a legal transition and must not become one, so this blocks — and names the
                # outstanding items so the reason is actionable rather than mysterious.
                outstanding = [
                    str(row.action_type)
                    for row in await self._plan_tasks(ctx)
                    if TaskState(row.state) is TaskState.needs_human
                ]
                await self._block(
                    ctx,
                    reason=(
                        "no operator decision is outstanding and no task can proceed; "
                        f"outstanding items: {', '.join(sorted(outstanding)) or 'none'}"
                    ),
                    detail={"plan_id": ctx.plan_id, "outstanding_actions": sorted(outstanding)},
                )
                return
            plan_task_id, evaluation_id = resolved
            ctx.metadata["current_plan_task_id"] = plan_task_id
            ctx.metadata["current_assurance_id"] = evaluation_id

        decision = await self._human_decision(int(evaluation_id))
        if decision is None:
            # An incident parked on an evaluation nobody may approve is not resting, it is stuck:
            # `POST /assurance/{id}/decision` refuses it with 409, so no run will ever find a
            # decision here. `_step_assuring` no longer creates that state, but a database written
            # before it did still contains it, and those incidents have to be able to finish.
            # Recovering them here rather than leaving them to a manual repair is the difference
            # between a cascade that can be driven to a conclusion and one that cannot.
            recorded = await self._session.get(AssuranceEvaluation, int(evaluation_id))
            if recorded is not None:
                result = _result_from_row(recorded)
                if not is_approvable(result):
                    held_task = await self._session.get(PlanTaskRow, int(plan_task_id))
                    action_type = held_task.action_type if held_task else "this action"
                    await self._block(
                        ctx,
                        reason=_unapprovable_reason(action_type, result),
                        detail={
                            "plan_task_id": plan_task_id,
                            "assurance_id": evaluation_id,
                            "action_type": action_type,
                            "unapprovable_reasons": unapprovable_reasons(result),
                            "recovered_from": "awaiting_approval",
                        },
                    )
                    return

            # Not an error. Waiting for a person is a legitimate resting state.
            #
            # A plan approval needs no special case here: Stream A's approval service writes a
            # `human_decision` with `scope='plan'` for every evaluation it covers, so this lookup
            # finds it exactly as it finds an action-scoped one. The engine stays unaware that
            # plan approvals exist, which is what `test_phase2_guards` asserts.
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
                # Says what the orchestrator did, not what the operator did. The approval
                # itself is the adjacent human-attributed entry; two entries both reading
                # "Operator approved" is what made the attribution ambiguous in the first place.
                summary=f"Proceeding on operator approval of evaluation {evaluation_id}",
                detail={
                    "assurance_id": evaluation_id,
                    "plan_task_id": plan_task_id,
                    "approved_by": decision.actor_id,
                },
            )
            return

        if task_row is not None:
            task_row.state = TaskState.rejected
            await self._session.flush()
        # The rejection itself is journalled by POST /assurance/{id}/decision, attributed to
        # the operator and timestamped when they decided. Writing it again here would put a
        # second entry for one human act on the timeline, at the later moment the run noticed.
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

        if outcome.result.status is ActionStatus.needs_human and _carries_evidence(outcome.result):
            # The service did its work and surfaced a decision. That is not a failure, and it
            # must not stop the rest of the plan.
            #
            # The case that forced this distinction is the hotel allocation: 71 of 87 rooms are
            # secured and 16 remain, which is a real decision for a person — raise the cap, go
            # further out, or accept that some passengers wait. Treating it as a failure would
            # abandon the 71 rooms that were secured *and* stop the connection, crew and
            # notification work for 604 passengers because 32 of them lack a bed. The refusal is
            # recorded on the action, named on the timeline, and carried into the blast radius as
            # `rooms_short`; nothing about it is hidden by continuing.
            #
            # A `failure`, a `skipped`, or a `needs_human` with no provenance still blocks, below.
            # The difference is whether the service actually worked: `_carries_evidence` is the
            # test, and it is why an unimplemented service still stops the plan instead of being
            # quietly filed as an outstanding item.
            await self._journal(
                ctx,
                stage=STAGE_EXECUTE,
                actor=ACTOR_ORCHESTRATOR,
                event_type="TASK_NEEDS_HUMAN",
                summary=(
                    f"{task_row.action_type} needs a person: {outcome.result.reason} "
                    "Continuing with the rest of the plan."
                ),
                detail={
                    "action_id": outcome.action_id,
                    "plan_task_id": task_row.id,
                    "status": outcome.result.status.value,
                    **outcome.result.payload,
                },
            )
            if await self._next_actionable_task(ctx) is not None:
                await self._transition(
                    ctx,
                    IncidentState.assuring,
                    stage=STAGE_ASSURE,
                    summary="Outstanding item recorded; submitting the next task to the gate",
                    detail={"plan_id": ctx.plan_id, "plan_task_id": task_row.id},
                )
                return
            await self._resolve(ctx)
            return

        if outcome.result.status is not ActionStatus.success:
            # A failure, a skip, or a refusal with no provenance stops the plan. Nothing is
            # invented to keep the run looking healthy.
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
        """The plan this incident is being driven by.

        Order matters, and it is not "the latest".

        1. **The selected plan**, when an operator chose one (migration 0005). That is an explicit,
           attributed decision and it wins.
        2. Otherwise **the earliest** plan — the one the run started with.

        Taking the latest was correct while an incident could only have one plan. It stopped being
        correct when candidates arrived: opening the comparison screen creates sibling plans, and
        "latest" then silently switches a *running* incident onto a fresh plan whose tasks have
        never been assured. The operator's approval still points at the old plan's task, so the
        incident asks for approval again and blocks — which is exactly what happened to the primary
        flight before this was fixed. A read must never re-route a live run.
        """
        stmt = (
            select(Plan)
            .where(Plan.incident_id == incident_id, Plan.selection_state == "selected")
            .order_by(Plan.id)
            .limit(1)
        )
        selected = (await self._session.execute(stmt)).scalars().first()
        if selected is not None:
            return selected

        stmt = select(Plan).where(Plan.incident_id == incident_id).order_by(Plan.id).limit(1)
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
        """The first task genuinely waiting on a person, as `(plan_task_id, evaluation_id)`.

        "Pending approval" is defined by the **evaluation's decision**, not by the task state.
        Task state `needs_human` is overloaded: the gate holding a task and a service surfacing a
        decision both land there, and they need opposite handling.

        Keying off the task state alone deadlocked the run. After the hotel allocation reported a
        16-room shortfall its task became `needs_human`, and the engine then waited forever for an
        operator decision on an evaluation that had already said `execute` and whose action had
        already run. Filtering on `decision == needs_human` excludes it, because the gate never
        held it.

        Note what is deliberately *not* filtered: whether a decision already exists. An approved
        evaluation still needs its task executed, so it is still the pending item until it runs.
        """
        for row in await self._plan_tasks(ctx):
            if TaskState(row.state) is not TaskState.needs_human:
                continue
            stmt = (
                select(AssuranceEvaluation)
                .where(
                    AssuranceEvaluation.plan_task_id == row.id,
                    AssuranceEvaluation.decision == AssuranceDecision.needs_human,
                )
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
        outstanding = [r for r in rows if TaskState(r.state) is TaskState.needs_human]
        metrics = {
            "tasks_total": len(rows),
            "tasks_succeeded": sum(1 for r in rows if TaskState(r.state) is TaskState.succeeded),
            "tasks_skipped": sum(1 for r in rows if TaskState(r.state) is TaskState.skipped),
            # Tasks whose service surfaced a decision rather than completing. Counted separately
            # and named, so "resolved" can never be read as "nothing left to do".
            "tasks_needing_human": len(outstanding),
            "outstanding_actions": [str(r.action_type) for r in outstanding],
            "steps_taken": ctx.steps_taken,
        }
        # The summary is the line an operator reads on the timeline, so it states the outstanding
        # work rather than leaving it to be inferred from a metric.
        summary = (
            "Every task in the plan completed"
            if not outstanding
            else (
                f"Plan worked to completion with {len(outstanding)} item(s) still needing a "
                f"person: {', '.join(sorted(str(r.action_type) for r in outstanding))}"
            )
        )
        await self._transition(
            ctx,
            IncidentState.resolved,
            stage=STAGE_RESOLVE,
            summary=summary,
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


#: Recorded when the pack has nothing to say about an action, so two green checks are not
#: mistaken for a policy verification that never happened.
POLICY_NOT_APPLICABLE = "POLICY_NOT_APPLICABLE"


def _gate_input_coverage(gathered: dict[str, Any], requirements: Any = None) -> dict[str, Any]:
    """State what each check had to work with, and where the policy inputs came from.

    Six green checks look like six verifications. When the pack has nothing to say about an
    action — `POLICY_BEARING_ACTIONS` is only `evaluate_entitlements` — `evidence_complete`
    and `policy_compliant` pass with empty lists. That is now Stream B's authoritative
    answer rather than this stream's guess, but it is still worth naming in the record, so a
    reader can tell "checked and satisfied" from "nothing applicable to check".

    `selected_rule_ids` and `pack_hash` make the answer traceable: they say which rules
    decided the requirement, without anybody inferring it.
    """
    coverage: dict[str, Any] = {
        "required_facts": len(gathered.get("required_facts") or []),
        "constraints": len(gathered.get("constraints") or []),
        "provided_facts": len(gathered.get("provided_facts") or {}),
        "resolved_entities": len(gathered.get("resolved_entities") or {}),
        "sources": len(gathered.get("sources") or {}),
        "pending_or_executed": len(gathered.get("pending_or_executed") or []),
    }
    if requirements is None:
        return coverage

    coverage["policy_bearing"] = bool(getattr(requirements, "policy_bearing", False))
    coverage["policy_mode"] = getattr(requirements, "policy_mode", None)
    coverage["selected_rule_ids"] = list(getattr(requirements, "selected_rule_ids", []) or [])
    coverage["pack_hash"] = getattr(requirements, "pack_hash", None)
    blocking = list(getattr(requirements, "blocking_reasons", []) or [])
    if blocking:
        coverage["requirements_blocked"] = blocking

    if not coverage["policy_bearing"]:
        # Not a gap: the pack genuinely has no rule about checking a connection. Named so the
        # two empty checks read as "not applicable" rather than "verified".
        coverage["notice"] = POLICY_NOT_APPLICABLE
        coverage["not_applicable_checks"] = ["evidence_complete", "policy_compliant"]
    return coverage


def _as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive timestamp read back from the database.

    Storage is always UTC in this system — local time is a display concern only — so this
    labels a value rather than converting one. It matters because `sources_fresh` compares
    an observation against an aware `now`, and mixing naive and aware datetimes raises
    TypeError. Postgres returns aware values; SQLite does not, and a driver difference must
    not change whether the freshness check can run.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


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


#: `provenance_kind` on a result that did not come from doing anything. `dispatch.refusal` and
#: the service adapters' `_unavailable` both use it.
PROVENANCE_UNAVAILABLE = "unavailable"


def _carries_evidence(result: ServiceResult) -> bool:
    """Whether a `needs_human` result came from a service that actually ran.

    This is the line between two outcomes that share a status and mean opposite things:

    * The hotel allocation secured 71 of 87 rooms and needs a person to decide about the other
      16. It ran, it produced evidence, and the plan should continue around it.
    * `evaluate_entitlements` has no implementation, so nothing happened at all. Continuing would
      let an incident resolve with a task nobody performed and nobody was told about.

    `provenance_kind == "unavailable"` is the discriminator because a result with no provenance is
    not a finding. Checked alongside the refusal reason codes so a future adapter that forgets the
    provenance still fails closed.
    """
    if result.provenance_kind == PROVENANCE_UNAVAILABLE:
        return False
    reason_code = (result.payload or {}).get("reason_code")
    return reason_code not in {
        dispatch.SERVICE_NOT_IMPLEMENTED,
        "SERVICE_INPUTS_UNAVAILABLE",
    }


def _task_state_for(status: ActionStatus) -> TaskState:
    return {
        ActionStatus.success: TaskState.succeeded,
        ActionStatus.failure: TaskState.failed,
        ActionStatus.skipped: TaskState.skipped,
        ActionStatus.needs_human: TaskState.needs_human,
    }[status]


def _unapprovable_reason(action_type: str, result: AssuranceResult) -> str:
    """Why this action is blocked rather than held, naming the fact to fix.

    One wording for both entry points — the fresh hold in `_step_assuring` and the recovery of an
    incident already parked by an older build in `_step_awaiting_approval` — so an operator reading
    a timeline cannot tell which path produced it, because operationally it is the same fact.
    """
    named = ", ".join(unapprovable_reasons(result))
    return (
        f"{action_type} cannot be authorised, and cannot be approved by a person: "
        f"{named or 'the gate blocked on evidence or a conflict'}. Approval covers risk, never "
        "failed evidence or an unresolved conflict — the underlying fact must change, which "
        "produces a new evaluation."
    )


def _result_from_row(row: AssuranceEvaluation) -> AssuranceResult:
    """Delegates to the adapter, which is the one place this reconstruction lives.

    Kept as a module-level name because tests and the replay path already import it.
    """
    return assurance_adapter.result_from_row(row)
