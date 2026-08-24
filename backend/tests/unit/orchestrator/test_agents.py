"""The agent boundary: what a model may propose, and what it may never do.

The load-bearing property is that a model cannot widen the system. It orders work from a closed set;
the orchestrator decides which entities exist, the gate decides what is allowed, and dispatch is the
only thing that turns an action into a call.

Owner: Stream A.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.contract import PlannerResponse, PlanTask
from app.agents.reflection import (
    CODE_DUPLICATE,
    CODE_INVENTED_TARGET,
    CODE_NO_SERVICE,
    CODE_SELF_DEPENDENCY,
    CODE_UNKNOWN_DEPENDENCY,
    reflect,
)
from app.models.enums import ActionType

APP = Path(__file__).resolve().parents[3] / "app"
REFS = ["incident:INC-1", "flight:1"]
REGISTERED = {
    ActionType.check_connections,
    ActionType.assess_crew_impact,
    ActionType.notify_passengers,
}


def _task(action: ActionType, *, refs: list[str] | None = None, deps: list[str] | None = None):
    return PlanTask(
        action=action,
        target_refs=REFS if refs is None else refs,
        depends_on=deps or [],
    )


class TestTheClosedActionEnum:
    def test_an_invented_action_is_rejected_before_assurance(self):
        """The enum is the boundary, and it fails inside the agent rather than at the gate."""
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(
                {
                    "status": "success",
                    "reason": "r",
                    "payload_type": "planner.v1",
                    "tasks": [{"action": "wire_money_to_me", "target_refs": []}],
                }
            )

    def test_an_empty_task_list_is_rejected(self):
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(
                {"status": "success", "reason": "r", "payload_type": "planner.v1", "tasks": []}
            )

    def test_confidence_is_not_a_contract_field(self):
        """`extra="forbid"`, so a model cannot smuggle a number the system might branch on."""
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(
                {
                    "status": "success",
                    "reason": "r",
                    "payload_type": "planner.v1",
                    "confidence": 91,
                    "tasks": [{"action": "check_connections", "target_refs": []}],
                }
            )


class TestReflection:
    def test_an_action_with_no_service_is_dropped_and_named(self):
        result = reflect(
            [_task(ActionType.check_connections), _task(ActionType.reassign_gate)],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        assert [t.action for t in result.tasks] == [ActionType.check_connections]
        assert [f.code for f in result.findings] == [CODE_NO_SERVICE]

    def test_a_duplicate_action_is_dropped(self):
        result = reflect(
            [_task(ActionType.check_connections), _task(ActionType.check_connections)],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        assert len(result.tasks) == 1
        assert CODE_DUPLICATE in [f.code for f in result.findings]

    def test_an_invented_target_ref_is_dropped(self):
        """A model naming an entity the orchestrator did not supply is a hallucination as fact."""
        result = reflect(
            [_task(ActionType.check_connections, refs=["flight:9999"])],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        assert result.rejected is True
        assert CODE_INVENTED_TARGET in [f.code for f in result.findings]

    def test_surviving_tasks_carry_the_orchestrators_refs_not_the_models(self):
        result = reflect(
            [_task(ActionType.check_connections, refs=[])],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        assert result.tasks[0].target_refs == REFS

    def test_a_dependency_on_a_dropped_task_is_removed_not_left_dangling(self):
        result = reflect(
            [
                _task(ActionType.notify_passengers, deps=["reassign_gate"]),
                _task(ActionType.check_connections),
            ],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        notify = next(t for t in result.tasks if t.action is ActionType.notify_passengers)
        assert notify.depends_on == []
        assert CODE_UNKNOWN_DEPENDENCY in [f.code for f in result.findings]

    def test_a_self_dependency_is_removed(self):
        result = reflect(
            [_task(ActionType.check_connections, deps=["check_connections"])],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        assert result.tasks[0].depends_on == []
        assert CODE_SELF_DEPENDENCY in [f.code for f in result.findings]

    def test_a_valid_dependency_survives(self):
        result = reflect(
            [
                _task(ActionType.check_connections),
                _task(ActionType.notify_passengers, deps=["check_connections"]),
            ],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        notify = next(t for t in result.tasks if t.action is ActionType.notify_passengers)
        assert notify.depends_on == ["check_connections"]

    def test_nothing_executable_is_a_rejection_not_an_empty_plan(self):
        """An empty plan would let an incident resolve without doing anything."""
        result = reflect(
            [_task(ActionType.reassign_gate)],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        assert result.rejected is True
        assert result.tasks == []

    def test_every_finding_carries_a_code_and_a_reason(self):
        result = reflect(
            [_task(ActionType.reassign_gate)],
            available_actions=REGISTERED,
            allowed_target_refs=REFS,
        )
        for finding in result.findings:
            assert finding.code and finding.detail


class TestTheToolInvocationBoundary:
    """Agents propose. Only dispatch invokes. Asserted structurally, not by convention."""

    def _imports(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
        return modules

    def test_agents_import_no_service(self):
        for path in (APP / "agents").rglob("*.py"):
            for module in self._imports(path):
                assert not module.startswith("app.services"), f"{path.name} imports {module}"

    def test_agents_import_no_database_session_or_model(self):
        """No session means nothing for a prompt injection to reach, and no state to corrupt."""
        for path in (APP / "agents").rglob("*.py"):
            for module in self._imports(path):
                assert not module.startswith("app.db"), f"{path.name} imports {module}"
                assert "sqlalchemy" not in module, f"{path.name} imports {module}"

    def test_agents_never_call_dispatch(self):
        """Checked on the AST, not the text: the word appears in a docstring explaining the rule."""
        for path in (APP / "agents").rglob("*.py"):
            for module in self._imports(path):
                assert "dispatch" not in module, f"{path.name} imports {module}"
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = ast.unparse(node.func)
                    assert "dispatch" not in called, f"{path.name} calls {called}"

    def test_the_llm_client_is_the_only_module_that_imports_the_model_sdk(self):
        """Checked on imports, not on text.

        An earlier version searched for the string `api.groq.com`, which the current client does not
        contain, so the guard passed while asserting nothing. Broadening it to any mention of
        "groq" then caught five modules that only name it in a comment. The import graph is the
        thing that actually decides who can call a model.
        """
        offenders = sorted(
            str(path.relative_to(APP))
            for path in APP.rglob("*.py")
            if path.name != "client.py"
            and any(module.split(".")[0] == "groq" for module in self._imports(path))
        )
        assert offenders == [], f"a second model call path exists: {offenders}"

    def test_prompts_are_files_not_inline_strings(self):
        """`plan.prompt_version` is meaningless if the prompt lives in a Python literal."""
        assert (APP / "llm" / "prompts" / "planner.v1.md").is_file()
        for path in (APP / "agents").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "You are the recovery planner" not in text, path.name
