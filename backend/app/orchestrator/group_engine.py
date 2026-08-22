"""Group orchestration — STREAM A.

Runs a network disruption. The unit of authorisation stays the **per-flight incident**: this
class opens one incident per declared member flight and drives each through the existing
per-incident `Orchestrator`, unchanged. It adds no authority of its own.

That is the load-bearing decision. The tempting shortcut is a single "group incident" with 604
passengers attached, which would make the eight-flight cascade one approval instead of eight —
collapsing eight separate operational decisions into one click, and losing the ability to say
which flight a given action was authorised for. Group scope is a *lens* over per-flight
incidents, never a replacement for them.

What this class genuinely adds:

* **Membership-driven opening.** Incidents come from `incident_group_flight`, so the group works
  the flights it declares rather than whatever happened to be injected.
* **Derived rollups, recorded.** After the per-incident runs it projects the cascade graph and
  appends a `cascade_snapshot`. Derivation stays the source of truth; the snapshot records what
  was derived and what it was derived from.
* **Plan-level assurance per incident, summarised for the group.** P2-D1 scopes plan assurance
  to the group, so the group summary is the set of per-incident plan results, each carrying its
  own hash and its own exposure.

Journalling: `decision_log` has one scope column, `incident_id`. Group-level entries are
attributed to the **primary** member incident with `incident_group_id` in the detail, rather than
written with a NULL incident. A NULL-scoped entry would be invisible in every existing timeline
view — a group event nobody could find is worse than one filed slightly broadly.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.plan_contract import PlanAssuranceResult
from app.db.scenario_queries import (
    CascadeRollup,
    GroupFlight,
    cascade_rollup,
    group_affected_flights,
    recorded_actions,
)
from app.models.enums import ActionType, IncidentState
from app.models.workflow import Incident, IncidentGroup, Plan
from app.observability.logging import correlation_id_var, get_logger
from app.orchestrator import plan_lifecycle
from app.orchestrator.engine import (
    ACTOR_ORCHESTRATOR,
    STAGE_ASSESS,
    STAGE_DETECT,
    STAGE_RESOLVE,
    Orchestrator,
)
from app.services.blast_radius import BlastRadius, compose_blast_radius
from app.services.cascade_graph import CascadeGraph, project_and_record

log = get_logger(__name__)

GROUP_OPENED = "GROUP_INCIDENTS_OPENED"
GROUP_RUN_COMPLETED = "GROUP_RUN_COMPLETED"
GROUP_SNAPSHOT_RECORDED = "GROUP_SNAPSHOT_RECORDED"

#: Terminal for the purposes of a group run. `blocked` and `failed` are terminal too — a group
#: run must not spin on an incident that has stopped, and must not hide that it stopped.
_TERMINAL = {IncidentState.resolved, IncidentState.blocked, IncidentState.failed}


@dataclass
class IncidentProgress:
    """One member flight's incident and where it got to."""

    flight_id: int
    flight_number: str
    role: str
    incident_id: int
    incident_reference: str
    state: str
    steps_taken: int
    is_terminal: bool
    note: str | None = None
    plan_id: int | None = None
    plan_hash: str | None = None
    #: Evaluations the action gate held for a person, and which are still undecided.
    awaiting_evaluation_ids: list[int] = field(default_factory=list)


@dataclass
class GroupRunResult:
    group_id: int
    group_reference: str
    incidents: list[IncidentProgress] = field(default_factory=list)
    rollup: CascadeRollup | None = None
    graph: CascadeGraph | None = None
    snapshot_hash: str | None = None
    edges_recorded: int = 0
    plan_assurance: list[PlanAssuranceResult] = field(default_factory=list)
    blast_radius: BlastRadius | None = None

    @property
    def is_terminal(self) -> bool:
        """True only when every member incident has stopped.

        Deliberately `all`, not `any`: a group with seven resolved incidents and one still
        awaiting approval is not finished, and reporting it as finished is exactly the failure
        the rollup's completeness flag exists to prevent.
        """
        return bool(self.incidents) and all(item.is_terminal for item in self.incidents)

    @property
    def awaiting_approval(self) -> list[IncidentProgress]:
        return [
            item for item in self.incidents if item.state == IncidentState.awaiting_approval.value
        ]

    @property
    def states(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.incidents:
            counts[item.state] = counts.get(item.state, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def note(self) -> str:
        if not self.incidents:
            return "This group declares no member flights, so there is nothing to run."
        if self.is_terminal:
            return (
                f"All {len(self.incidents)} member incidents have reached a terminal state: "
                + ", ".join(f"{count} {state}" for state, count in self.states.items())
                + "."
            )
        waiting = self.awaiting_approval
        if waiting:
            return (
                f"{len(waiting)} of {len(self.incidents)} member incidents are waiting for an "
                "operator decision. Approve them, or approve the plan where it covers them, and "
                "run the group again."
            )
        return (
            f"{len(self.incidents)} member incidents in progress: "
            + ", ".join(f"{count} {state}" for state, count in self.states.items())
            + "."
        )


class GroupOrchestrator:
    """Drives a whole network disruption without ever becoming the authorisation unit."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        bus: Any | None = None,
        settings: Any | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._bus = bus
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))
        #: Set for the duration of a run so group-level journal entries carry the same correlation
        #: id as the per-incident entries they sit between. An uncorrelated entry breaks the
        #: timeline's guarantee that one request can be traced end to end, and the demo
        #: verification checks exactly that.
        self._correlation_id: str | None = None

    def _child(self) -> Orchestrator:
        """A per-incident orchestrator. The same class the single-incident path uses.

        Constructed fresh per incident rather than reused, because `WorkflowContext` is
        per-incident state and sharing one would let one member's step budget leak into another's.
        """
        return Orchestrator(self._session, bus=self._bus, settings=self._settings, now=self._now)

    # ------------------------------------------------------------------ membership

    async def _group(self, group_id: int) -> IncidentGroup:
        group = await self._session.get(IncidentGroup, group_id)
        if group is None:
            raise LookupError(f"incident group {group_id} not found")
        return group

    async def members(self, group_id: int) -> list[GroupFlight]:
        return await group_affected_flights(self._session, group_id=group_id)

    async def open_group(self, group_id: int) -> list[int]:
        """Open an incident for every declared member flight that lacks one.

        Idempotent: a flight that already carries an incident in this group is left alone. The
        partial unique index on `incident(flight_id)` for active incidents is the backstop, so a
        concurrent open cannot produce two.
        """
        group = await self._group(group_id)
        members = await self.members(group_id)
        opened: list[int] = []

        for member in members:
            if member.incident_id is not None:
                continue
            child = self._child()
            ctx = await child.open_incident(
                member.flight_id,
                str(group.root_cause),
                severity=str(group.severity),
                group_id=group_id,
                demo_dataset_id=group.demo_dataset_id,
                evidence_refs=[
                    f"incident_group_flight:{group.reference}:{member.flight_id}",
                ],
                opened_at=group.opened_at,
            )
            opened.append(ctx.incident_id)

        if opened:
            await self._journal_group(
                group_id,
                event_type=GROUP_OPENED,
                stage=STAGE_DETECT,
                summary=(
                    f"{len(opened)} incidents opened for the flights {group.reference} declares"
                ),
                detail={
                    "incident_ids": opened,
                    "declared_flight_ids": [member.flight_id for member in members],
                    "note": (
                        "One incident per flight. The group is the scope of the disruption; the "
                        "incident stays the unit of authorisation."
                    ),
                },
            )
            await self._session.commit()
        return opened

    # -------------------------------------------------------------------- the run

    async def run_group(
        self, group_id: int, *, correlation_id: str | None = None
    ) -> GroupRunResult:
        """Open what is missing, advance every member incident, then record what was derived.

        Each member is driven by the ordinary per-incident `run()`, which already stops at
        `awaiting_approval` without spinning. The group loop therefore inherits the resting
        behaviour rather than reimplementing it.
        """
        group = await self._group(group_id)
        self._correlation_id = correlation_id or correlation_id_var.get()
        await self.open_group(group_id)

        members = await self.members(group_id)
        progress: list[IncidentProgress] = []

        for member in members:
            if member.incident_id is None:
                # Declared but unopenable — an active incident already exists for the flight
                # outside this group. Reported, not silently skipped.
                progress.append(
                    IncidentProgress(
                        flight_id=member.flight_id,
                        flight_number=member.flight_number,
                        role=member.role,
                        incident_id=0,
                        incident_reference="",
                        state="unopened",
                        steps_taken=0,
                        is_terminal=False,
                        note=(
                            "no incident could be opened for this declared flight; an active "
                            "incident may already exist for it outside this group"
                        ),
                    )
                )
                continue

            child = self._child()
            ctx = await child.load_context(member.incident_id, correlation_id=correlation_id)
            ctx = await child.run(ctx)
            progress.append(
                await self._progress_for(member, ctx.state, ctx.steps_taken, ctx.last_note)
            )

        result = GroupRunResult(
            group_id=group_id, group_reference=group.reference, incidents=progress
        )
        await self._record_derived(result)
        await self._journal_group(
            group_id,
            event_type=GROUP_RUN_COMPLETED,
            stage=STAGE_RESOLVE if result.is_terminal else STAGE_ASSESS,
            summary=result.note,
            detail={
                "states": result.states,
                "is_terminal": result.is_terminal,
                "snapshot_hash": result.snapshot_hash,
                "edges_recorded": result.edges_recorded,
            },
        )
        await self._session.commit()
        return result

    async def _progress_for(
        self,
        member: GroupFlight,
        state: IncidentState,
        steps_taken: int,
        note: str | None,
    ) -> IncidentProgress:
        incident_id = int(member.incident_id or 0)
        incident = await self._session.get(Incident, incident_id)
        plan = (
            (
                await self._session.execute(
                    select(Plan)
                    .where(Plan.incident_id == incident_id)
                    .order_by(Plan.generated_at.desc(), Plan.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return IncidentProgress(
            flight_id=member.flight_id,
            flight_number=member.flight_number,
            role=member.role,
            incident_id=incident_id,
            incident_reference=incident.reference if incident else "",
            state=state.value if isinstance(state, IncidentState) else str(state),
            steps_taken=steps_taken,
            is_terminal=IncidentState(state) in _TERMINAL,
            note=note,
            plan_id=int(plan.id) if plan else None,
            plan_hash=str(plan.plan_hash) if plan and plan.plan_hash else None,
            awaiting_evaluation_ids=await self._undecided_evaluations(incident_id),
        )

    async def _undecided_evaluations(self, incident_id: int) -> list[int]:
        """Evaluations the gate held and nobody has decided yet.

        Left-joined against `human_decision` rather than filtered in Python, so an evaluation
        with a rejection is not reported as still waiting.
        """
        from app.models.workflow import AssuranceEvaluation, HumanDecision, PlanTask

        rows = (
            await self._session.execute(
                select(AssuranceEvaluation.id)
                .join(PlanTask, PlanTask.id == AssuranceEvaluation.plan_task_id)
                .join(Plan, Plan.id == PlanTask.plan_id)
                .outerjoin(HumanDecision, HumanDecision.assurance_id == AssuranceEvaluation.id)
                .where(
                    Plan.incident_id == incident_id,
                    AssuranceEvaluation.decision == "needs_human",
                    HumanDecision.id.is_(None),
                )
                .order_by(AssuranceEvaluation.id)
            )
        ).all()
        return [int(row[0]) for row in rows]

    # ---------------------------------------------------------------- derivation

    async def _record_derived(self, result: GroupRunResult) -> None:
        """Project the graph, append a snapshot, and compose the blast radius.

        Order matters: the graph and rollup are read from recorded actions, so this runs *after*
        the member incidents have advanced. Running it first would snapshot the state before the
        work rather than after it.
        """
        graph, rollup, snapshot, inserted = await project_and_record(
            self._session, group_id=result.group_id
        )
        result.graph = graph
        result.rollup = rollup
        result.snapshot_hash = str(snapshot.snapshot_hash)
        result.edges_recorded = inserted
        result.plan_assurance = await self.plan_assurance(result.group_id, rollup=rollup)
        result.blast_radius = compose_blast_radius(
            rollup=rollup,
            graph=graph,
            passenger_payload=await self._latest_payload(
                result.group_id, ActionType.notify_passengers
            ),
            hotel_payload=await self.hotel_totals(result.group_id),
        )
        await self._journal_group(
            result.group_id,
            event_type=GROUP_SNAPSHOT_RECORDED,
            stage=STAGE_ASSESS,
            summary=(
                f"Cascade snapshot {snapshot.snapshot_hash}: {rollup.flights_affected} flights, "
                f"{rollup.passengers_affected} passengers, {rollup.connections_at_risk} "
                f"connections, {rollup.crew_pairings_affected} rotations"
            ),
            detail={
                "snapshot_hash": snapshot.snapshot_hash,
                "edges_recorded": inserted,
                "is_complete": rollup.is_complete,
                "source_action_ids": list(graph.source_action_ids),
                "source_prediction_ids": list(graph.source_prediction_ids),
            },
        )

    async def _latest_payload(
        self, group_id: int, action_type: ActionType
    ) -> dict[str, Any] | None:
        """The most recent successful payload of one action type across the group.

        Used only to feed figures that another service already computed into the blast radius —
        never to recompute one. `None` when the action has not run, which the blast radius
        reports as a named gap.
        """
        members = await self.members(group_id)
        incident_ids = [m.incident_id for m in members if m.incident_id is not None]
        rows = await recorded_actions(self._session, incident_ids, action_type.value)
        return rows[-1][2] if rows else None

    async def hotel_totals(self, group_id: int) -> dict[str, Any] | None:
        """Accommodation figures summed across the whole group.

        Not the most recent action's payload. Eight flights each allocate against the same finite
        inventory, so the last one to run sees whatever is left — reporting its figures as the
        group's would have shown "9 rooms required, 0 short" for a disruption needing 302 rooms
        with 71 available. The gap is the whole point of the scenario and it has to be the sum.

        Partial allocations are included, because a partial allocation committed real rooms.
        """
        members = await self.members(group_id)
        incident_ids = [m.incident_id for m in members if m.incident_id is not None]
        rows = await recorded_actions(
            self._session,
            incident_ids,
            ActionType.reserve_hotel_block.value,
            statuses=("success", "needs_human"),
        )
        if not rows:
            return None

        required = allocated = cost = 0
        allocations: list[dict[str, Any]] = []
        for _incident_id, _action_id, payload in rows:
            required += int(payload.get("rooms_required") or 0)
            allocated += int(payload.get("rooms_allocated") or 0)
            cost += int(payload.get("total_cost_inr") or 0)
            allocations.extend(payload.get("allocations") or [])

        short = max(0, required - allocated)
        return {
            "rooms_required": required,
            "rooms_allocated": allocated,
            "shortfall_rooms": short,
            "total_cost_inr": cost,
            "allocations": allocations,
            "is_complete": short == 0,
            "shortfall_note": (
                f"All {required} rooms secured across the group."
                if short == 0
                else (
                    f"{allocated} of {required} rooms secured across the group. {short} rooms "
                    "short. Every property within the rate cap is exhausted, so closing the gap "
                    "needs a decision: raise the cap, go further out, or accept that some "
                    "passengers wait."
                )
            ),
        }

    # ------------------------------------------------------------ plan assurance

    async def plan_assurance(
        self, group_id: int, *, rollup: CascadeRollup | None = None
    ) -> list[PlanAssuranceResult]:
        """Plan-level assurance for every member incident's selected plan.

        P2-D1: plan assurance is group-scoped, so every result carries the group reference and is
        evaluated against the group's coverage and exposure. One result per plan rather than one
        for the group, because a plan is what an operator approves and each member flight has its
        own.
        """
        active = rollup or await cascade_rollup(self._session, group_id=group_id)
        loaded = plan_lifecycle.load_plan_gate_config()
        hotel_payload = await self.hotel_totals(group_id)

        members = await self.members(group_id)
        results: list[PlanAssuranceResult] = []
        for member in members:
            if member.incident_id is None:
                continue
            plan = (
                (
                    await self._session.execute(
                        select(Plan)
                        .where(
                            Plan.incident_id == member.incident_id,
                            Plan.selection_state == plan_lifecycle.SELECTION_SELECTED,
                        )
                        .order_by(Plan.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if plan is None:
                continue
            results.append(
                await plan_lifecycle.assure_plan(
                    self._session,
                    plan_id=int(plan.id),
                    rollup=active,
                    hotel_payload=hotel_payload,
                    loaded=loaded,
                )
            )
        return results

    # ------------------------------------------------------------------ journal

    async def _primary_incident_id(self, group_id: int) -> int | None:
        """The incident on the group's primary flight, for attributing group-level entries.

        Falls back to the lowest member incident id so a group whose primary flight has no
        incident still journals somewhere findable.
        """
        members = await self.members(group_id)
        primary = next((m for m in members if m.is_primary and m.incident_id is not None), None)
        if primary is not None:
            return int(primary.incident_id or 0) or None
        opened = sorted(int(m.incident_id) for m in members if m.incident_id is not None)
        return opened[0] if opened else None

    async def _journal_group(
        self,
        group_id: int,
        *,
        event_type: str,
        stage: str,
        summary: str,
        detail: dict[str, Any],
    ) -> None:
        from app.models.workflow import DecisionLog

        incident_id = await self._primary_incident_id(group_id)
        group = await self._group(group_id)
        self._session.add(
            DecisionLog(
                incident_id=incident_id,
                occurred_at=self._now(),
                stage=stage,
                actor=ACTOR_ORCHESTRATOR,
                event_type=event_type,
                summary=summary,
                detail={
                    **detail,
                    # The group scope lives in the detail because `decision_log` has one scope
                    # column. A NULL incident would make the entry invisible in every timeline.
                    "incident_group_id": group_id,
                    "group_reference": group.reference,
                },
                correlation_id=self._correlation_id or correlation_id_var.get(),
            )
        )
        await self._session.flush()
        log.info(
            "group_event",
            event_type=event_type,
            incident_group_id=group_id,
            group_reference=group.reference,
            summary=summary,
        )
