"""Group-level plan assurance — aggregate, then authorise one action at a time.

P2-D1 makes the review surface the **disruption group**. P2-D3 lets an operator's plan
approval cover low and medium risk actions, while high risk always needs its own decision and
no approval ever covers a failed check.

The boundary this module defends: **`PlanAssuranceResult.authorises_no_action` is `Literal[True]`
in Stream B's contract, and nothing here changes that.** A plan summary never makes an action
executable. What a plan *approval* does is create the ordinary `human_decision` rows that
`execute()` already requires — so there is still exactly one path to execution, and
`execute()` never learns that plan approvals exist.

Stream A's job here is assembly, not judgement. Every rule lives in Stream B:

* the six plan checks and their aggregation — `app.assurance.plan_gate`
* what an approval may cover — `app.assurance.approval`
* the zero-write guard for what-if — `app.assurance.whatif`

Owner: Stream A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.contract import AssuranceResult
from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
    PlanAssuranceResult,
    PlanUnderReview,
    TaskOutcome,
)
from app.assurance.plan_gate import (
    LoadedPlanConfig,
    evaluate_plan,
    load_plan_config,
    task_outcome_from,
)
from app.config import Settings, get_settings
from app.db.scenario_queries import CascadeRollup, cascade_rollup
from app.errors import AssuranceConfigMissing
from app.models.enums import IncidentState
from app.models.workflow import AssuranceEvaluation, Incident, Plan, PlanTask
from app.orchestrator import assurance_adapter

log = structlog.get_logger(__name__)

#: External effects Stream A is responsible for counting: actions that reach outside the
#: airline's own systems and therefore cannot be silently retried. Stream C counts rooms;
#: Stream B counts money; this is the one exposure figure that is ours.
EXTERNAL_EFFECT_ACTIONS: frozenset[str] = frozenset(
    {
        "notify_passengers",
        "reserve_hotel_block",
        "arrange_ground_transport",
        "issue_compensation",
        "rebook_passengers",
    }
)


@dataclass
class PlanScope:
    """The tasks and evidence one plan-level evaluation covers."""

    plan_id: int
    incident_id: int
    incident_reference: str
    group_reference: str
    tasks: list[TaskOutcome] = field(default_factory=list)
    #: Task id -> the PlanTask row, so a caller can act on what the gate reported.
    rows: dict[str, PlanTask] = field(default_factory=dict)


def load_plan_configuration(settings: Settings | None = None) -> LoadedPlanConfig | None:
    """Load the plan-level config, or None when it is unavailable.

    Returning None rather than raising is deliberate: `evaluate_plan(config=None)` is
    fail-closed by design — every check FAILs with `PLAN_CONFIG_MISSING` and the decision is
    `needs_human` at tier `high`. Swallowing the error here would hide the reason; raising
    would make an unconfigured deployment return 500 instead of an honest "nothing is
    authorised". So the error is logged and the fail-closed path is taken.
    """
    settings = settings or get_settings()
    try:
        return load_plan_config(settings.plan_config_path)
    except AssuranceConfigMissing as exc:
        log.error(
            "plan_assurance_config_unavailable",
            outcome="error",
            path=str(settings.plan_config_path),
            reason=str(exc),
        )
        return None


def _external_effects(tasks: list[TaskOutcome]) -> int:
    return sum(1 for task in tasks if task.action_type in EXTERNAL_EFFECT_ACTIONS)


class PlanAssuranceService:
    """Builds Stream B's plan-gate inputs from persisted rows and calls it."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------ task projection

    async def _recorded_result(
        self, task_row: PlanTask
    ) -> tuple[AssuranceResult | None, int | None]:
        """The most recent recorded evaluation for a task, if the gate has already run.

        Reading the recorded evaluation rather than re-evaluating matters: a plan summary must
        describe the same decision the audit trail holds, not a fresh one that might differ
        because an observation aged in between.
        """
        stmt = (
            select(AssuranceEvaluation)
            .where(AssuranceEvaluation.plan_task_id == task_row.id)
            .order_by(AssuranceEvaluation.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            return None, None
        return assurance_adapter.result_from_row(row), row.id

    async def scope_for_plan(self, plan: Plan, *, group_reference: str) -> PlanScope:
        """Project one plan's tasks into Stream B's `TaskOutcome` list."""
        incident = await self._session.get(Incident, plan.incident_id)
        incident_reference = incident.reference if incident else str(plan.incident_id)

        stmt = select(PlanTask).where(PlanTask.plan_id == plan.id).order_by(PlanTask.task_order)
        rows = list((await self._session.execute(stmt)).scalars())

        scope = PlanScope(
            plan_id=plan.id,
            incident_id=plan.incident_id,
            incident_reference=incident_reference,
            group_reference=group_reference,
        )

        by_id = {row.id: row for row in rows}
        for row in rows:
            result, evaluation_id = await self._recorded_result(row)
            task_id = str(row.id)
            scope.rows[task_id] = row
            if result is None:
                # Not yet evaluated. Reported as a gap rather than assumed to pass: the plan
                # gate's `tasks_authorised` check treats an unevaluated task as unauthorised,
                # which is the honest reading.
                scope.tasks.append(
                    TaskOutcome(
                        task_id=task_id,
                        action_type=row.action_type,
                        target_refs=list(row.target_refs or []),
                        depends_on=[str(dep) for dep in (row.depends_on or []) if dep in by_id],
                        decision=assurance_adapter.NOT_EVALUATED_DECISION,
                        risk_tier=assurance_adapter.NOT_EVALUATED_TIER,
                        blocking_kinds=["evidence"],
                        approvable=False,
                        evaluation_id=None,
                    )
                )
                continue

            scope.tasks.append(
                task_outcome_from(
                    task_id=task_id,
                    action_type=row.action_type,
                    result=result,
                    target_refs=list(row.target_refs or []),
                    depends_on=[str(dep) for dep in (row.depends_on or []) if dep in by_id],
                    evaluation_id=evaluation_id,
                )
            )
        return scope

    # ---------------------------------------------------------------------- declarations

    def coverage_from_rollup(
        self, rollup: CascadeRollup, scope_tasks: list[TaskOutcome]
    ) -> CoverageDeclaration:
        """Declare what the plan set covers, against the group's recorded impact.

        `declared=True` is only claimed when the rollup is complete. An incomplete rollup means
        a declared member flight has no incident or has not been assessed, and a coverage claim
        over a partial impact set would read as total.
        """
        impacted = [f"flight:{flight_id}" for flight_id in rollup.member_flight_ids]
        deferred = {
            f"flight:{flight_id}": "no incident is open for this declared member flight"
            for flight_id in rollup.flights_without_incident
        }
        covered = {ref for task in scope_tasks for ref in task.target_refs}
        for ref in impacted:
            if ref not in covered and ref not in deferred:
                deferred[ref] = "no task in this plan targets this flight"

        return CoverageDeclaration(
            declared=rollup.membership_is_declared,
            impacted_refs=impacted,
            deferred=deferred,
        )

    async def exposure_for_group(
        self,
        *,
        rollup: CascadeRollup,
        scope_tasks: list[TaskOutcome],
        rooms_committed: int | None = None,
        total_exposure_inr: int | None = None,
        unresolved_cohorts: list[str] | None = None,
    ) -> ExposureInputs:
        """Assemble the exposure the plan gate measures against its limits.

        Every figure is passed through from the stream that owns it. `None` stays `None`:
        Stream B's exposure check treats an unknown figure as a breach, not as zero, and
        substituting a default here would convert "we do not know" into "it is fine".
        """
        return ExposureInputs(
            total_exposure_inr=total_exposure_inr,
            passengers_affected=rollup.passengers_affected,
            rooms_committed=rooms_committed,
            external_effects=_external_effects(scope_tasks),
            unresolved_cohorts=list(unresolved_cohorts or []),
        )

    # -------------------------------------------------------------------------- evaluate

    async def evaluate_group(
        self,
        *,
        group_id: int,
        group_reference: str,
        plans: list[Plan],
        rooms_committed: int | None = None,
        total_exposure_inr: int | None = None,
        unresolved_cohorts: list[str] | None = None,
    ) -> tuple[PlanAssuranceResult, list[PlanScope], CascadeRollup]:
        """One plan-level evaluation over the group's selected plan set.

        The *plan set* is one selected plan per member incident — the plans stay per-incident,
        which is the Phase 1 invariant; only the review scope is the group.
        """
        rollup = await cascade_rollup(self._session, group_id=group_id)
        loaded = load_plan_configuration(self._settings)

        scopes: list[PlanScope] = []
        tasks: list[TaskOutcome] = []
        for plan in plans:
            scope = await self.scope_for_plan(plan, group_reference=group_reference)
            scopes.append(scope)
            tasks.extend(scope.tasks)

        under_review = PlanUnderReview(
            plan_id=plans[0].id if len(plans) == 1 else None,
            group_reference=group_reference,
            tasks=tasks,
            generator=plans[0].generator if plans else None,
        )
        coverage = self.coverage_from_rollup(rollup, tasks)
        exposure = await self.exposure_for_group(
            rollup=rollup,
            scope_tasks=tasks,
            rooms_committed=rooms_committed,
            total_exposure_inr=total_exposure_inr,
            unresolved_cohorts=unresolved_cohorts,
        )

        result = evaluate_plan(
            plan=under_review,
            coverage=coverage,
            exposure=exposure,
            config=loaded.plan if loaded else None,
            config_version=loaded.version if loaded else "unavailable",
            config_hash=loaded.digest if loaded else "unavailable",
        )
        log.info(
            "plan_assurance_evaluated",
            group_reference=group_reference,
            decision=result.decision.value,
            plan_risk_tier=result.plan_risk_tier.value,
            tasks=result.task_count,
            blocking=[name.value for name in result.blocking],
        )
        return result, scopes, rollup

    # --------------------------------------------------------------------- plan set read

    async def selected_plans(self, group_id: int) -> list[Plan]:
        """One selected plan per member incident, falling back to the latest per incident.

        The fallback keeps Phase 1 behaviour intact: an incident with a single unselected plan
        behaves exactly as it did before candidates existed.
        """
        stmt = (
            select(Plan)
            .join(Incident, Incident.id == Plan.incident_id)
            .where(Incident.group_id == group_id)
            .order_by(Plan.incident_id, Plan.id.desc())
        )
        rows = list((await self._session.execute(stmt)).scalars())

        chosen: dict[int, Plan] = {}
        for plan in rows:
            if plan.selection_state == "discarded":
                continue
            held = chosen.get(plan.incident_id)
            supersedes = (
                held is not None
                and plan.selection_state == "selected"
                and held.selection_state != "selected"
            )
            if held is None or supersedes:
                chosen[plan.incident_id] = plan
        return [chosen[key] for key in sorted(chosen)]

    async def member_incidents(self, group_id: int) -> list[Incident]:
        stmt = select(Incident).where(Incident.group_id == group_id).order_by(Incident.id)
        return list((await self._session.execute(stmt)).scalars())

    async def awaiting_evaluations(self, group_id: int) -> list[AssuranceEvaluation]:
        """Every evaluation in the group that is waiting on a person.

        This is what a plan approval partitions. It is read at approval time, so the operator
        is shown exactly what their signature will cover — and an evaluation produced later in
        the run is never covered, because forward coverage would be a blank cheque.
        """
        stmt = (
            select(AssuranceEvaluation)
            .join(PlanTask, PlanTask.id == AssuranceEvaluation.plan_task_id)
            .join(Plan, Plan.id == PlanTask.plan_id)
            .join(Incident, Incident.id == Plan.incident_id)
            .where(
                Incident.group_id == group_id,
                Incident.state == IncidentState.awaiting_approval,
            )
            .order_by(AssuranceEvaluation.id)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def incident_for_evaluation(self, evaluation: AssuranceEvaluation) -> Incident | None:
        """The incident an evaluation belongs to, via its task and plan.

        `assurance_evaluation` has no `incident_id`: it links through `plan_task`. Walking the
        real path rather than denormalising keeps one answer to "which incident authorised
        this".
        """
        stmt = (
            select(Incident)
            .join(Plan, Plan.incident_id == Incident.id)
            .join(PlanTask, PlanTask.plan_id == Plan.id)
            .where(PlanTask.id == evaluation.plan_task_id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().first()


def summarise(result: PlanAssuranceResult) -> dict[str, Any]:
    """A compact, display-ready view. Counts only — never an aggregate score.

    `docs/18-decision-assurance-gate.md` defines a fail-closed, ordered gate. A mean of six
    checks would be a fiction, so there is no average at task, plan or group level.
    """
    return {
        "decision": result.decision.value,
        "plan_risk_tier": result.plan_risk_tier.value,
        "task_count": result.task_count,
        "blocking": [name.value for name in result.blocking],
        "admissible": result.admissible,
        "requires_human": result.requires_human,
        "authorises_no_action": True,
    }
