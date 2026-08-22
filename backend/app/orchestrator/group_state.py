"""Disruption-group lifecycle.

`incident_group.state` has existed since the initial schema and nothing drove it: a group
eight flights deep still read `detected`. This module gives it a legal transition table and,
more importantly, a *derivation* — a group's state is a function of its members, never an
independently steered value.

Two rules make the derivation trustworthy:

1. **`resolved` requires every member resolved.** Seven of eight is not success. A group that
   claimed completion its members had not reached would be the single most misleading thing
   this system could report.
2. **The vocabulary is `IncidentState`.** Steering: "no layer defines an alternate state
   vocabulary." A second enum would also need a migration and a widened CHECK constraint. The
   group uses a documented *subset* — `assuring` and `awaiting_approval` are per-task concepts
   and never describe a group — with its own transition table, so group and incident rules stay
   independent without splitting the vocabulary.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.errors import InvalidStateTransition
from app.models.enums import IncidentState as S

#: The legal subset. A strict subset of the CHECK constraint on `incident_group.state`, so
#: no migration is required and no value this module produces can violate the database.
GROUP_STATES: frozenset[S] = frozenset(
    {S.detected, S.assessing, S.planning, S.executing, S.resolved, S.blocked, S.failed}
)

#: Progress order. A group state is DERIVED from its members, and members can advance several
#: stages between two calls — eight incidents driven from `detected` to `awaiting_approval` in one
#: request legitimately moves the group from `detected` straight to `planning`. So the rule that
#: matters is not "one step at a time", it is **forward only**: a group must never appear to go
#: backwards, and must never reach `resolved` before its members do.
GROUP_ORDER: tuple[S, ...] = (S.detected, S.assessing, S.planning, S.executing)

#: A group may terminate from ANY progress stage, because its members decide when it is over and
#: they can cross several stages between two derivations. Eight incidents that were all
#: `awaiting_approval` (group: `planning`) and are all `resolved` after one approval sweep move the
#: group straight from `planning` to `resolved`; requiring it to pass through `executing` first
#: would fail a request for a state nobody steered.
#:
#: The three rules that actually matter, and that the table enforces:
#:   1. progress is forward only (see GROUP_ORDER and the guard below);
#:   2. a terminal state is reachable from anywhere active;
#:   3. a terminal state is final.
GROUP_TRANSITIONS: dict[S, frozenset[S]] = {
    S.detected: frozenset({S.assessing, S.planning, S.executing, S.resolved, S.blocked, S.failed}),
    S.assessing: frozenset({S.planning, S.executing, S.resolved, S.blocked, S.failed}),
    S.planning: frozenset({S.executing, S.resolved, S.blocked, S.failed}),
    # A group re-enters `executing` as later members reach it, so the edge is reflexive.
    S.executing: frozenset({S.executing, S.resolved, S.blocked, S.failed}),
    S.resolved: frozenset(),
    S.blocked: frozenset(),
    S.failed: frozenset(),
}

GROUP_TERMINAL: frozenset[S] = frozenset({S.resolved, S.blocked, S.failed})


def can_transition(current: S, target: S) -> bool:
    if current not in GROUP_TRANSITIONS:
        return False
    return target in GROUP_TRANSITIONS[current]


def assert_group_transition(current: S, target: S, *, group_ref: str | None = None) -> None:
    """Raise InvalidStateTransition (HTTP 409) when a group move is not permitted."""
    if current == target:
        return
    if can_transition(current, target):
        return
    raise InvalidStateTransition(
        f"cannot move disruption group from '{current.value}' to '{target.value}'",
        details={
            "group_reference": group_ref,
            "current_state": current.value,
            "requested_state": target.value,
            "allowed": sorted(s.value for s in GROUP_TRANSITIONS.get(current, frozenset())),
        },
    )


def is_terminal(state: S) -> bool:
    return state in GROUP_TERMINAL


def derive_group_state(member_states: Iterable[S | str]) -> S:
    """The group's state, computed from its members. Never a stored guess.

    | Result | Rule |
    | --- | --- |
    | `detected` | no members, or every member still `detected` |
    | `assessing` | some member has moved, none has a plan yet |
    | `planning` | every active member is at least `planning` |
    | `executing` | any member is `executing` or has resolved while others continue |
    | `resolved` | **every** member `resolved` |
    | `blocked` | every member terminal, at least one not `resolved` |

    `failed` is never produced here. A group does not fail as a unit in Phase 2; a member
    failing makes the group `blocked` with that member named, which is more useful to an
    operator than a single word covering eight different situations.
    """
    states = [S(state) for state in member_states]
    if not states:
        return S.detected

    if all(state is S.resolved for state in states):
        return S.resolved

    if all(state in S.terminal() for state in states):
        # Every member finished and at least one did not resolve.
        return S.blocked

    if any(state in {S.executing, S.resolved} for state in states):
        return S.executing

    active = [state for state in states if state not in S.terminal()]
    if active and all(state in {S.planning, S.assuring, S.awaiting_approval} for state in active):
        return S.planning

    if any(state is not S.detected for state in states):
        return S.assessing

    return S.detected


def unresolved_members(members: dict[str, S | str]) -> list[str]:
    """References of members that are not `resolved`, for a blocked group's reason."""
    return sorted(ref for ref, state in members.items() if S(state) is not S.resolved)


def _validate_table() -> None:
    assert set(S) >= GROUP_STATES, "group states must be a subset of IncidentState"
    assert set(GROUP_TRANSITIONS) == GROUP_STATES, "every group state needs a transition entry"
    for targets in GROUP_TRANSITIONS.values():
        assert targets <= GROUP_STATES, "a group may only move to a group state"


_validate_table()
