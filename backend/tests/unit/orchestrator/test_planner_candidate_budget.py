"""A slow planner model must not hold an incident's run open.

Phase 3's contract is that the optional Planner candidate never blocks recovery: the playbook plan
is persisted and selected before the agent is asked. That was enforced for a model that *fails* but
not for one that is merely *slow* — and because group runs advance their members sequentially, one
hung call per incident is enough to push a whole cascade past its caller's request budget.

These tests pin the boundary: the model call is bounded by
`Settings.planner_candidate_budget_seconds`, a breach takes the existing skip route, and no plan or
task rows are written when it does.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import (
    LLMMode,
    NotificationMode,
    PolicyMode,
    ResolvedModes,
    Settings,
    WeatherMode,
)
from app.models.enums import IncidentState
from app.orchestrator.engine import Orchestrator, WorkflowContext


class _Session:
    """Just enough AsyncSession for the pre-call reads in `_propose_planner_candidate`."""

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    async def get(self, _model, _pk):
        return None

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1


class _LogRecorder:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def _record(self, event: str, **fields) -> None:
        self.entries.append({"event": event, **fields})

    info = _record
    warning = _record
    error = _record


def _modes() -> ResolvedModes:
    return ResolvedModes(
        llm=LLMMode.live,
        weather=WeatherMode.fixture,
        notification=NotificationMode.console,
        policy=PolicyMode.demo,
        real_email_enabled=False,
        assurance_config_present=True,
        assurance_config_version="v1",
        assurance_config_hash="hash",
        degradations=[],
    )


def _context() -> WorkflowContext:
    return WorkflowContext(
        incident_id=1,
        incident_reference="INC-BUDGET-1",
        state=IncidentState.planning,
        correlation_id="correlation-budget",
    )


@pytest.fixture
def logs(monkeypatch) -> list[dict]:
    recorder = _LogRecorder()
    monkeypatch.setattr("app.orchestrator.engine.log", recorder)
    return recorder.entries


@pytest.fixture
def journalled(monkeypatch) -> list[dict]:
    """Capture `_journal` calls without needing a real DecisionLog row."""
    captured: list[dict] = []

    async def _capture(_self, _ctx, **kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(Orchestrator, "_journal", _capture)
    return captured


@pytest.fixture
def no_precedents(monkeypatch) -> None:
    async def _none(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.memory.retrieval.find_precedents", _none)


def _engine(session: _Session, *, budget: float) -> Orchestrator:
    return Orchestrator(
        session,  # type: ignore[arg-type]
        settings=Settings(planner_candidate_budget_seconds=budget),
        modes=_modes(),
    )


def _stub_propose(monkeypatch, delay: float) -> None:
    """Replace the agent's model call with one that sleeps longer than any test budget."""

    class _SlowPlanner:
        async def propose(self, **_kwargs):
            await asyncio.sleep(delay)
            raise AssertionError("the budget should have cancelled this call")

    monkeypatch.setattr("app.agents.planner.PlannerAgent", _SlowPlanner)


async def test_slow_model_is_abandoned_at_the_budget(monkeypatch, logs, journalled, no_precedents):
    """The call is cancelled at the budget instead of running to the client's own 184s ceiling."""
    session = _Session()
    engine = _engine(session, budget=0.05)
    _stub_propose(monkeypatch, delay=30.0)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await engine._propose_planner_candidate(_context())
    elapsed = loop.time() - started

    # Generous ceiling: the point is that it returns in a fraction of the model's 30s, not that
    # the timer is precise.
    assert elapsed < 5.0


async def test_slow_model_takes_the_existing_unavailable_route(
    monkeypatch, logs, journalled, no_precedents
):
    """A timeout is journalled as PLANNER_AGENT_UNAVAILABLE, not as an unexpected failure."""
    session = _Session()
    engine = _engine(session, budget=0.05)
    _stub_propose(monkeypatch, delay=30.0)

    await engine._propose_planner_candidate(_context())

    event_types = [entry["event_type"] for entry in journalled]
    assert event_types == ["PLANNER_AGENT_UNAVAILABLE"]
    assert "PLANNER_AGENT_FAILED" not in event_types

    detail = journalled[0]["detail"]
    # The phase distinguishes an orchestrator-side budget breach from a provider-side failure, and
    # the reason names the budget that was applied.
    assert detail["llm_phase"] == "orchestrator_budget"
    assert detail["phase"] == "request"
    assert "0.05s budget" in detail["reason"]

    skipped = [entry for entry in logs if entry["event"] == "planner_candidate_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["llm_phase"] == "orchestrator_budget"


async def test_timeout_writes_no_plan_or_task_rows(monkeypatch, logs, journalled, no_precedents):
    """The playbook plan is the only plan left standing, and the session was never flushed."""
    session = _Session()
    engine = _engine(session, budget=0.05)
    _stub_propose(monkeypatch, delay=30.0)

    await engine._propose_planner_candidate(_context())

    assert session.added == []
    assert session.flushes == 0
    assert engine._pending_planner_plan_id is None


async def test_wrapper_is_transparent_to_a_call_inside_the_budget(
    monkeypatch, logs, journalled, no_precedents
):
    """A provider error inside the budget still surfaces as itself, with its own diagnostics.

    The wrapper must bound the call without rewriting its outcome: an `LLMUnavailable` raised by
    the agent has to arrive at the existing handler carrying the agent's own phase, not the
    orchestrator's.
    """
    from app.llm.client import LLMUnavailable

    reached: list[str] = []

    class _FailingPlanner:
        async def propose(self, **kwargs):
            reached.append(kwargs["incident_reference"])
            raise LLMUnavailable("provider said no", phase="assistant_json", status_code=429)

    monkeypatch.setattr("app.agents.planner.PlannerAgent", _FailingPlanner)

    engine = _engine(_Session(), budget=20.0)
    await engine._propose_planner_candidate(_context())

    assert reached == ["INC-BUDGET-1"]
    detail = journalled[0]["detail"]
    assert detail["llm_phase"] == "assistant_json"
    assert detail["status_code"] == 429
    assert "provider said no" in detail["reason"]


def test_budget_is_configurable_and_bounded():
    """The default bounds an eight-incident sequential cascade well inside a 300s request."""
    assert Settings().planner_candidate_budget_seconds == 20.0
    assert Settings().planner_candidate_budget_seconds * 8 < 300

    with pytest.raises(ValueError):
        Settings(planner_candidate_budget_seconds=0)
    with pytest.raises(ValueError):
        Settings(planner_candidate_budget_seconds=181)
