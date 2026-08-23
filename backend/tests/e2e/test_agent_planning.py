"""The agent path over the real app: a model plans, and nothing else changes.

Phase 2's guarantees are the point of this file. With an agent producing the plan, the gate still
authorises every task and a person still approves high risk: the model changed the *order of work*,
not who may do it.

Owner: Stream A.
"""

from __future__ import annotations

import pytest

from app.config import LLMMode, get_modes

PREFIX = "/api/v1"


@pytest.fixture
def agent_modes(monkeypatch):
    """Run the app in `LLM_MODE=fixture`: the agent path, no network."""
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
    monkeypatch.setattr("app.llm.client.get_modes", lambda: stub)
    return stub


class TestAgentProducedPlan:
    def test_the_plan_records_the_agent_and_its_prompt_version(self, client, incident, agent_modes):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        assert plan["generator"] == "planner-agent"
        assert plan["prompt_version"] == "planner.v1"

    def test_the_model_self_report_is_recorded_but_did_not_authorise_anything(
        self, client, incident, agent_modes
    ):
        """It is on the plan and nowhere in the decision path — that is the whole point of it."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}").json()

        assert body["plan"]["model_self_report"] == 82
        for action in body["actions"]:
            assert action["assurance_id"], "an action executed without an evaluation"

    def test_an_action_with_no_service_never_reaches_the_plan(self, client, incident, agent_modes):
        """The fixture proposes `reassign_gate`; reflection drops it before persistence."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        assert "reassign_gate" not in [task["action_type"] for task in plan["tasks"]]

    def test_the_drop_is_in_the_record_not_just_in_a_log(self, client, incident, agent_modes):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]

        proposed = next(e for e in entries if e["event_type"] == "PLAN_PROPOSED")
        agent = proposed["detail"]["agent"]
        assert "reassign_gate" in agent["dropped_actions"]
        assert any(f["code"] == "NO_REGISTERED_SERVICE" for f in agent["findings"])

    def test_the_gate_still_holds_the_high_risk_action(self, client, incident, agent_modes):
        """A model ordering the work does not soften the gate."""
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()
        assert body["state"] == "awaiting_approval"

    def test_an_approval_on_an_agent_plan_is_attributed_the_same_way(
        self, client, incident, agent_modes
    ):
        """The approval model does not change because a model wrote the plan.

        Deliberately not asserting `resolved`: this fixture has no booking rows, so the
        notification correctly refuses with "nobody to notify" and the incident blocks. That is the
        same outcome the playbook path reaches here, and it is the honest one — inventing a
        recipient to make a test go green is the failure this system exists to avoid. Resolution on
        real data is covered by `scripts/verify_phase2.py`.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        evaluations = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()["evaluations"]
        pending = [e for e in evaluations if e["decision"] == "needs_human"]
        assert pending, "expected the gate to hold something"

        response = client.post(
            f"{PREFIX}/assurance/{pending[0]['id']}/decision",
            json={"decision": "approved", "reason": "agent plan reviewed"},
        )
        assert response.status_code == 200

        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        human = [e for e in entries if e["actor_kind"] == "human"]
        assert len(human) == 1, "the operator decision must read as a person's act"

    def test_every_task_carries_a_resolved_dependency_or_none(self, client, incident, agent_modes):
        """Reflection resolves action-name dependencies to persisted ids, never a dangling name."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        ids = {str(task["id"]) for task in plan["tasks"]}
        for task in plan["tasks"]:
            for dependency in task["depends_on"]:
                assert dependency in ids, f"dangling dependency {dependency}"
