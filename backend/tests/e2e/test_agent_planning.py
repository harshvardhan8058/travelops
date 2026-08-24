"""The planner candidate over the real app, and what reflection does to it.

Main's design: the playbook plan is created first and stays selected; the planner produces an
**additional candidate**. This file asserts the Phase 3 invariants at that seam — the model widens
nothing, and a candidate that cannot be executed is not offered as one.

Owner: Stream A.
"""

from __future__ import annotations

import pytest

from app.config import LLMMode, get_modes

PREFIX = "/api/v1"


@pytest.fixture
def agent_modes(monkeypatch):
    """Run the orchestrator in `LLM_MODE=fixture`: the agent path, no network."""
    resolved = get_modes()
    stub = type(
        "Modes",
        (),
        {
            "llm": LLMMode.fixture,
            "weather": resolved.weather,
            "notification": resolved.notification,
            "policy": resolved.policy,
            "real_email_enabled": resolved.real_email_enabled,
            "assurance_config_version": resolved.assurance_config_version,
            "assurance_config_hash": resolved.assurance_config_hash,
            "workflow_executable": resolved.workflow_executable,
        },
    )()
    monkeypatch.setattr("app.orchestrator.engine.get_modes", lambda: stub)

    # The client resolves its own mode from settings, so the engine's view is not enough. Forced
    # explicitly rather than left to an ambient default: a test that depends on `.env` passes or
    # fails for reasons that have nothing to do with the code under test.
    from app.llm.client import LLMClient

    monkeypatch.setattr(
        "app.agents.planner.LLMClient", lambda **_kwargs: LLMClient(mode=LLMMode.fixture)
    )
    return stub


def _plans(client, incident) -> list[dict]:
    return client.get(f"{PREFIX}/incidents/{incident}/plans").json()["plans"]


class TestThePlaybookStaysAuthoritative:
    def test_the_selected_plan_is_still_the_playbook(self, client, incident, agent_modes):
        """A model producing a candidate must not silently take over the run."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        assert plan["generator"] == "fallback-playbook"
        assert plan["prompt_version"] is None

    def test_the_agent_candidate_exists_alongside_it(self, client, incident, agent_modes):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        generators = {plan["generator"] for plan in _plans(client, incident)}

        assert "fallback-playbook" in generators
        assert "planner-agent" in generators

    def test_the_candidate_records_its_prompt_version(self, client, incident, agent_modes):
        """`plan.prompt_version` is what makes a model-produced plan reproducible."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        candidate = next(
            plan for plan in _plans(client, incident) if plan["generator"] == "planner-agent"
        )

        assert candidate["prompt_version"] == "planner.v1"
        assert candidate["selection_state"] == "candidate"


class TestReflectionNarrowsTheCandidate:
    def test_no_task_survives_without_a_registered_service(self, client, incident, agent_modes):
        """Reflection reads the dispatch registry, so this widens by itself as services land."""
        from app.models.enums import ActionType
        from app.orchestrator import dispatch

        client.post(f"{PREFIX}/incidents/{incident}/run")
        candidate = next(
            plan for plan in _plans(client, incident) if plan["generator"] == "planner-agent"
        )

        for task in candidate["tasks"]:
            assert dispatch.is_implemented(ActionType(task["action_type"])), task["action_type"]

    def test_no_task_is_duplicated(self, client, incident, agent_modes):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        candidate = next(
            plan for plan in _plans(client, incident) if plan["generator"] == "planner-agent"
        )
        actions = [task["action_type"] for task in candidate["tasks"]]

        assert len(actions) == len(set(actions))

    def test_no_dependency_dangles(self, client, incident, agent_modes):
        """A dependency on a dropped task is removed, never left permanently unsatisfiable."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        candidate = next(
            plan for plan in _plans(client, incident) if plan["generator"] == "planner-agent"
        )
        ids = {str(task["id"]) for task in candidate["tasks"]}

        for task in candidate["tasks"]:
            for dependency in task["depends_on"]:
                assert dependency in ids, f"dangling dependency {dependency}"

    def test_the_reflection_is_in_the_record(self, client, incident, agent_modes):
        """Kept, dropped and the reason code for each — in the timeline, not only in a log."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]

        agent_entry = next(
            entry
            for entry in entries
            if entry["event_type"] == "PLAN_PROPOSED"
            and entry["detail"].get("generator") == "planner-agent"
        )
        reflection = agent_entry["detail"]["reflection"]
        assert reflection["kept_actions"]
        assert reflection["rejected"] is False


class TestAModelFailureIsNeverFatal:
    def test_llm_off_produces_no_candidate_and_does_not_block(self, client, incident):
        """The default path. One plan, from the playbook, and the incident proceeds."""
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()
        generators = {plan["generator"] for plan in _plans(client, incident)}

        assert generators == {"fallback-playbook"}
        assert body["state"] != "failed"

    def test_the_gate_still_holds_the_high_risk_action(self, client, incident, agent_modes):
        """A model in the loop does not soften the gate."""
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()
        assert body["state"] == "awaiting_approval"

    def test_an_approval_is_still_attributed_to_a_person(self, client, incident, agent_modes):
        """Deliberately not asserting `resolved`.

        This fixture has no booking rows, so the notification correctly refuses with "nobody to
        notify" and the incident blocks — the same outcome the playbook reaches here, and the honest
        one. Inventing a recipient to make a test go green is the failure this system exists to
        avoid. Resolution on real data is covered by `scripts/verify_phase2.py`.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        evaluations = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()["evaluations"]
        pending = [e for e in evaluations if e["decision"] == "needs_human"]
        assert pending, "expected the gate to hold something"

        response = client.post(
            f"{PREFIX}/assurance/{pending[0]['id']}/decision",
            json={"decision": "approved", "reason": "candidate reviewed"},
        )
        assert response.status_code == 200

        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        human = [entry for entry in entries if entry["actor_kind"] == "human"]
        assert len(human) == 1


class TestModelSelfReportNeverControlsExecution:
    def test_every_executed_action_names_its_own_evaluation(self, client, incident, agent_modes):
        """Whatever the model reported about itself, the gate is what authorised each action."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        actions = client.get(f"{PREFIX}/incidents/{incident}").json()["actions"]

        assert actions, "no action executed"
        for action in actions:
            assert action["assurance_id"], action["action_type"]
