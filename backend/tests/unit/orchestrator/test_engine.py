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
from app.orchestrator.playbook import FALLBACK_GENERATOR, playbook_for
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
            # Allocation is its own step, behind the search. The search commits nothing; this one
            # takes rooms off the market, so it is a separate decision with its own evidence and
            # its own place on the timeline. Without it the hotel service is registered but never
            # planned, so no room is ever held and the cascade shows no accommodation edge.
            "reserve_hotel_block",
            "assess_crew_impact",
            "notify_passengers",
            "evaluate_entitlements",
        ]
        # Dependencies are resolved to persisted task IDs, looked up by action rather than by
        # position so inserting a step does not silently re-point an assertion.
        notify = next(row for row in rows if row.action_type == "notify_passengers")
        search = next(row for row in rows if row.action_type == "find_hotel_options")
        reserve = next(row for row in rows if row.action_type == "reserve_hotel_block")
        assert notify.depends_on == [str(rows[0].id)]
        assert reserve.depends_on == [str(search.id)]

    async def test_the_plan_narrows_to_actions_with_a_registered_service(
        self, session, flight, settings
    ):
        """A plan proposing work nothing can do stops dead and overstates the system."""
        from app.orchestrator import dispatch
        from app.services.base import ServiceResult

        async def ok(**_kwargs):
            return ServiceResult(status=ActionStatus.success, reason="done")

        dispatch.register(ActionType.check_connections, ok)
        dispatch.register(ActionType.assess_crew_impact, ok)

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        rows = (
            (await session.execute(select(PlanTaskRow).order_by(PlanTaskRow.task_order)))
            .scalars()
            .all()
        )
        assert [r.action_type for r in rows] == ["check_connections", "assess_crew_impact"]

    async def test_a_deferred_action_is_named_in_the_record(self, session, flight, settings):
        from app.orchestrator import dispatch
        from app.services.base import ServiceResult

        async def ok(**_kwargs):
            return ServiceResult(status=ActionStatus.success, reason="done")

        dispatch.register(ActionType.check_connections, ok)

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        plan = (await session.execute(select(Plan))).scalars().one()
        assert "find_hotel_options" in plan.rationale
        assert "no deterministic service is available" in plan.rationale

        stmt = select(DecisionLog).where(DecisionLog.event_type == "PLAN_PROPOSED")
        proposed = (await session.execute(stmt)).scalars().one()
        assert "find_hotel_options" in proposed.detail["deferred_actions"]

    async def test_a_dependency_on_a_deferred_action_is_dropped_not_left_dangling(
        self, session, flight, settings
    ):
        """notify_passengers depends on check_connections; if that is deferred the edge goes.

        A dependency naming a task that was never created can never be satisfied, so the
        plan would deadlock on its own edge rather than on anything real.
        """
        from app.orchestrator import dispatch
        from app.services.base import ServiceResult

        async def ok(**_kwargs):
            return ServiceResult(status=ActionStatus.success, reason="done")

        dispatch.register(ActionType.notify_passengers, ok)

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        rows = (await session.execute(select(PlanTaskRow))).scalars().all()
        assert [r.action_type for r in rows] == ["notify_passengers"]
        assert rows[0].depends_on == []

    async def test_an_empty_registry_keeps_the_whole_playbook(self, session, flight, settings):
        """An empty plan would let an incident resolve without doing anything at all.

        That is a worse failure than a visible refusal, so with nothing registered the full
        playbook is proposed and the run blocks honestly at the first dispatch.
        """
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            await engine.advance(ctx)

        rows = (await session.execute(select(PlanTaskRow))).scalars().all()
        # Read from the playbook rather than restated: a literal here made every playbook change
        # look like a planning bug.
        assert len(rows) == len(playbook_for("weather"))

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
        # No weather observation is seeded in this fixture, so the risk assessment records
        # that it could not run rather than defaulting to a number nobody measured.
        assert [e.event_type for e in entries] == [
            "INCIDENT_OPENED",
            "STATE_CHANGED",
            "DELAY_RISK_UNAVAILABLE",
            "STATE_CHANGED",
            "PLAN_PROPOSED",
            "STATE_CHANGED",
        ]
        assert [e.id for e in entries] == sorted(e.id for e in entries)

    async def test_absent_weather_leaves_the_risk_absent(self, session, flight, settings):
        """No observation means no Prediction, not a zero-risk one."""
        from app.models.workflow import Prediction

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.advance(ctx)
        await engine.advance(ctx)

        assert await _count(session, Prediction) == 0
        incident = await session.get(Incident, ctx.incident_id)
        assert incident.prediction_id is None

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
    async def test_a_missing_gate_entry_point_blocks_execution(
        self, session, flight, settings, no_gate
    ):
        """The fail-closed guarantee, provoked deliberately now that B's gate exists."""
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert await _count(session, Action) == 0

    async def test_an_unavailable_gate_is_never_executable(
        self, session, flight, settings, no_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        result = await engine.assure(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )
        assert result.executable is False
        assert result.decision is AssuranceDecision.needs_human
        assert len(result.blocking) == 6

    async def test_a_stubbed_out_gate_is_never_executable(
        self, session, flight, settings, monkeypatch
    ):
        """A gate that still raises NotImplementedError must refuse, not crash the run."""
        from app.assurance import gate

        def not_yet(**_kwargs):
            raise NotImplementedError("Stream B")

        monkeypatch.setattr(gate, "evaluate", not_yet, raising=False)
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert await _count(session, Action) == 0

    async def test_a_gate_returning_the_wrong_type_is_never_executable(
        self, session, flight, settings, monkeypatch
    ):
        from app.assurance import gate

        monkeypatch.setattr(gate, "evaluate", lambda **_k: {"decision": "execute"}, raising=False)
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert await _count(session, Action) == 0

    async def test_a_refusal_is_labelled_as_one_not_as_a_real_evaluation(
        self, session, flight, settings, no_gate
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
        inputs = calls[0]["inputs"]
        assert inputs.action_type == "check_connections"
        assert "confidence" not in inputs.model_dump()
        # The config hash the gate is given is the one recorded on the evaluation.
        assert calls[0]["config_hash"]
        # `now` is passed explicitly so a replay is reproducible.
        assert calls[0]["now"] == FIXED_NOW

    async def test_the_real_gate_authorises_low_risk_and_holds_high_risk(
        self, session, flight, settings
    ):
        """No stub. Stream B's gate against the committed config/assurance.v1.yaml.

        The config tiers `check_connections` low and `notify_passengers` high, and
        `high_risk_requires_human` is true. The recorded decisions must reflect that, which
        is also the story the committed incident_detail fixture tells.
        """
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        low = await engine.assure(
            ctx,
            PlanTask(
                action=ActionType.check_connections,
                target_refs=[f"incident:{ctx.incident_reference}", f"flight:{flight.id}"],
            ),
        )
        high = await engine.assure(
            ctx,
            PlanTask(
                action=ActionType.notify_passengers,
                target_refs=[f"incident:{ctx.incident_reference}", f"flight:{flight.id}"],
            ),
        )

        assert low.executable is True
        assert low.risk_tier.value == "low"
        assert high.executable is False
        assert high.decision is AssuranceDecision.needs_human
        assert high.risk_tier.value == "high"

    async def test_an_unresolvable_entity_is_refused_by_the_real_gate(
        self, session, flight, settings
    ):
        """`entities_valid` only bites if the orchestrator gathers real resolutions.

        A reference the database cannot resolve is omitted from `resolved_entities` rather
        than recorded as empty, which is what lets the check fail a hallucinated entity.
        """
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        result = await engine.assure(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:999999"])
        )
        assert result.executable is False

    async def test_gathered_inputs_include_prior_actions_for_conflict_detection(
        self, session, flight, settings, working_gate
    ):
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        from app.agents.contract import PlanTask

        gathered, _requirements = await engine._gate_inputs(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )
        assert gathered["pending_or_executed"], "the gate must be able to see prior actions"
        assert gathered["pending_or_executed"][0]["action_type"] == "check_connections"

    async def test_freshness_is_never_manufactured(self, session, flight, settings):
        """No weather row means no source, not a source stamped `now`."""
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        gathered, _requirements = await engine._gate_inputs(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )
        assert gathered["sources"] == {}

    async def test_the_source_offered_to_the_gate_is_bounded_and_not_a_forecast(
        self, session, flight, settings
    ):
        """Re-homed from a Stream C contract test that was deleted when its file was replaced.

        Their tripwire asserted this on `Orchestrator._source_timestamps` directly, because
        two things in this schema are easy to hand a freshness check by accident:

          * The archive holds real observations on their own later timestamps, so the plain
            newest row is dated *after* the moment being assessed. The gate FAILs a
            future-dated source, correctly — a broken feed must not read as maximally fresh.
          * `weather_observation` also holds TAF rows, and a forecast is not an observation.

        Both are seeded here so the query has something to get wrong, and the assertion is
        that it picks the bounded actual report rather than either trap.
        """
        from datetime import timedelta

        from app.agents.contract import PlanTask
        from app.models.enums import ProvenanceKind
        from app.models.reference import WeatherObservation

        def observation(*, observed_at, is_forecast, visibility):
            return WeatherObservation(
                airport_icao="VOBL",
                observed_at=observed_at,
                wind_speed_kt=24,
                visibility_m=visibility,
                is_forecast=is_forecast,
                provenance_kind=ProvenanceKind.fixture,
                provenance_provider="fixture",
                source_ref=f"fixture:test:{observed_at.isoformat()}:{is_forecast}",
            )

        in_force = FIXED_NOW - timedelta(minutes=6)
        session.add_all(
            [
                observation(observed_at=in_force, is_forecast=False, visibility=800),
                # Dated after the incident: a later archive row.
                observation(
                    observed_at=FIXED_NOW + timedelta(hours=18),
                    is_forecast=False,
                    visibility=8000,
                ),
                # A forecast, at or before the clock, which must still not be chosen.
                observation(
                    observed_at=FIXED_NOW - timedelta(minutes=1),
                    is_forecast=True,
                    visibility=9999,
                ),
            ]
        )
        await session.commit()

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        gathered, _requirements = await engine._gate_inputs(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )

        assert gathered["sources"] == {"metar:VOBL": in_force}

    async def test_a_recorded_observation_is_offered_to_the_freshness_check(
        self, session, flight, settings
    ):
        from app.models.enums import ProvenanceKind
        from app.models.reference import WeatherObservation

        session.add(
            WeatherObservation(
                airport_icao="VOBL",
                observed_at=FIXED_NOW,
                wind_speed_kt=24,
                visibility_m=800,
                ceiling_ft=900,
                provenance_kind=ProvenanceKind.fixture,
                provenance_provider="fixture",
                source_ref="fixture:bengaluru_storm:weather:VOBL",
            )
        )
        await session.commit()

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        from app.agents.contract import PlanTask

        gathered, _requirements = await engine._gate_inputs(
            ctx, PlanTask(action=ActionType.check_connections, target_refs=["flight:1"])
        )
        assert gathered["sources"] == {"metar:VOBL": FIXED_NOW}

    async def test_policy_requirements_come_from_stream_b(
        self, session, flight, settings, working_gate
    ):
        """The tripwire this test used to be has fired, and the wiring is done.

        `required_facts` and `constraints` are asked for, never assembled here. For an action
        the pack has nothing to say about, Stream B answers `policy_bearing=False` with empty
        lists — an authoritative answer, where this stream previously had a guess.

        The record still names it, because two empty checks passing must read as "nothing
        applicable to check" rather than "policy verified".
        """
        from app.orchestrator.engine import POLICY_NOT_APPLICABLE

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        stmt = select(DecisionLog).where(DecisionLog.event_type == "ASSURANCE_EVALUATED")
        recorded = (await session.execute(stmt)).scalars().all()
        assert recorded

        for entry in recorded:
            coverage = entry.detail["gate_inputs"]
            assert coverage["policy_bearing"] is False
            assert coverage["notice"] == POLICY_NOT_APPLICABLE
            assert coverage["not_applicable_checks"] == ["evidence_complete", "policy_compliant"]
            assert coverage["policy_mode"] == "charter"

    async def test_a_policy_bearing_action_demands_real_facts(self, session, flight, settings):
        """`evaluate_entitlements` is the one action the pack speaks to.

        With no trip context supplied, Stream B's requirements fail closed, so the gate has
        something to refuse. An entitlement decided on facts nobody supplied would be an
        unreviewed legal claim, which is the outcome this prevents.
        """
        from app.agents.contract import PlanTask

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        gathered, requirements = await engine._gate_inputs(
            ctx,
            PlanTask(action=ActionType.evaluate_entitlements, target_refs=[f"flight:{flight.id}"]),
        )

        assert requirements.policy_bearing is True
        # Something real to check, rather than two vacuous passes.
        assert gathered["required_facts"] or gathered["constraints"]

    async def test_the_orchestrator_supplies_no_fact_it_was_not_given(
        self, session, flight, settings
    ):
        """A fact invented to satisfy `evidence_complete` defeats the check entirely."""
        from app.agents.contract import PlanTask

        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")

        gathered, _requirements = await engine._gate_inputs(
            ctx,
            PlanTask(action=ActionType.evaluate_entitlements, target_refs=[f"flight:{flight.id}"]),
        )
        assert gathered["provided_facts"] == {}

        # Only what a caller explicitly passed comes through.
        gathered, _requirements = await engine._gate_inputs(
            ctx,
            PlanTask(
                action=ActionType.evaluate_entitlements,
                target_refs=[f"flight:{flight.id}"],
                inputs={"facts": {"event": {"kind": "delay"}}},
            ),
        )
        assert gathered["provided_facts"] == {"event": {"kind": "delay"}}

    async def test_the_inputs_the_orchestrator_does_own_are_populated(
        self, session, flight, settings, working_gate
    ):
        """The other four checks must not be vacuous too."""
        engine = build(session, settings=settings)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        stmt = select(DecisionLog).where(DecisionLog.event_type == "ASSURANCE_EVALUATED")
        first = (await session.execute(stmt)).scalars().first()
        coverage = first.detail["gate_inputs"]

        assert coverage["resolved_entities"] >= 1, "entities_valid had nothing to validate"

    async def test_the_orchestrator_computes_no_check_outcome_itself(self):
        """Stream A gathers facts; Stream B judges them. Assert the boundary in code.

        The engine must not import a check function or the aggregator. If it did, the safety
        boundary would exist in two places and they would drift.
        """
        import ast
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[3] / "app" / "orchestrator"
        forbidden = {
            "aggregate",
            "evidence_complete",
            "sources_fresh",
            "entities_valid",
            "policy_compliant",
            "no_conflicts",
            "action_risk",
        }
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    ("app.assurance.checks", "app.policy")
                ):
                    names = {alias.name for alias in node.names}
                    assert not names & forbidden, (
                        f"{path.name} imports {names & forbidden}: assurance logic must stay "
                        "in Stream B"
                    )

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
            assert await _count(session, Action) == len(playbook_for("weather"))
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
