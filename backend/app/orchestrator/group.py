"""Disruption-group orchestration — the network event, not one flight.

The Phase 1 invariant survives intact: **an incident is one flight and is the unit of
authorisation.** This module opens and advances a *set* of incidents and derives the group's
state from them. It creates no second incident-creation path — every member goes through
`Orchestrator.open_incident`, so per-flight dedupe, the partial unique index, the incident
clock and `prediction_id` all behave exactly as they did in Phase 1.

Membership is **declared data**, read from `incident_group_flight` via Stream C's
`group_affected_flight_ids`. It is deliberately not derived from
`flight.origin_icao == group.airport_icao`: that returns seven flights for the storm, because
UK 705 *arrives* into VOBL, and those seven still yield nine pairings — so the headline number
survives while the `onward_duty` mechanism silently disappears. A wrong number gets caught in
review; a right number reached the wrong way does not.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import GroupFlight, group_affected_flights
from app.errors import EntityNotFound
from app.models.enums import IncidentState
from app.models.workflow import DecisionLog, Incident, IncidentGroup
from app.orchestrator.engine import ACTOR_ORCHESTRATOR, Orchestrator, WorkflowContext
from app.orchestrator.group_state import (
    GROUP_ORDER,
    GROUP_TERMINAL,
    assert_group_transition,
    derive_group_state,
    is_terminal,
    unresolved_members,
)
from app.services.cascade_graph import project_and_record
from app.services.passenger_impact import UNASSESSED_FACTORS

log = structlog.get_logger(__name__)

STAGE_GROUP = "cascade"


def _is_backwards(current: IncidentState, target: IncidentState) -> bool:
    """True when `target` is an earlier progress stage than `current`."""
    if current in GROUP_TERMINAL or target in GROUP_TERMINAL:
        return False
    if current not in GROUP_ORDER or target not in GROUP_ORDER:
        return False
    return GROUP_ORDER.index(target) < GROUP_ORDER.index(current)


@dataclass
class MemberOutcome:
    """One member incident's position after a group advance."""

    flight_id: int
    flight_number: str
    incident_id: int | None
    incident_reference: str | None
    state: IncidentState | None
    role: str
    note: str | None = None


@dataclass
class GroupContext:
    """Everything a caller needs to describe a group run without a second query."""

    group_id: int
    group_reference: str
    state: IncidentState
    correlation_id: str
    members: list[MemberOutcome] = field(default_factory=list)
    #: Set when the group is blocked, naming what is unresolved rather than just saying so.
    blocked_reason: str | None = None
    opened_incident_ids: list[int] = field(default_factory=list)

    @property
    def member_states(self) -> list[IncidentState]:
        return [m.state for m in self.members if m.state is not None]

    @property
    def unresolved(self) -> list[str]:
        return unresolved_members(
            {
                m.incident_reference or m.flight_number: m.state
                for m in self.members
                if m.state is not None
            }
        )


class GroupOrchestrator:
    """Opens and advances every incident in a disruption group.

    Deliberately thin. It owns *scope* and *group state*; everything about how one incident
    progresses stays in `Orchestrator`, which is why a member incident driven through the
    group is indistinguishable from one driven through `POST /incidents/{ref}/run`.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        orchestrator: Orchestrator | None = None,
        now: Any = None,
    ) -> None:
        self._session = session
        self._orchestrator = orchestrator or Orchestrator(session)
        self._now_fn = now or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return self._now_fn()

    # ------------------------------------------------------------------------- resolution

    async def resolve(self, reference_or_id: str | int) -> IncidentGroup:
        """Accept a reference or a numeric id, as `/incidents/{ref}` already does."""
        group: IncidentGroup | None = None
        if isinstance(reference_or_id, int) or str(reference_or_id).isdigit():
            group = await self._session.get(IncidentGroup, int(reference_or_id))
        if group is None:
            stmt = select(IncidentGroup).where(IncidentGroup.reference == str(reference_or_id))
            group = (await self._session.execute(stmt)).scalars().first()
        if group is None:
            raise EntityNotFound(
                "disruption group not found", details={"group": str(reference_or_id)}
            )
        return group

    # ------------------------------------------------------------------------------ open

    async def open_group(
        self,
        reference_or_id: str | int,
        *,
        flight_ids: Sequence[int] | None = None,
        correlation_id: str | None = None,
        opened_at: datetime | None = None,
    ) -> GroupContext:
        """Open one incident per declared member flight. Idempotent by construction.

        Re-running opens nothing new: `open_incident` returns the existing active incident for
        a flight, so a repeated inject cannot double the cascade.

        `opened_incident_ids` reports only the incidents this call actually created. It previously
        listed every member incident, so a second click told the operator it had opened eight
        incidents again — the cascade was intact but the report of it was false, which is worse than
        a visible error because nothing looks wrong.
        """
        group = await self.resolve(reference_or_id)
        members = await group_affected_flights(self._session, group_id=group.id)
        if flight_ids is not None:
            wanted = set(flight_ids)
            members = [m for m in members if m.flight_id in wanted]

        if not members:
            raise EntityNotFound(
                "this disruption group declares no member flights",
                details={
                    "group_reference": group.reference,
                    "resolution": (
                        "membership lives in incident_group_flight; seed the demo dataset or "
                        "declare members before opening the cascade"
                    ),
                },
            )

        correlation = correlation_id or f"group-{group.reference}"
        opened: list[int] = []
        outcomes: list[MemberOutcome] = []
        # Which flights already carried an incident before this call, so "opened" can mean opened.
        already_open = {member.flight_id for member in members if member.incident_id is not None}

        for member in members:
            ctx = await self._orchestrator.open_incident(
                member.flight_id,
                group.root_cause,
                severity=group.severity,
                group_id=group.id,
                correlation_id=correlation,
                demo_dataset_id=group.demo_dataset_id,
                opened_at=opened_at or group.opened_at,
            )
            if ctx.incident_id not in opened and member.flight_id not in already_open:
                opened.append(ctx.incident_id)
            outcomes.append(
                MemberOutcome(
                    flight_id=member.flight_id,
                    flight_number=member.flight_number,
                    incident_id=ctx.incident_id,
                    incident_reference=ctx.incident_reference,
                    state=ctx.state,
                    role=member.role,
                )
            )

        ctx_group = GroupContext(
            group_id=group.id,
            group_reference=group.reference,
            state=IncidentState(group.state),
            correlation_id=correlation,
            members=outcomes,
            opened_incident_ids=opened,
        )
        await self._sync_state(group, ctx_group, summary_verb="opened")
        log.info(
            "group_opened",
            group_reference=group.reference,
            members=len(outcomes),
            incidents=len(opened),
        )
        return ctx_group

    # --------------------------------------------------------------------------- advance

    async def advance_group(
        self,
        reference_or_id: str | int,
        *,
        max_incidents: int | None = None,
        max_iterations: int | None = None,
        correlation_id: str | None = None,
    ) -> GroupContext:
        """Advance every non-terminal member, then re-derive the group's state.

        A member whose service refuses does not stop the others: each is advanced
        independently and the group ends `blocked` naming what did not resolve. One flight
        without a hotel must not strand the other seven.

        `max_incidents` bounds how many members advance in one call, because eight incidents
        x three tasks x real services is materially slower than one and a demo pause has a
        budget. Members already terminal never consume the budget.
        """
        group = await self.resolve(reference_or_id)
        members = await group_affected_flights(self._session, group_id=group.id)
        correlation = correlation_id or f"group-{group.reference}"

        outcomes: list[MemberOutcome] = []
        advanced = 0
        for member in members:
            outcome = MemberOutcome(
                flight_id=member.flight_id,
                flight_number=member.flight_number,
                incident_id=member.incident_id,
                incident_reference=None,
                state=IncidentState(member.incident_state) if member.incident_state else None,
                role=member.role,
            )
            if member.incident_id is None:
                # The group declares the flight but no incident exists. A real gap: surfaced,
                # not smoothed into a pass.
                outcome.note = "declared in the group but no incident is open"
                outcomes.append(outcome)
                continue

            incident = await self._session.get(Incident, member.incident_id)
            if incident is None:
                outcome.note = "incident row missing"
                outcomes.append(outcome)
                continue

            outcome.incident_reference = incident.reference
            state = IncidentState(incident.state)
            if state in IncidentState.terminal():
                outcome.state = state
                outcomes.append(outcome)
                continue

            if max_incidents is not None and advanced >= max_incidents:
                outcome.state = state
                outcome.note = "not advanced in this call: per-request budget reached"
                outcomes.append(outcome)
                continue

            ctx = WorkflowContext(
                incident_id=incident.id,
                incident_reference=incident.reference,
                state=state,
                correlation_id=correlation,
                flight_id=incident.flight_id,
                trigger_type=incident.trigger_type,
            )
            # Each member gets its own step budget. A long member must not starve the rest.
            ctx = await self._orchestrator.run(ctx, max_iterations=max_iterations)
            advanced += 1
            outcome.state = ctx.state
            outcome.note = ctx.last_note
            outcomes.append(outcome)

        ctx_group = GroupContext(
            group_id=group.id,
            group_reference=group.reference,
            state=IncidentState(group.state),
            correlation_id=correlation,
            members=outcomes,
        )
        await self._record_cascade(group, ctx_group)
        await self._sync_state(group, ctx_group, summary_verb="advanced")
        log.info(
            "group_advanced",
            group_reference=group.reference,
            advanced=advanced,
            state=ctx_group.state.value,
        )
        return ctx_group

    async def _record_cascade(self, group: IncidentGroup, ctx: GroupContext) -> None:
        """Persist the projected edges and an append-only rollup snapshot.

        The graph is a projection and stays recomputable, so this is a record of what was true at
        a point in time rather than a cache to read from. Nothing is denormalised onto
        `incident_group`: a mutable rollup drifts from the rows it summarises and then nothing
        says which is right.

        A failure here must not fail the run. The durable state and the audit trail have already
        committed; losing a snapshot loses a convenience, and blocking recovery over it would be
        the wrong trade. It is logged, never swallowed silently.
        """
        try:
            graph, rollup, snapshot, edges = await project_and_record(
                self._session, group_id=group.id
            )
        except Exception as exc:
            log.error(
                "cascade_snapshot_failed",
                outcome="error",
                group_reference=group.reference,
                detail=type(exc).__name__,
                reason=str(exc),
            )
            await self._journal(
                ctx,
                event_type="CASCADE_SNAPSHOT_FAILED",
                summary=(
                    "The disruption graph could not be recorded. Recovery continued; the "
                    "decision log remains authoritative."
                ),
                detail={"error": type(exc).__name__, "reason": str(exc)},
            )
            return

        await self._journal(
            ctx,
            event_type="CASCADE_PROJECTED",
            summary=(
                f"Cascade projected: {len(graph.nodes)} nodes, {len(graph.edges)} edges "
                f"({edges} newly recorded), snapshot {snapshot.snapshot_hash[:12]}"
            ),
            detail={
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
                "edges_recorded": edges,
                "snapshot_hash": snapshot.snapshot_hash,
                "rule_version": graph.rule_version,
                "flights_affected": rollup.flights_affected,
                "passengers_affected": rollup.passengers_affected,
                "connections_at_risk": rollup.connections_at_risk,
                "crew_pairings_affected": rollup.crew_pairings_affected,
                "is_complete": rollup.is_complete,
            },
        )

        await self._record_impacts(group, ctx)

    async def _record_impacts(self, group: IncidentGroup, ctx: GroupContext) -> None:
        """Rank the group's passengers into `passenger_impact` from rows already recorded.

        This runs at group scope alongside the snapshot rather than as a per-flight plan step, and
        that placement is the point. A ranking over persisted rows has **no external effect**: it
        books nothing, reserves nothing and authorises nothing, which is the same reason delay risk
        is assessed without a gate. Making it a plan step would have meant either inventing an
        action the playbook does not declare, or overloading `rebook_passengers` — a name that
        promises a booking — with an assessment. Neither is honest.

        The inputs are the recorded findings: `connection_broken` comes from the persisted
        `check_connections` action, so the ranking and the connection count cannot disagree. Two of
        the ruleset's factors have no service establishing them yet; they stay false here and are
        named in `UNASSESSED_FACTORS`, so a surface can say "not established" instead of "no".

        Failure is contained exactly as the snapshot's is. The durable state and the audit trail
        have already committed, and losing a ranking loses a convenience.
        """
        from app.db.scenario_queries import load_business_constraints
        from app.orchestrator.service_registry import load_passenger_cohort_facts
        from app.services.passenger_impact import (
            assess_passenger_impact,
            load_ruleset,
            persist_passenger_impacts,
        )

        try:
            flights = await self.member_flights(group.id)
            flight_ids = {flight.flight_id for flight in flights}
            facts = await load_passenger_cohort_facts(self._session, flight_ids)
            if not facts:
                # Not an error and not a zero. No booking rows are in scope, so there is nobody to
                # rank; recording an empty assessment would present that as "everyone is fine".
                log.info(
                    "group_impacts_skipped",
                    group_reference=group.reference,
                    reason="no booking records are in scope",
                )
                return

            ruleset = load_ruleset(await load_business_constraints(self._session))
            assessment = assess_passenger_impact(cohort_facts=facts, ruleset=ruleset)
            written = await persist_passenger_impacts(
                self._session, incident_group_id=group.id, assessment=assessment
            )
        except Exception as exc:
            log.error(
                "group_impacts_failed",
                outcome="error",
                group_reference=group.reference,
                detail=type(exc).__name__,
                reason=str(exc),
            )
            await self._journal(
                ctx,
                event_type="PASSENGER_IMPACT_FAILED",
                summary=(
                    "Per-passenger priorities could not be recorded. Recovery continued; the "
                    "decision log remains authoritative."
                ),
                detail={"error": type(exc).__name__, "reason": str(exc)},
            )
            return

        await self._journal(
            ctx,
            event_type="PASSENGER_IMPACT_RECORDED",
            summary=(
                f"{written} passengers ranked into {len(assessment.cohorts)} cohorts under "
                f"ruleset {assessment.ruleset_version} ({assessment.ruleset_hash})"
            ),
            detail={
                "passengers_assessed": assessment.passengers_assessed,
                "rows_written": written,
                "count_by_band": assessment.count_by_band,
                "rule_version": assessment.rule_version,
                "ruleset_version": assessment.ruleset_version,
                "ruleset_hash": assessment.ruleset_hash,
                "unassessed_factors": [factor for factor, _, _ in UNASSESSED_FACTORS],
                "basis": "persisted_records",
                "authorises_no_action": True,
            },
        )

    # ------------------------------------------------------------------------ group state

    async def _sync_state(
        self, group: IncidentGroup, ctx: GroupContext, *, summary_verb: str
    ) -> None:
        """Move `incident_group.state` to the value derived from its members."""
        current = IncidentState(group.state)
        target = derive_group_state(ctx.member_states)
        regressed = False

        if target is not current and _is_backwards(current, target):
            # Derivation can legitimately point backwards — a member incident opened later starts
            # at `detected` and drags the aggregate down. A group must never *appear* to go
            # backwards, so the earlier value is kept and the fact is recorded rather than the
            # request being failed: refusing the whole run over a presentational regression would
            # be the wrong trade when the durable state is already correct.
            regressed = True
            target = current

        if target is not current:
            assert_group_transition(current, target, group_ref=group.reference)
            group.state = target
            if is_terminal(target):
                group.closed_at = self._now()
            await self._session.flush()

        ctx.state = target
        if target is IncidentState.blocked:
            unresolved = ctx.unresolved
            ctx.blocked_reason = (
                "every member incident has finished and "
                f"{len(unresolved)} did not resolve: {', '.join(unresolved)}"
            )

        await self._journal(
            ctx,
            event_type="GROUP_STATE_DERIVED" if target is not current else "GROUP_ASSESSED",
            summary=(
                f"Disruption group {summary_verb}: {len(ctx.members)} member flights, "
                f"state {target.value}"
            ),
            detail={
                "from": current.value,
                "to": target.value,
                "members": [
                    {
                        "flight_number": m.flight_number,
                        "incident_reference": m.incident_reference,
                        "state": m.state.value if m.state else None,
                        "role": m.role,
                        "note": m.note,
                    }
                    for m in ctx.members
                ],
                "unresolved": ctx.unresolved,
                "derived_from": "member incident states",
                "regressed": regressed,
            },
        )

    async def _journal(
        self,
        ctx: GroupContext,
        *,
        event_type: str,
        summary: str,
        detail: dict[str, Any] | None = None,
    ) -> DecisionLog:
        """Append a group-scoped entry to `decision_log`.

        `incident_id` is NULL, because a group entry does not belong to any one flight, and
        `correlation_id` carries the group reference so the entry is *findable* — there is no
        `group_id` column and adding one would need a Stream C migration for no gain. The
        timeline endpoint filters on `incident_id`, so these never leak into an incident's
        timeline; a test asserts it rather than trusting the reasoning.
        """
        entry = DecisionLog(
            incident_id=None,
            occurred_at=self._now(),
            stage=STAGE_GROUP,
            actor=ACTOR_ORCHESTRATOR,
            event_type=event_type,
            summary=summary,
            detail={"group_reference": ctx.group_reference, **(detail or {})},
            correlation_id=ctx.group_reference,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    # --------------------------------------------------------------------------- readers

    async def member_flights(self, group_id: int) -> list[GroupFlight]:
        return await group_affected_flights(self._session, group_id=group_id)

    async def awaiting_approval_count(self, group_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.group_id == group_id,
                Incident.state == IncidentState.awaiting_approval,
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def group_journal(self, group_reference: str) -> list[DecisionLog]:
        """Group-scoped log entries, in true chronological order."""
        stmt = (
            select(DecisionLog)
            .where(
                DecisionLog.incident_id.is_(None),
                DecisionLog.correlation_id == group_reference,
            )
            .order_by(DecisionLog.occurred_at, DecisionLog.id)
        )
        return list((await self._session.execute(stmt)).scalars())
