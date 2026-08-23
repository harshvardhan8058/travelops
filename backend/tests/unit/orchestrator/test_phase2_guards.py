"""Structural guards for the Phase 2 boundaries.

These assert things about the *source*, not about behaviour, because each protects an invariant
that a future edit could break silently while every behavioural test still passed.

Any stream may add a guard here. No stream may weaken one.

Owner: Stream A.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"


def _module(*parts: str) -> ast.Module:
    return ast.parse((APP.joinpath(*parts)).read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    """Module paths this file imports from, e.g. `app.api.actors`."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


class TestOneActorKindMapping:
    """Two mappings would let the timeline and replay disagree about a human decision.

    That is the one thing Phase 1 closed, so the mapping is defined once and imported.
    """

    def test_the_mapping_is_defined_only_in_actors(self):
        defining: list[str] = []
        for path in APP.rglob("*.py"):
            if path.name == "actors.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "_ACTOR_KINDS: dict" in text or "ACTOR_KINDS: dict[str, str] = {" in text:
                defining.append(str(path.relative_to(APP)))
        assert defining == [], f"a second actor_kind mapping exists in {defining}"

    def test_replay_imports_the_shared_mapping(self):
        assert "app.api.actors" in _imported_modules(_module("api", "replay.py"))

    def test_incidents_imports_the_shared_mapping(self):
        assert "app.api.actors" in _imported_modules(_module("api", "incidents.py"))


class TestOnePathToExecution:
    """`execute()` consults `human_decision` and nothing else.

    A plan approval can only cause such a row to exist; it is never read at execution time. If the
    engine learned about plan approvals there would be two paths to execution, which is the most
    dangerous thing Phase 2 could introduce.
    """

    def test_the_engine_never_imports_plan_approval(self):
        imported = _imported_modules(_module("orchestrator", "engine.py"))
        assert not any("plan_approval" in module for module in imported), imported

    def test_the_engine_source_never_mentions_the_plan_approval_model(self):
        text = (APP / "orchestrator" / "engine.py").read_text(encoding="utf-8")
        assert "PlanApproval" not in text

    def test_only_the_approval_service_writes_plan_approvals(self):
        writers: list[str] = []
        for path in APP.rglob("*.py"):
            if path.name == "plan_approval.py" or "models" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "PlanApprovalTier(" in text or "PlanApproval(" in text:
                writers.append(str(path.relative_to(APP)))
        assert writers == [], f"plan approvals are written outside the service: {writers}"


class TestBlastRadiusComputesNothing:
    """ "Just sum the counts" is the mistake that turns 22 connections into 176.

    Stream A bands and exposes; Stream C derives. This guard exists because the wrong version is
    easier to write than the right one.
    """

    def test_the_group_api_does_not_aggregate_action_payloads(self):
        tree = _module("api", "incident_groups.py")
        for node in ast.walk(tree):
            is_sum = isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            if is_sum and node.func.id == "sum":
                source = ast.unparse(node)
                assert "payload" not in source, f"aggregating a payload: {source}"

    def test_the_group_api_does_not_accumulate_a_payload_in_a_loop_either(self):
        """`sum(...)` was too narrow a guard, and something slipped past it.

        `_recorded_exposure` accumulated rooms with `rooms += int(data.get(...))` inside a `for`,
        which is the same aggregation written the other way. It also read a key nothing emits and
        filtered on a status that is not an `ActionStatus` member, so it matched no rows at all and
        reported group exposure as permanently unknown. A guard that only looks for `sum` would
        never have found it.
        """
        tree = _module("api", "incident_groups.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
                source = ast.unparse(node)
                assert "payload" not in source and "data.get" not in source, (
                    f"accumulating a payload: {source}"
                )

    def test_the_group_api_never_reaches_for_an_action_row(self):
        """The strongest form of the same rule, and the one that closes the class.

        Aggregation over recorded findings belongs to a service. If this module cannot name the
        `Action` model it cannot query one, so it cannot grow a second definition of a figure a
        service already owns — whether written as `sum`, as `+=`, or as anything else.
        """
        text = (APP / "api" / "incident_groups.py").read_text(encoding="utf-8")
        assert "import Action" not in text and "Action," not in text, (
            "the group API imports the Action model; read findings through a service instead"
        )

    def test_no_module_filters_actions_on_a_task_state_value(self):
        """`ActionStatus` has `success`; `TaskState` has `succeeded`. They are not interchangeable.

        Comparing `Action.status` to `"succeeded"` is always false, so the query returns nothing and
        the caller reports "unknown" forever. That reads as caution rather than as a bug, which is
        what made it survive. Pinned across the whole app because the mistake is one keystroke and
        the symptom is silence.
        """
        offenders: list[str] = []
        for path in APP.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # Over the AST, not the text: this file's own explanation of the bug quotes the
                # broken comparison, and a substring search cannot tell prose from code.
                if not isinstance(node, ast.Compare):
                    continue
                left = ast.unparse(node.left)
                if not left.endswith("Action.status"):
                    continue
                for comparator in node.comparators:
                    value = comparator.value if isinstance(comparator, ast.Constant) else None
                    if value == "succeeded":
                        offenders.append(str(path.relative_to(APP)))
        assert offenders == [], f"comparing an action status to a task state in {offenders}"

    def test_the_group_api_imports_the_rollup_rather_than_recomputing(self):
        imported = _imported_modules(_module("api", "incident_groups.py"))
        assert "app.db.scenario_queries" in imported


class TestWhatIfCannotClaimAProjection:
    """`basis` and `wrote_rows` are Literals, so the type system carries the boundary."""

    def test_the_response_pins_basis_to_recorded_evidence(self):
        text = (APP / "schemas" / "cascade.py").read_text(encoding="utf-8")
        assert 'basis: Literal["recorded_evidence"]' in text

    def test_the_response_pins_wrote_rows_to_false(self):
        text = (APP / "schemas" / "cascade.py").read_text(encoding="utf-8")
        assert "wrote_rows: Literal[False]" in text

    def test_the_comparison_response_pins_its_basis_too(self):
        text = (APP / "schemas" / "plans.py").read_text(encoding="utf-8")
        assert 'basis: Literal["recorded_evidence"]' in text


class TestTheGroupSummaryGrantsNothing:
    def test_authorises_no_action_is_a_literal_true(self):
        text = (APP / "schemas" / "plans.py").read_text(encoding="utf-8")
        assert "authorises_no_action: Literal[True]" in text


class TestNoAggregateAssuranceScore:
    """A fail-closed, ordered gate has no average. A mean of six checks would be a fiction."""

    def test_no_score_field_on_the_group_assurance_contract(self):
        text = (APP / "schemas" / "plans.py").read_text(encoding="utf-8").lower()
        for banned in ("score:", "confidence:", "average:"):
            assert banned not in text, f"found '{banned}' on a plan contract"
