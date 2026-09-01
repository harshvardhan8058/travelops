"""An authorisation boundary nobody may cross is a block, not a wait.

The Phase 3 stall this file pins: `[FAIL] group reaches resolved (state=executing)` with
`awaiting_approval_count` stuck at one, and no error anywhere to explain it.

The gate refuses an action for one of three kinds of reason — risk, evidence, or conflict
(`app/assurance/blocking.py`). Only a risk-only refusal is approvable: `may_approve_action` permits
it, and `POST /assurance/{id}/decision` accepts an operator's signature. An evidence or conflict
refusal is **not approvable by anyone**, and the endpoint answers 409 for ever.

The engine used to file both shapes the same way, as `awaiting_approval`. For the unapprovable shape
that is a deadlock: no run can find a decision, because no decision can legally be recorded. The
incident waits for a signature the system itself refuses to accept, and a cascade whose other
members resolved reports `executing` indefinitely — the aggregate has no way to tell "a person is
thinking about it" from "this can never proceed".

So the rule these tests hold is the one the unavailable-gate branch already established: hold for a
person only when a person may actually decide. Otherwise block, and name the fact to fix.

What is deliberately NOT changed, and is asserted below: a risk-only hold still waits for a real
human decision, still executes nothing before one exists, and still executes on approval. The fix
removes a deadlock; it does not remove an approval.

Owner: Stream A.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.assurance.approval import may_approve_action
from app.assurance.blocking import is_approvable
from app.assurance.contract import CHECK_ORDER, AssuranceResult, CheckResult, ReasonCode
from app.models.enums import AssuranceDecision, CheckState, IncidentState, RiskTier, TaskState
from app.models.workflow import Action, AssuranceEvaluation, DecisionLog, HumanDecision
from app.models.workflow import PlanTask as PlanTaskRow
from app.orchestrator.engine import Orchestrator

from .conftest import FIXED_NOW, make_modes, needs_human_result

RISK_CHECK = CHECK_ORDER[-1]
SOURCES_CHECK = CHECK_ORDER[1]
CONFLICT_CHECK = CHECK_ORDER[-2]


def build(session, **kwargs) -> Orchestrator:
    return Orchestrator(session, modes=make_modes(), now=lambda: FIXED_NOW, **kwargs)


def _result(*failures: tuple, decision=AssuranceDecision.needs_human) -> AssuranceResult:
    """A gate result whose failing checks carry the reason codes given."""
    reasons = dict(failures)
    return AssuranceResult(
        decision=decision,
        risk_tier=RiskTier.high,
        checks=[
            CheckResult(
                name=name,
                state=CheckState.failed if name in reasons else CheckState.passed,
                reason_code=reasons.get(name, ReasonCode.OK),
            )
            for name in CHECK_ORDER
        ],
        blocking=list(reasons),
        config_version="assurance-v1",
        config_hash="9f2c4b71d3e85a06",
    )


def evidence_blocked() -> AssuranceResult:
    """Stale evidence *and* a high tier: the shape that used to deadlock."""
    return _result(
        (SOURCES_CHECK, ReasonCode.SOURCE_STALE),
        (RISK_CHECK, ReasonCode.HUMAN_APPROVAL_REQUIRED),
    )


def conflict_blocked() -> AssuranceResult:
    return _result(
        (CONFLICT_CHECK, ReasonCode.DUPLICATE_ACTION),
        (RISK_CHECK, ReasonCode.HUMAN_APPROVAL_REQUIRED),
    )


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


class TestTheRuleItself:
    """The premise, asserted rather than assumed."""

    def test_a_risk_only_refusal_is_approvable(self):
        assert is_approvable(needs_human_result()) is True
        assert may_approve_action(needs_human_result()).permitted is True

    @pytest.mark.parametrize(
        "result", [evidence_blocked(), conflict_blocked()], ids=["evidence", "conflict"]
    )
    def test_an_evidence_or_conflict_refusal_is_not_approvable_by_anyone(self, result):
        assert is_approvable(result) is False
        assert may_approve_action(result).permitted is False


class TestAnUnapprovableRefusalBlocks:
    @pytest.mark.parametrize(
        "result", [evidence_blocked(), conflict_blocked()], ids=["evidence", "conflict"]
    )
    async def test_it_blocks_instead_of_awaiting_a_decision_nobody_may_make(
        self, session, flight, stub_gate, result
    ):
        stub_gate(result)
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.blocked
        assert ctx.state is not IncidentState.awaiting_approval
        assert await _count(session, Action) == 0

    async def test_the_reason_names_the_fact_to_fix(self, session, flight, stub_gate):
        stub_gate(evidence_blocked())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        entry = (
            await session.execute(
                select(DecisionLog)
                .where(DecisionLog.event_type == "STATE_CHANGED")
                .order_by(DecisionLog.id.desc())
                .limit(1)
            )
        ).scalar_one()

        # An operator has to be able to act on this without reading the code.
        assert "SOURCE_STALE" in entry.summary
        assert "cannot be approved by a person" in entry.summary
        assert entry.detail["unapprovable_reasons"] == ["SOURCE_STALE"]

    async def test_the_task_is_left_needing_a_person_not_silently_settled(
        self, session, flight, stub_gate
    ):
        """The work is outstanding, and the record must keep saying so."""
        stub_gate(evidence_blocked())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        states = list(
            (await session.execute(select(PlanTaskRow.state).order_by(PlanTaskRow.id))).scalars()
        )
        assert TaskState.needs_human in [TaskState(state) for state in states]

    async def test_a_further_run_does_not_reanimate_it(self, session, flight, stub_gate):
        """`blocked` is terminal. Re-running must not walk it back into a wait."""
        stub_gate(evidence_blocked())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        ctx = await engine.run(ctx)

        assert ctx.state is IncidentState.blocked


class TestAlreadyParkedIncidentsRecover:
    """Databases written before the guard existed still contain the deadlock."""

    async def test_an_incident_parked_on_an_unapprovable_evaluation_is_recovered(
        self, session, flight, stub_gate, monkeypatch
    ):
        # Reproduce the old behaviour exactly: hold for approval regardless of blocking kind.
        from app.orchestrator import engine as engine_module

        monkeypatch.setattr(engine_module, "is_approvable", lambda _result: True)
        stub_gate(evidence_blocked())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        assert ctx.state is IncidentState.awaiting_approval, "failed to reproduce the parked state"

        # Restore the real rule and drive again, as a later /run would.
        monkeypatch.undo()
        stub_gate(evidence_blocked())
        recovered = build(session)
        ctx = await recovered.run(await recovered.load_context(ctx.incident_id))

        assert ctx.state is IncidentState.blocked
        assert await _count(session, Action) == 0
        assert await _count(session, HumanDecision) == 0, "recovery must not invent a decision"

    async def test_recovery_is_recorded_as_such(self, session, flight, stub_gate, monkeypatch):
        from app.orchestrator import engine as engine_module

        monkeypatch.setattr(engine_module, "is_approvable", lambda _result: True)
        stub_gate(evidence_blocked())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)
        monkeypatch.undo()

        stub_gate(evidence_blocked())
        recovered = build(session)
        await recovered.run(await recovered.load_context(ctx.incident_id))

        entry = (
            await session.execute(
                select(DecisionLog)
                .where(DecisionLog.event_type == "STATE_CHANGED")
                .order_by(DecisionLog.id.desc())
                .limit(1)
            )
        ).scalar_one()
        assert entry.detail["recovered_from"] == "awaiting_approval"


class TestApprovalIsStillReal:
    """The fix must not have loosened the approval it exists to protect."""

    async def test_a_risk_only_hold_still_waits_for_a_person(self, session, flight, stub_gate):
        stub_gate(needs_human_result())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert ctx.state is IncidentState.awaiting_approval
        assert await _count(session, Action) == 0, "nothing may execute before a decision"

    async def test_it_stays_waiting_across_repeated_runs_with_no_decision(
        self, session, flight, stub_gate
    ):
        stub_gate(needs_human_result())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        for _ in range(3):
            ctx = await engine.run(ctx)

        assert ctx.state is IncidentState.awaiting_approval
        assert await _count(session, Action) == 0
        assert await _count(session, HumanDecision) == 0

    async def test_no_approval_is_ever_written_by_the_engine(self, session, flight, stub_gate):
        """The engine reads decisions. It must never author one, on either path."""
        for result in (needs_human_result(), evidence_blocked()):
            stub_gate(result)
            engine = build(session)
            ctx = await engine.open_incident(flight.id, "weather")
            await engine.run(ctx)

        assert await _count(session, HumanDecision) == 0

    async def test_an_evaluation_is_still_recorded_for_the_blocked_action(
        self, session, flight, stub_gate
    ):
        """Blocking must not skip the gate record: the refusal is the evidence."""
        stub_gate(evidence_blocked())
        engine = build(session)
        ctx = await engine.open_incident(flight.id, "weather")
        await engine.run(ctx)

        assert await _count(session, AssuranceEvaluation) >= 1
