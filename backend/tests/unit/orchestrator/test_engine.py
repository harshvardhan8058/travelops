"""Orchestrator engine tests.

Ordered to match the slice: open_incident, advance, state-machine enforcement, limits,
idempotency, assurance invocation, execution dispatch boundary.

The gate is stubbed by monkeypatching, never by editing `app/assurance/`. Where a test
needs a service, it registers one into `dispatch.SERVICE_REGISTRY` and removes it after,
never by editing `app/services/`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.errors import AssuranceBlocked, EntityNotFound, InvalidStateTransition
from app.events.inmemory import in_memory_bus
from app.events.types import EventType
from app.models.enums import (
    ActionStatus,
    ActionType,
    AssuranceDecision,
    HumanDecisionType,
    IncidentState,
    TaskState,
)
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    DecisionLog,
    HumanDecision,
    Incident,
    Plan,
)
from app.models.workflow import PlanTask as PlanTaskRow
from app.orchestrator import dispatch
from app.orchestrator.engine import Orchestrator
from app.orchestrator.limits import Limits
from app.orchestrator.playbook import FALLBACK_GENERATOR
from app.orchestrator.state import TRANSITIONS
from app.services.base import ServiceResult

from .conftest import FIXED_NOW, make_modes, needs_human_result, passing_result


def build(session, *, modes=None, settings=None, bus=None, limits=None) -> Orchestrator:
    return Orchestrator(
        session,
        bus=bus,
        settings=settings,
        modes=modes or make_modes(),
        limits=limits,
        now=lambda: FIXED_NOW,
    )


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _log_summaries(session) -> list[str]:
    stmt = select(DecisionLog).order_by(DecisionLog.id)
    return [f"{e.stage}:{e.event_type}" for e in (await session.execute(stmt)).scalars()]


@pytest.fixture
def working_gate(stub_gate):
    """A gate that authorises everything. Lets the run reach the dispatch boundary."""
    return stub_gate(passing_result())


# --------------------------------------------------------------------- 1. open_incident


class TestOpenIncident:
    async def test_opens_one_incident(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        assert ctx.state is IncidentState.detected
        assert ctx.flight_id == flight.id
        assert await _count(session, Incident) == 1

    async def test_reference_matches_the_committed_fixture_format(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        assert ctx.incident_reference == "INC-2026-0820-VOBL-01"

    async def test_injecting_twice_does_not_open_a_second_incident(self, session, flight, settings):
        """The Stage 2 readiness gate: injecting bengaluru_storm creates exactly one."""
        engine = build(session, settings=settings)
        first = await engine.open_incident(flight.id, "weather")
        second = await engine.open_incident(flight.id, "weather")

        assert second.incident_id == first.incident_id
        assert second.incident_reference == first.incident_reference
        assert await _count(session, Incident) == 1

    async def test_the_suppression_is_recorded_not_silent(self, session, flight, settings):
        engine = build(session, settings=settings)
        await engine.open_incident(flight.id, "weather")
        await engine.open_incident(flight.id, "weather")

        assert "detect:INCIDENT_OPEN_SUPPRESSED" in await _log_summaries(session)

    async def test_the_database_index_catches_the_race(self, session, flight, settings):
        """Deduplication must not depend only on the pre-check query.

        Two pollers can pass the query in the same instant. The partial unique index is
        what actually prevents the duplicate, so it is asserted directly here: inserting a
        second active incident for one flight must be a database error.
        """
        from sqlalchemy.exc import IntegrityError

        engine = build(session, settings=settings)
        await engine.open_incident(flight.id, "weather")

        session.add(
            Incident(
                reference="INC-2026-0820-VOBL-99",
                flight_id=flight.id,
                trigger_type="weather",
                severity="high",
                state=IncidentState.detected,
                opened_at=FIXED_NOW,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_terminal_incident_releases_the_flight(self, session, flight, settings):
        """`blocked` and `resolved` are terminal, so a new incident may be opened."""
        engine = build(session, settings=settings)
        first = await engine.open_incident(flight.id, "weather")

        incident = await session.get(Incident, first.incident_id)
        incident.state = IncidentState.resolved
        await session.commit()

        second = await engine.open_incident(flight.id, "weather")
        assert second.incident_id != first.incident_id
        assert second.incident_reference == "INC-2026-0820-VOBL-02"

    async def test_unknown_flight_is_rejected(self, session, settings):
        engine = build(session, settings=settings)
        with pytest.raises(EntityNotFound):
            await engine.open_incident(4242, "weather")

    async def test_unknown_trigger_type_is_rejected(self, session, flight, settings):
        engine = build(session, settings=settings)
        with pytest.raises(EntityNotFound):
            await engine.open_incident(flight.id, "alien_invasion")

    async def test_incident_opened_event_is_published(self, session, flight, settings):
        bus, _client = in_memory_bus()
        published: list[object] = []
        bus.subscribe(None, _append(published))
        engine = build(session, settings=settings, bus=bus)

        ctx = await engine.open_incident(flight.id, "weather")
        await bus.consume_once(group="test", consumer="c", block_ms=0)

        assert [e.event_type for e in published] == [EventType.incident_opened]
        assert published[0].incident_reference == ctx.incident_reference
        assert published[0].correlation_id == ctx.correlation_id

    async def test_correlation_id_is_on_every_log_entry(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        entries = (await session.execute(select(DecisionLog))).scalars().all()
        assert entries
        assert {e.correlation_id for e in entries} == {ctx.correlation_id}


# ---------------------------------------------------------------------------- 2. advance


class TestAdvance:
    async def test_walks_detected_to_assuring(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        await engine.advance(ctx)
        assert ctx.state is IncidentState.assessing
        await engine.advance(ctx)
        assert ctx.state is IncidentState.planning
        await engine.advance(ctx)
        assert ctx.state is IncidentState.assuring

    async def test_planning_persists_the_deterministic_plan(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        plan = (await session.execute(select(Plan))).scalars().one()
        assert plan.generator == FALLBACK_GENERATOR
        assert plan.prompt_version is None
        assert plan.model_self_report is None
        assert plan.rationale

    async def test_the_api_can_always_state_which_generator_produced_the_plan(
        self, session, flight, settings
    ):
        """A judge must never have to guess whether a model was involved."""
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        plan = (await session.execute(select(Plan))).scalars().one()
        assert plan.generator == "fallback-playbook"
        assert plan.prompt_version is None

    async def test_plan_matches_the_committed_fixture_shape(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        rows = (
            (await session.execute(select(PlanTaskRow).order_by(PlanTaskRow.task_order)))
            .scalars()
            .all()
        )
        assert [r.action_type for r in rows] == [
            "check_connections",
            "find_hotel_options",
            "assess_crew_impact",
            "notify_passengers",
            "evaluate_entitlements",
        ]
        # notify_passengers depends on check_connections, stored as a resolved task ID.
        assert rows[3].depends_on == [str(rows[0].id)]

    async def test_a_plan_is_not_regenerated_on_re_entry(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        ctx.state = IncidentState.planning
        await engine.advance(ctx)
        assert await _count(session, Plan) == 1

    async def test_every_step_appends_an_ordered_record(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        entries = (
            (await session.execute(select(DecisionLog).order_by(DecisionLog.id))).scalars().all()
        )
        assert [e.event_type for e in entries] == [
            "INCIDENT_OPENED",
            "STATE_CHANGED",
            "STATE_CHANGED",
            "PLAN_PROPOSED",
            "STATE_CHANGED",
        ]
        assert [e.id for e in entries] == sorted(e.id for e in entries)

    async def test_a_terminal_incident_does_not_advance(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        incident = await session.get(Incident, ctx.incident_id)
        incident.state = IncidentState.resolved
        await session.commit()
        ctx.state = IncidentState.resolved

        before = ctx.steps_taken
        await engine.advance(ctx)
        assert ctx.steps_taken == before
        assert "terminal" in (ctx.last_note or "")


# ----------------------------------------------------------------- 3. the state machine


class TestStateMachineEnforcement:
    async def test_illegal_transition_raises_409(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        with pytest.raises(InvalidStateTransition) as caught:
            await engine._transition(
                ctx, IncidentState.executing, stage="execute", summary="illegal"
            )

        assert caught.value.status_code == 409
        assert caught.value.details["current_state"] == "detected"
        assert caught.value.details["requested_state"] == "executing"
        assert "assessing" in caught.value.details["allowed"]

    async def test_a_refused_transition_does_not_change_stored_state(
        self, session, flight, settings
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        with pytest.raises(InvalidStateTransition):
            await engine._transition(
                ctx, IncidentState.resolved, stage="resolve", summary="illegal"
            )

        incident = await session.get(Incident, ctx.incident_id)
        assert IncidentState(incident.state) is IncidentState.detected
        assert ctx.state is IncidentState.detected

    async def test_executing_is_only_entered_from_assuring_or_awaiting_approval(self):
        """Mirrors the frozen guard in tests/unit/test_state_machine.py."""
        sources = {s for s, targets in TRANSITIONS.items() if IncidentState.executing in targets}
        assert sources == {IncidentState.assuring, IncidentState.awaiting_approval}

    async def test_the_engine_only_moves_through_legal_transitions(
        self, session, flight, settings, working_gate
    ):
        """Replay the recorded state changes and assert every hop was legal."""
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        stmt = (
            select(DecisionLog)
            .where(DecisionLog.event_type == "STATE_CHANGED")
            .order_by(DecisionLog.id)
        )
        for entry in (await session.execute(stmt)).scalars():
            source = IncidentState(entry.detail["from"])
            target = IncidentState(entry.detail["to"])
            assert target in TRANSITIONS[source], f"illegal hop recorded: {source} -> {target}"

    async def test_state_is_recorded_on_the_incident_row_not_just_the_context(
        self, session, flight, settings
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.advance(ctx)

        incident = await session.get(Incident, ctx.incident_id)
        assert IncidentState(incident.state) is IncidentState.assessing


# ------------------------------------------------------------------------- 4. the limits


class TestLimits:
    async def test_step_budget_breach_blocks_the_incident(self, session, flight, settings):
        engine = build(
            session,
            settings=settings,
            limits=Limits(max_workflow_steps=2, action_timeout_seconds=30),
        )
        ctx = await engine.open_incident(flight.id, "weather")

        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        incident = await session.get(Incident, ctx.incident_id)
        assert IncidentState(incident.state) is IncidentState.blocked

    async def test_the_breach_is_explained_in_the_record(self, session, flight, settings):
        engine = build(
            session,
            settings=settings,
            limits=Limits(max_workflow_steps=2, action_timeout_seconds=30),
        )
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        stmt = select(DecisionLog).where(DecisionLog.event_type == "STATE_CHANGED")
        blocked = [
            e
            for e in (await session.execute(stmt)).scalars()
            if e.detail.get("to") == IncidentState.blocked.value
        ]
        assert blocked
        assert "workflow steps" in blocked[-1].detail["reason"]
        assert blocked[-1].detail["max_workflow_steps"] == 2

    async def test_a_breach_never_loops_forever(self, session, flight, settings):
        engine = build(
            session,
            settings=settings,
            limits=Limits(max_workflow_steps=1, action_timeout_seconds=30),
        )
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        assert ctx.state is IncidentState.blocked

    async def test_limits_come_from_config_not_a_literal(self, settings):
        limits = Limits.from_settings(settings)
        assert limits.max_workflow_steps == settings.max_workflow_steps
        assert limits.action_timeout_seconds == settings.action_timeout_seconds


# -------------------------------------------------------------------- 5. idempotency


class TestIdempotency:
    async def test_a_replayed_execute_returns_the_original_result(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx, task, evaluation = await _authorise_one_task(engine, session, flight)

        first = await engine.execute(
            ctx,
            task,
            passing_result(),
            evaluation_id=evaluation.id,
            plan_task_id=task_id(task, ctx),
        )
        second = await engine.execute(
            ctx,
            task,
            passing_result(),
            evaluation_id=evaluation.id,
            plan_task_id=task_id(task, ctx),
        )

        assert second.replayed is True
        assert first.replayed is False
        assert second.action_id == first.action_id
        assert second.result.reason == first.result.reason

    async def test_a_replay_does_not_write_a_second_action(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx, task, evaluation = await _authorise_one_task(engine, session, flight)
        key = engine._idempotency_key(ctx, task, task_id(task, ctx))

        await engine.execute(
            ctx,
            task,
            passing_result(),
            evaluation_id=evaluation.id,
            plan_task_id=task_id(task, ctx),
        )
        await engine.execute(
            ctx,
            task,
            passing_result(),
            evaluation_id=evaluation.id,
            plan_task_id=task_id(task, ctx),
        )

        rows = (
            (await session.execute(select(Action).where(Action.idempotency_key == key)))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_the_key_is_scoped_to_action_task_and_incident(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        a = PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        b = PlanTask(action=ActionType.find_hotel_options, target_refs=["flight:1"])

        assert engine._idempotency_key(ctx, a, 1) != engine._idempotency_key(ctx, b, 1)
        assert engine._idempotency_key(ctx, a, 1) != engine._idempotency_key(ctx, a, 2)
        assert engine._idempotency_key(ctx, a, 1) == engine._idempotency_key(ctx, a, 1)

    async def test_the_key_fits_the_column(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        task = PlanTask(
            action=ActionType.arrange_ground_transport,
            target_refs=[f"passenger:{n}" for n in range(50)],
        )
        assert len(engine._idempotency_key(ctx, task, 999)) <= 128

    async def test_rerunning_a_settled_incident_changes_nothing(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        actions = await _count(session, Action)
        logs = await _count(session, DecisionLog)

        await engine.run(ctx)
        assert await _count(session, Action) == actions
        assert await _count(session, DecisionLog) == logs


# --------------------------------------------------------------------- 6. assurance


class TestAssuranceInvocation:
    async def test_no_gate_means_no_execution(self, session, flight, settings):
        """Stream B's gate is not implemented: the run must block, never proceed."""
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert await _count(session, Action) == 0

    async def test_an_unavailable_gate_is_never_executable(self, session, flight, settings):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        result = await engine.assure(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )
        assert result.executable is False
        assert result.decision is AssuranceDecision.needs_human
        assert len(result.blocking) == 6

    async def test_a_refusal_is_labelled_as_one_not_as_a_real_evaluation(
        self, session, flight, settings
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        result = await engine.assure(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )
        assert all(check.reason_code.value == "CONFIG_MISSING" for check in result.checks)
        assert result.config_version == "assurance-v1"

    async def test_missing_assurance_config_blocks_rather_than_degrades(
        self, session, flight, settings, stub_gate
    ):
        """Even with a working gate, absent config must not authorise anything."""
        stub_gate(passing_result())
        engine = build(session, settings=settings, modes=make_modes(assurance_present=False))
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert await _count(session, Action) == 0

    async def test_a_gate_that_says_needs_human_awaits_approval(
        self, session, flight, settings, stub_gate
    ):
        stub_gate(needs_human_result())
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.awaiting_approval
        assert await _count(session, Action) == 0

    async def test_an_unavailable_gate_blocks_instead_of_awaiting_approval(
        self, session, flight, settings
    ):
        """Nobody may approve past an authorisation boundary that did not run.

        A working gate asking for a human is an approval request. A gate that could not
        evaluate is not — treating them the same would let a broken boundary look routine.
        """
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert ctx.state is not IncidentState.awaiting_approval

    async def test_the_evaluation_is_persisted_immutably(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        evaluations = (await session.execute(select(AssuranceEvaluation))).scalars().all()
        assert evaluations
        first = evaluations[0]
        assert set(first.check_results) == {
            "evidence_complete",
            "sources_fresh",
            "entities_valid",
            "policy_compliant",
            "no_conflicts",
            "action_risk",
        }
        assert first.config_version == "assurance-v1"
        assert first.config_hash

    async def test_the_orchestrator_asks_the_gate_rather_than_deciding(
        self, session, flight, settings, stub_gate
    ):
        calls = stub_gate(passing_result())
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert calls, "the engine must consult the gate, not decide for itself"
        assert calls[0]["action_type"] == "check_connections"
        assert "confidence" not in calls[0]

    async def test_assurance_evaluated_event_carries_the_config_hash(
        self, session, flight, settings, stub_gate
    ):
        stub_gate(passing_result())
        bus, _client = in_memory_bus()
        published: list[object] = []
        bus.subscribe(EventType.assurance_evaluated, _append(published))
        engine = build(session, settings=settings, bus=bus)

        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        await bus.consume_once(group="test", consumer="c", count=50, block_ms=0)

        assert published
        assert published[0].config_hash == "9f2c4b71d3e85a06"
        assert published[0].config_version == "assurance-v1"


# ------------------------------------------------------------- 7. the dispatch boundary


class TestExecutionBoundary:
    async def test_execute_refuses_an_unauthorised_task(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx, task, evaluation = await _authorise_one_task(engine, session, flight)

        with pytest.raises(AssuranceBlocked):
            await engine.execute(
                ctx,
                task,
                needs_human_result(),
                evaluation_id=evaluation.id,
                plan_task_id=task_id(task, ctx),
            )
        assert await _count(session, Action) == 0

    async def test_needs_human_requires_an_approved_decision(
        self, session, flight, settings, stub_gate
    ):
        stub_gate(needs_human_result())
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        assert ctx.state is IncidentState.awaiting_approval

        evaluation = (await session.execute(select(AssuranceEvaluation))).scalars().first()
        row = await session.get(PlanTaskRow, evaluation.plan_task_id)
        from app.agents.contract import PlanTask

        task = PlanTask(action=ActionType(row.action_type), target_refs=list(row.target_refs))

        with pytest.raises(AssuranceBlocked) as caught:
            await engine.execute(
                ctx,
                task,
                needs_human_result(),
                evaluation_id=evaluation.id,
                plan_task_id=row.id,
            )
        assert caught.value.details["assurance_id"] == evaluation.id
        assert await _count(session, Action) == 0

    async def test_a_rejected_decision_cannot_be_reused(self, session, flight, settings, stub_gate):
        stub_gate(needs_human_result())
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        evaluation = (await session.execute(select(AssuranceEvaluation))).scalars().first()
        session.add(
            HumanDecision(
                assurance_id=evaluation.id,
                decision=HumanDecisionType.rejected,
                actor_id="operator-1",
                reason="not appropriate for this disruption",
                decided_at=FIXED_NOW,
            )
        )
        await session.commit()

        row = await session.get(PlanTaskRow, evaluation.plan_task_id)
        from app.agents.contract import PlanTask

        task = PlanTask(action=ActionType(row.action_type), target_refs=list(row.target_refs))
        with pytest.raises(AssuranceBlocked) as caught:
            await engine.execute(
                ctx, task, needs_human_result(), evaluation_id=evaluation.id, plan_task_id=row.id
            )
        assert "rejected" in str(caught.value)

    async def test_an_approved_decision_authorises_execution(
        self, session, flight, settings, stub_gate
    ):
        stub_gate(needs_human_result())
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        assert ctx.state is IncidentState.awaiting_approval

        evaluation = (await session.execute(select(AssuranceEvaluation))).scalars().first()
        session.add(
            HumanDecision(
                assurance_id=evaluation.id,
                decision=HumanDecisionType.approved,
                actor_id="operator-1",
                reason="verified against the ops board",
                decided_at=FIXED_NOW,
            )
        )
        await session.commit()

        await engine.advance(ctx)
        assert ctx.state is IncidentState.executing

        await engine.advance(ctx)
        action = (await session.execute(select(Action))).scalars().first()
        assert action is not None
        assert action.human_decision_id is not None
        assert action.assurance_id == evaluation.id

    async def test_no_action_row_exists_without_an_assurance_evaluation(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        for action in (await session.execute(select(Action))).scalars():
            assert action.assurance_id is not None
            assert await session.get(AssuranceEvaluation, action.assurance_id) is not None

    async def test_a_missing_service_is_refused_not_faked(
        self, session, flight, settings, working_gate
    ):
        """The load-bearing honesty test.

        Stream C's services do not exist yet. The recorded outcome must say so, rather
        than reporting a success that never happened.
        """
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        action = (await session.execute(select(Action))).scalars().one()
        assert action.status == ActionStatus.needs_human
        assert "SERVICE_NOT_IMPLEMENTED" in action.reason
        assert action.payload["owning_stream"] == "C"
        # Nothing happened, so nothing may be claimed about provenance.
        assert action.provenance_kind == "unavailable"

    async def test_a_refused_dispatch_blocks_the_incident(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert "SERVICE_NOT_IMPLEMENTED" in (ctx.last_note or "")

    async def test_the_refusal_never_reports_success(self, session, flight, settings):
        result = dispatch.refusal(ActionType.check_connections)
        assert result.status is not ActionStatus.success
        assert result.status is ActionStatus.needs_human
        assert result.provenance_kind == "unavailable"

    async def test_a_registered_service_is_dispatched_and_completes_the_run(
        self, session, flight, settings, working_gate
    ):
        """Proof the boundary is a swap, not a rewrite.

        Registering services is exactly what Stream C's work will do. The engine's public
        contract does not change, and the same run reaches `resolved`.
        """
        actions = list(ActionType)
        try:
            for action in actions:
                dispatch.register(action, _fake_service(action))

            engine = build(session, settings=settings)
            ctx = await engine.open_incident(flight.id, "weather")
            await engine.run(ctx)

            assert ctx.state is IncidentState.resolved
            rows = (await session.execute(select(PlanTaskRow))).scalars().all()
            assert all(TaskState(r.state) is TaskState.succeeded for r in rows)
            assert await _count(session, Action) == 5
        finally:
            dispatch.SERVICE_REGISTRY.clear()

    async def test_a_failing_service_blocks_and_is_recorded(
        self, session, flight, settings, working_gate
    ):
        async def broken(**_kwargs):
            return ServiceResult(status=ActionStatus.failure, reason="provider timed out")

        try:
            dispatch.register(ActionType.check_connections, broken)
            engine = build(session, settings=settings)
            ctx = await engine.open_incident(flight.id, "weather")
            await engine.run(ctx)

            assert ctx.state is IncidentState.blocked
            action = (await session.execute(select(Action))).scalars().one()
            assert action.status == ActionStatus.failure
        finally:
            dispatch.SERVICE_REGISTRY.clear()


# ------------------------------------------------------- the Stage 2 deterministic slice


class TestBengaluruStormSlice:
    async def test_the_whole_path_runs_with_llm_mode_off(
        self, session, flight, settings, working_gate
    ):
        """open incident -> advance -> assurance -> transitions -> explicit outcome."""
        assert settings.llm_mode.value == "off"

        bus, _client = in_memory_bus()
        published: list[object] = []
        bus.subscribe(None, _append(published))
        engine = build(session, settings=settings, bus=bus)

        ctx = await engine.open_incident(
            flight.id, "weather", evidence_refs=["fixture:bengaluru_storm:weather:VOBL"]
        )
        await engine.run(ctx)
        await bus.consume_once(group="test", consumer="c", count=100, block_ms=0)

        # Exactly one incident.
        assert await _count(session, Incident) == 1
        # A terminal state, reached without a model.
        assert ctx.state is IncidentState.blocked
        plan = (await session.execute(select(Plan))).scalars().one()
        assert plan.generator == FALLBACK_GENERATOR
        # An ordered, immutable record per step.
        summaries = await _log_summaries(session)
        assert summaries[0] == "detect:INCIDENT_OPENED"
        assert "plan:PLAN_PROPOSED" in summaries
        assert "assure:ASSURANCE_EVALUATED" in summaries
        assert "execute:ACTION_COMPLETED" in summaries
        # Every action carries its authorisation.
        for action in (await session.execute(select(Action))).scalars():
            assert action.assurance_id is not None
        # And the bus saw the same story.
        assert EventType.incident_opened in {e.event_type for e in published}
        assert EventType.plan_proposed in {e.event_type for e in published}
        assert EventType.assurance_evaluated in {e.event_type for e in published}

    async def test_the_incident_closes_when_it_reaches_a_terminal_state(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        incident = await session.get(Incident, ctx.incident_id)
        assert incident.closed_at is not None

    async def test_a_bus_outage_does_not_lose_the_audit_trail(
        self, session, flight, settings, working_gate
    ):
        """The decision log is authoritative; the bus is a fan-out channel on top."""

        class BrokenBus:
            async def publish(self, event, **_kwargs):
                raise RuntimeError("redis is down")

        engine = build(session, settings=settings, bus=BrokenBus())
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        summaries = await _log_summaries(session)
        assert "detect:INCIDENT_OPENED" in summaries
        # The outage is recorded, not swallowed.
        assert any("EVENT_PUBLICATION_FAILED" in s for s in summaries)

    async def test_no_llm_is_reachable_from_the_engine(self):
        """The orchestrator is covered by the frozen AST guard; assert intent here too."""
        import ast
        import pathlib

        source = pathlib.Path(__file__).resolve().parents[3] / "app" / "orchestrator"
        for path in source.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert not name.startswith("app.llm"), f"{path.name} imports {name}"
                    assert name.split(".")[0] not in {
                        "groq",
                        "openai",
                        "anthropic",
                        "litellm",
                        "ollama",
                    }, f"{path.name} imports {name}"


# ------------------------------------------------------------------------------ helpers


def _append(sink: list):
    async def handler(event) -> None:
        sink.append(event)

    return handler


def _fake_service(action: ActionType):
    """A stand-in for a Stream C service. Only ever registered inside a test."""

    async def call(**kwargs) -> ServiceResult:
        return ServiceResult(
            status=ActionStatus.success,
            reason=f"{action.value} completed",
            payload={"action": action.value},
            evidence_refs=list(kwargs.get("evidence_refs", [])),
            provenance_kind="simulated",
        )

    return call


def task_id(task, ctx) -> int:
    """The plan task ID stashed on the context by the assuring step."""
    return int(ctx.metadata["current_plan_task_id"])


async def _authorise_one_task(engine: Orchestrator, session, flight):
    """Drive an incident to the point where exactly one task is authorised."""
    ctx = await engine.open_incident(flight.id, "weather")
    for _ in range(4):  # detected -> assessing -> planning -> assuring -> executing
        await engine.advance(ctx)
        if ctx.state is IncidentState.executing:
            break
    assert ctx.state is IncidentState.executing, f"expected executing, got {ctx.state}"

    row = await session.get(PlanTaskRow, int(ctx.metadata["current_plan_task_id"]))
    evaluation = await session.get(AssuranceEvaluation, int(ctx.metadata["current_assurance_id"]))
    from app.agents.contract import PlanTask

    task = PlanTask(
        action=ActionType(row.action_type),
        target_refs=list(row.target_refs),
        inputs=dict(row.inputs or {}),
    )
    return ctx, task, evaluation
