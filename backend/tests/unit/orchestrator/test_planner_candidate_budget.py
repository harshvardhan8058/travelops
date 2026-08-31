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
from types import SimpleNamespace

import pytest

from app.agents.contract import ModelCallAudit, PlannerResponse
from app.config import (
    LLMMode,
    NotificationMode,
    PolicyMode,
    ResolvedModes,
    Settings,
    WeatherMode,
)
from app.models.enums import ActionStatus, ActionType, IncidentState
from app.models.workflow import Incident, Plan
from app.orchestrator.engine import Orchestrator, WorkflowContext


class _Session:
    """Just enough AsyncSession for the reads and candidate writes in the budget tests."""

    def __init__(self, *, incident=None, member_role: str | None = None) -> None:
        self.incident = incident
        self.member_role = member_role
        self.added: list[object] = []
        self.flushes = 0
        self._next_id = 1

    async def get(self, model, _pk):
        if model is Incident:
            return self.incident
        return None

    async def scalar(self, _statement):
        return self.member_role

    def add(self, obj: object) -> None:
        # SQLAlchemy would assign these on flush. Assigning them here keeps dependency and pending-
        # plan assertions meaningful without replacing the method under test with a real database.
        if hasattr(obj, "id") and obj.id is None:  # type: ignore[attr-defined]
            obj.id = self._next_id  # type: ignore[attr-defined]
            self._next_id += 1
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


def _engine(
    session: _Session, *, budget: float, primary_demo_budget: float | None = None
) -> Orchestrator:
    return Orchestrator(
        session,  # type: ignore[arg-type]
        settings=Settings(
            planner_candidate_budget_seconds=budget,
            primary_demo_planner_candidate_budget_seconds=(
                primary_demo_budget if primary_demo_budget is not None else 40.0
            ),
        ),
        modes=_modes(),
    )


def _stub_propose(monkeypatch, delay: float) -> None:
    """Replace the agent's model call with one that sleeps longer than any test budget."""

    class _SlowPlanner:
        async def propose(self, **_kwargs):
            await asyncio.sleep(delay)
            raise AssertionError("the budget should have cancelled this call")

    monkeypatch.setattr("app.agents.planner.PlannerAgent", _SlowPlanner)


def _successful_propose(monkeypatch, *, delay: float) -> None:
    """A valid live-like planner response after a controlled provider latency."""

    class _SuccessfulPlanner:
        async def propose(self, **kwargs):
            await asyncio.sleep(delay)
            return (
                PlannerResponse(
                    status=ActionStatus.success,
                    reason="Check threatened connections before notifying passengers.",
                    evidence_refs=[f"incident:{kwargs['incident_reference']}"],
                    tasks=[
                        {
                            "action": ActionType.check_connections,
                            "target_refs": [f"incident:{kwargs['incident_reference']}"],
                            "inputs": {},
                            "depends_on": [],
                        }
                    ],
                ),
                ModelCallAudit(
                    generator="openrouter:openai/gpt-oss-120b",
                    prompt_version="planner.v1",
                    latency_ms=int(delay * 1000),
                ),
            )

    monkeypatch.setattr("app.agents.planner.PlannerAgent", _SuccessfulPlanner)
    monkeypatch.setattr("app.orchestrator.dispatch.is_implemented", lambda _action: True)


def _primary_demo_session() -> _Session:
    return _Session(
        incident=SimpleNamespace(
            id=1,
            group_id=7,
            flight_id=42,
            severity="high",
            demo_dataset_id="bengaluru_storm",
        ),
        member_role="primary",
    )


def _primary_context() -> WorkflowContext:
    return WorkflowContext(
        incident_id=1,
        incident_reference="INC-2026-0820-VOBL-01",
        state=IncidentState.planning,
        correlation_id="correlation-primary-live",
        flight_id=42,
        trigger_type="weather",
    )


@pytest.mark.parametrize(
    ("member_role", "dataset_id", "expected", "is_primary_demo"),
    [
        ("primary", "bengaluru_storm", 40.0, True),
        ("affected_departure", "bengaluru_storm", 20.0, False),
        ("primary", "another_dataset", 20.0, False),
        (None, "bengaluru_storm", 20.0, False),
    ],
)
async def test_extra_allowance_requires_both_declared_primary_role_and_demo_dataset(
    member_role, dataset_id, expected, is_primary_demo
):
    """Never infer primary from `-01`, and never apply demo tuning to a production incident."""
    incident = SimpleNamespace(
        group_id=7,
        flight_id=42,
        demo_dataset_id=dataset_id,
    )
    engine = _engine(
        _Session(incident=incident, member_role=member_role),
        budget=20.0,
        primary_demo_budget=40.0,
    )

    assert await engine._planner_candidate_budget(incident) == (expected, is_primary_demo)


async def test_response_just_under_the_ordinary_budget_is_persisted(
    monkeypatch, logs, journalled, no_precedents
):
    """A healthy response near the deadline is not mistaken for a timeout."""
    session = _Session()
    engine = _engine(session, budget=0.12)
    _successful_propose(monkeypatch, delay=0.08)

    await engine._propose_planner_candidate(_context())

    candidates = [row for row in session.added if isinstance(row, Plan)]
    assert len(candidates) == 1
    assert candidates[0].generator == "planner-agent"
    assert engine._pending_planner_plan_id == candidates[0].id
    assert [entry["event_type"] for entry in journalled] == ["PLAN_PROPOSED"]


async def test_primary_demo_live_latency_beyond_twenty_seconds_still_creates_candidate(
    monkeypatch, logs, journalled, no_precedents
):
    """The primary's observed first-call latency fits inside its scoped 40-second allowance.

    This deliberately takes a little over 20 real seconds: the regression must fail against the
    previous global 20-second boundary rather than merely inspect a configured number. The agent
    stays a local deterministic stub — no CI test should call a paid provider — but the
    orchestrator runs in live mode, sleeps at the same await boundary as HTTPX, reflects the
    response and stages the same Plan and task rows a real provider response would.
    """
    session = _primary_demo_session()
    engine = _engine(session, budget=20.0, primary_demo_budget=40.0)
    _successful_propose(monkeypatch, delay=20.5)

    await engine._propose_planner_candidate(_primary_context())

    candidates = [row for row in session.added if isinstance(row, Plan)]
    assert len(candidates) == 1
    assert candidates[0].generator == "planner-agent"
    assert engine._pending_planner_plan_id == candidates[0].id
    assert [entry["event_type"] for entry in journalled] == ["PLAN_PROPOSED"]
    selected = [entry for entry in logs if entry["event"] == "planner_candidate_budget_selected"]
    assert selected == [
        {
            "event": "planner_candidate_budget_selected",
            "incident_reference": "INC-2026-0820-VOBL-01",
            "budget_seconds": 40.0,
            "primary_demo_budget": True,
        }
    ]


async def test_slow_primary_demo_model_is_abandoned_at_its_bounded_allowance(
    monkeypatch, logs, journalled, no_precedents
):
    """The primary gets more time, never unlimited time; fallback remains deterministic."""
    session = _primary_demo_session()
    engine = _engine(session, budget=0.02, primary_demo_budget=0.05)
    _stub_propose(monkeypatch, delay=30.0)

    await engine._propose_planner_candidate(_primary_context())

    assert session.added == []
    assert session.flushes == 0
    assert engine._pending_planner_plan_id is None
    assert [entry["event_type"] for entry in journalled] == ["PLANNER_AGENT_UNAVAILABLE"]
    detail = journalled[0]["detail"]
    assert detail["llm_phase"] == "orchestrator_budget"
    assert detail["budget_seconds"] == 0.05
    assert detail["primary_demo_budget"] is True


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


def test_budgets_are_configurable_bounded_and_fit_the_phase2_request():
    """One 40s primary plus seven 20s members leaves 120s inside Phase 2's 300s request."""
    settings = Settings()
    assert settings.planner_candidate_budget_seconds == 20.0
    assert settings.primary_demo_planner_candidate_budget_seconds == 40.0
    assert (
        settings.primary_demo_planner_candidate_budget_seconds
        + 7 * settings.planner_candidate_budget_seconds
        == 180.0
        < 300
    )

    with pytest.raises(ValueError):
        Settings(planner_candidate_budget_seconds=0)
    with pytest.raises(ValueError):
        Settings(planner_candidate_budget_seconds=181)
    with pytest.raises(ValueError):
        Settings(primary_demo_planner_candidate_budget_seconds=0)
    with pytest.raises(ValueError):
        Settings(primary_demo_planner_candidate_budget_seconds=121)
