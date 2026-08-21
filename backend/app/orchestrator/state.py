"""Incident state machine.

The legal transition table is a CONTRACT, not an implementation detail: the API returns
409 INVALID_STATE_TRANSITION based on it, the UI renders its progress rail from it, and
tests assert against it. That is why it ships in Wave 0 rather than being left to a stream.

Stream A owns the engine that drives these transitions.
"""

from __future__ import annotations

from app.errors import InvalidStateTransition
from app.models.enums import IncidentState as S

# Canonical lower-case vocabulary. Display may title-case; no layer redefines the set.
TRANSITIONS: dict[S, frozenset[S]] = {
    S.detected: frozenset({S.assessing, S.failed}),
    S.assessing: frozenset({S.planning, S.blocked, S.failed}),
    S.planning: frozenset({S.assuring, S.blocked, S.failed}),
    # Assurance either authorises, defers to a human, or blocks.
    S.assuring: frozenset({S.executing, S.awaiting_approval, S.blocked, S.failed}),
    # An approval resumes the SAME incident; it does not open a new one.
    S.awaiting_approval: frozenset({S.assuring, S.executing, S.blocked, S.failed}),
    # Re-entering assuring covers multi-task plans where each task is gated separately.
    S.executing: frozenset({S.assuring, S.resolved, S.blocked, S.failed}),
    # Terminal.
    S.resolved: frozenset(),
    S.blocked: frozenset(),
    S.failed: frozenset(),
}

TERMINAL: frozenset[S] = frozenset({S.resolved, S.blocked, S.failed})
ACTIVE: frozenset[S] = frozenset(set(S) - TERMINAL)


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: S, target: S, *, incident_ref: str | None = None) -> None:
    """Raise InvalidStateTransition (HTTP 409) when the move is not permitted."""
    if can_transition(current, target):
        return
    raise InvalidStateTransition(
        f"cannot move incident from '{current.value}' to '{target.value}'",
        details={
            "incident_reference": incident_ref,
            "current_state": current.value,
            "requested_state": target.value,
            "allowed": sorted(s.value for s in TRANSITIONS[current]),
        },
    )


def is_terminal(state: S) -> bool:
    return state in TERMINAL


def _validate_table() -> None:
    """Guard against an unreachable state being introduced by a future edit."""
    assert set(TRANSITIONS) == set(S), "every state needs an entry in TRANSITIONS"
    reachable = {S.detected} | {t for targets in TRANSITIONS.values() for t in targets}
    orphans = set(S) - reachable
    assert not orphans, f"unreachable states: {sorted(s.value for s in orphans)}"


_validate_table()
