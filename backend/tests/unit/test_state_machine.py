"""Incident state machine — the legal transition contract."""

from __future__ import annotations

import pytest

from app.errors import InvalidStateTransition
from app.models.enums import IncidentState as S
from app.orchestrator.state import (
    ACTIVE,
    TERMINAL,
    TRANSITIONS,
    assert_transition,
    can_transition,
    is_terminal,
)


def test_every_state_has_an_entry():
    assert set(TRANSITIONS) == set(S)


def test_terminal_states_have_no_exits():
    for state in TERMINAL:
        assert TRANSITIONS[state] == frozenset(), state


def test_terminal_and_active_partition_the_state_set():
    assert set(S) == TERMINAL | ACTIVE
    assert not TERMINAL & ACTIVE


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.detected, S.assessing),
        (S.assessing, S.planning),
        (S.planning, S.assuring),
        (S.assuring, S.executing),
        (S.assuring, S.awaiting_approval),
        # An approval resumes the same incident rather than opening a new one.
        (S.awaiting_approval, S.executing),
        (S.awaiting_approval, S.assuring),
        # Multi-task plans re-enter assurance for each task.
        (S.executing, S.assuring),
        (S.executing, S.resolved),
    ],
)
def test_permitted_transitions(current: S, target: S):
    assert can_transition(current, target)
    assert_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # Assurance may not be skipped.
        (S.planning, S.executing),
        (S.detected, S.executing),
        (S.assessing, S.resolved),
        # Terminal states are terminal.
        (S.resolved, S.executing),
        (S.blocked, S.assuring),
        (S.failed, S.detected),
    ],
)
def test_forbidden_transitions_raise(current: S, target: S):
    assert not can_transition(current, target)
    with pytest.raises(InvalidStateTransition) as exc:
        assert_transition(current, target, incident_ref="INC-TEST-1")

    details = exc.value.details
    assert details["current_state"] == current.value
    assert details["requested_state"] == target.value
    # The error tells the caller what WAS allowed, so a 409 is actionable.
    assert "allowed" in details


def test_execution_is_never_reachable_without_assurance():
    """The core safety property: no path reaches `executing` except via `assuring`."""
    predecessors = {s for s, targets in TRANSITIONS.items() if S.executing in targets}
    assert predecessors == {S.assuring, S.awaiting_approval}


def test_is_terminal():
    assert is_terminal(S.resolved)
    assert is_terminal(S.blocked)
    assert not is_terminal(S.assuring)
