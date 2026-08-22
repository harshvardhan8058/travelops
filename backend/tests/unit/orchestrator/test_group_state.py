"""Group lifecycle: derived from members, forward only, never optimistic.

The rule these tests defend: **a group is `resolved` only when every member is.** Seven of eight
resolved is `blocked` with the eighth named, because a group claiming completion its members have
not reached is the single most misleading thing this system could report.

Owner: Stream A.
"""

from __future__ import annotations

import pytest

from app.errors import InvalidStateTransition
from app.models.enums import IncidentState as S
from app.orchestrator.group_state import (
    GROUP_ORDER,
    GROUP_STATES,
    GROUP_TERMINAL,
    GROUP_TRANSITIONS,
    assert_group_transition,
    can_transition,
    derive_group_state,
    is_terminal,
    unresolved_members,
)


class TestVocabulary:
    def test_group_states_are_a_subset_of_incident_states(self):
        """Steering: no layer defines an alternate state vocabulary.

        Asserted mechanically rather than by reading, so the two cannot diverge in a later edit.
        """
        assert set(S) >= GROUP_STATES

    def test_per_task_states_are_excluded(self):
        """`assuring` and `awaiting_approval` describe one task, never a network event."""
        assert S.assuring not in GROUP_STATES
        assert S.awaiting_approval not in GROUP_STATES

    def test_every_group_state_has_a_transition_entry(self):
        assert set(GROUP_TRANSITIONS) == GROUP_STATES

    def test_a_group_may_only_move_to_a_group_state(self):
        for source, targets in GROUP_TRANSITIONS.items():
            assert targets <= GROUP_STATES, source


class TestDerivation:
    def test_no_members_reads_as_detected(self):
        assert derive_group_state([]) is S.detected

    def test_every_member_resolved_resolves_the_group(self):
        assert derive_group_state([S.resolved] * 8) is S.resolved

    def test_seven_of_eight_is_blocked_not_resolved(self):
        """The load-bearing assertion in this file."""
        states = [S.resolved] * 7 + [S.blocked]
        assert derive_group_state(states) is S.blocked

    def test_one_failed_member_blocks_the_group(self):
        states = [S.resolved] * 7 + [S.failed]
        assert derive_group_state(states) is S.blocked

    def test_a_group_is_never_failed_as_a_unit(self):
        """`failed` covers eight different situations; `blocked` names the member instead."""
        assert derive_group_state([S.failed] * 8) is S.blocked

    def test_any_member_executing_makes_the_group_executing(self):
        states = [S.planning, S.executing, S.detected]
        assert derive_group_state(states) is S.executing

    def test_a_resolved_member_alongside_active_ones_is_executing(self):
        assert derive_group_state([S.resolved, S.planning]) is S.executing

    def test_all_members_awaiting_approval_reads_as_planning(self):
        """Eight held tasks is a planned group, not an executing one."""
        assert derive_group_state([S.awaiting_approval] * 8) is S.planning

    def test_one_member_moved_reads_as_assessing(self):
        assert derive_group_state([S.assessing, S.detected, S.detected]) is S.assessing

    def test_untouched_members_read_as_detected(self):
        assert derive_group_state([S.detected] * 8) is S.detected

    def test_strings_are_accepted_as_well_as_enums(self):
        assert derive_group_state(["resolved", "resolved"]) is S.resolved


class TestTransitions:
    def test_progress_is_forward_only(self):
        for index, state in enumerate(GROUP_ORDER):
            for earlier in GROUP_ORDER[:index]:
                assert not can_transition(state, earlier), f"{state} -> {earlier}"

    def test_a_derivation_may_skip_stages(self):
        """Members can cross several stages between two derivations.

        Eight incidents that were all `awaiting_approval` and are all `resolved` after one
        approval sweep move the group from `planning` straight to `resolved`. Requiring it to pass
        through `executing` would fail a request for a state nobody steered.
        """
        assert can_transition(S.detected, S.planning)
        assert can_transition(S.planning, S.resolved)

    def test_a_group_may_terminate_from_any_active_stage(self):
        for state in GROUP_ORDER:
            for terminal in GROUP_TERMINAL:
                assert can_transition(state, terminal), f"{state} -> {terminal}"

    def test_a_terminal_group_is_final(self):
        for terminal in GROUP_TERMINAL:
            assert GROUP_TRANSITIONS[terminal] == frozenset()
            assert is_terminal(terminal)

    def test_an_illegal_move_names_what_was_allowed(self):
        with pytest.raises(InvalidStateTransition) as raised:
            assert_group_transition(S.resolved, S.executing, group_ref="GRP-X")
        details = raised.value.details
        assert details["group_reference"] == "GRP-X"
        assert details["current_state"] == "resolved"
        assert details["requested_state"] == "executing"
        assert details["allowed"] == []

    def test_staying_put_is_not_a_transition(self):
        assert_group_transition(S.executing, S.executing, group_ref="GRP-X")


class TestUnresolvedMembers:
    def test_it_names_what_did_not_resolve(self):
        members = {
            "INC-1": S.resolved,
            "INC-2": S.blocked,
            "INC-3": S.resolved,
            "INC-4": S.failed,
        }
        assert unresolved_members(members) == ["INC-2", "INC-4"]

    def test_a_fully_resolved_group_names_nothing(self):
        assert unresolved_members({"INC-1": S.resolved}) == []
