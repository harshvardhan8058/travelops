"""Planner success is logged only after the existing transaction commit."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models.enums import IncidentState
from app.orchestrator.engine import Orchestrator, WorkflowContext


class _Session:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.commit_error = commit_error
        self.committed = False

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True


class _LogRecorder:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def _record(self, event: str, **fields) -> None:
        self.entries.append({"event": event, **fields})

    info = _record
    warning = _record
    error = _record


@pytest.fixture
def logs(monkeypatch) -> list[dict]:
    recorder = _LogRecorder()
    monkeypatch.setattr("app.orchestrator.engine.log", recorder)
    return recorder.entries


def _context() -> WorkflowContext:
    return WorkflowContext(
        incident_id=1,
        incident_reference="INC-1",
        state=IncidentState.planning,
        correlation_id="correlation-1",
    )


async def test_commit_failure_does_not_emit_created(monkeypatch, logs):
    session = _Session(commit_error=RuntimeError("commit failed"))
    engine = Orchestrator(session, settings=Settings())  # type: ignore[arg-type]

    async def stage_candidate(_ctx) -> None:
        engine._pending_planner_plan_id = 42

    monkeypatch.setattr(engine, "_step_planning", stage_candidate)

    with pytest.raises(RuntimeError, match="commit failed"):
        await engine.advance(_context())

    events = [entry["event"] for entry in logs]
    assert "planner_candidate_persistence_failed" in events
    assert "planner_candidate_created" not in events
    failed = [entry for entry in logs if entry["event"] == "planner_candidate_persistence_failed"]
    assert failed[-1]["phase"] == "commit"
    assert failed[-1]["plan_id"] == 42


async def test_created_is_emitted_after_commit(monkeypatch, logs):
    session = _Session()
    engine = Orchestrator(session, settings=Settings())  # type: ignore[arg-type]

    async def stage_candidate(_ctx) -> None:
        engine._pending_planner_plan_id = 43

    monkeypatch.setattr(engine, "_step_planning", stage_candidate)

    await engine.advance(_context())

    assert session.committed is True
    created = [entry for entry in logs if entry["event"] == "planner_candidate_created"]
    assert created[-1]["phase"] == "committed"
    assert created[-1]["plan_id"] == 43
