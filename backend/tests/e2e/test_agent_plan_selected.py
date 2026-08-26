"""What happens when an operator SELECTS the agent-authored plan.

`test_agent_planning.py` (Stream A) asserts the agent stays subordinate: the playbook is created
first, stays selected, and reflection narrows the candidate. This file covers the other half — the
case where a person chooses the model's plan — because that is the only path on which a
model-authored proposal actually reaches the Decision Assurance Gate.

It exists because that path was unverified and two things were wrong on it:

1. **Stream B's authorship assurance was inert.** #64 built `authorship_constraints` and
   `gate_requirements(authorship=...)`; nothing supplied the argument, so every call returned `[]`.
   A model could put `assurance_decision` in a payload, or cite a reference nobody recorded, and the
   gate had no reason to distrust it.
2. **The corroboration baseline was too narrow.** Once authorship was supplied, the fixture
   planner's citations — which are exactly the refs the orchestrator handed it — were all refused as
   uncorroborated, so the agent plan could never clear the gate.

Both are fixed. These tests pin the behaviour, and pin the constraint that remains: selecting the
agent plan once the deterministic tasks are outstanding is refused as a duplicate, which is correct.

Owner: Stream C (integration). The constraints are Stream B's; the planner seam is Stream A's.
"""

from __future__ import annotations

import pytest

from app.config import LLMMode, get_modes

PREFIX = "/api/v1"
PLAYBOOK = "fallback-playbook"


@pytest.fixture
def agent_modes(monkeypatch):
    """`LLM_MODE=fixture` for both the engine and the client. Mirrors Stream A's fixture.

    Duplicated rather than imported so this file does not break if Stream A restructures theirs.
    """
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

    from app.llm.client import LLMClient

    monkeypatch.setattr(
        "app.agents.planner.LLMClient", lambda **_kwargs: LLMClient(mode=LLMMode.fixture)
    )
    return stub


def _plans(client, incident) -> list[dict]:
    return client.get(f"{PREFIX}/incidents/{incident}/plans").json()["plans"]


def _agent_plan(client, incident) -> dict:
    agent = [p for p in _plans(client, incident) if p["generator"] != PLAYBOOK]
    assert agent, "no agent-authored candidate was produced"
    return agent[0]


def _evaluations(client, incident) -> list[dict]:
    return client.get(f"{PREFIX}/incidents/{incident}/assurance").json()["evaluations"]


def _blocking(evaluation: dict) -> list[str]:
    """`AssuranceEvaluationOut.blocking`, the field that actually exists.

    Named `blocking`, not `blocking_reasons` — the latter is the ORM column name. Reading the wrong
    one returns `None` for every evaluation, which makes an assertion about it pass vacuously. That
    happened here once already.
    """
    return list(evaluation.get("blocking") or [])


def _failed_check(evaluation: dict, name: str) -> dict | None:
    """The named check's result, when it did not pass."""
    for check in evaluation.get("checks") or []:
        if check.get("name") == name and str(check.get("state", "")).lower() in {
            "failed",
            "fail",
        }:
            return check
    return None


class TestTheAgentPlanIsAuthorisedByTheSameGate:
    def test_selecting_it_records_the_choice_against_a_person(self, client, incident, agent_modes):
        """Selection is a human act with an actor, on the existing seam. No new mechanism."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = _agent_plan(client, incident)

        response = client.post(
            f"{PREFIX}/incidents/{incident}/plans/{plan['id']}/select",
            json={"reason": "chose the agent plan", "actor_id": "operator-1"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["selected_plan_id"] == plan["id"]
        chosen = next(p for p in body["plans"] if p["id"] == plan["id"])
        assert chosen["selected_by"] == "operator-1"

    def test_its_tasks_are_evaluated_rather_than_executed_unassured(
        self, client, incident, agent_modes
    ):
        """The property that matters: no task of a model plan runs without its own evaluation.

        Whatever the gate decides, it must have decided. A model-authored task that advanced with
        `assurance_id` unset would be a second path to execution.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = _agent_plan(client, incident)
        client.post(
            f"{PREFIX}/incidents/{incident}/plans/{plan['id']}/select",
            json={"reason": "chose the agent plan", "actor_id": "operator-1"},
        )
        client.post(f"{PREFIX}/incidents/{incident}/run")

        driving = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]
        assert driving["generator"] != PLAYBOOK, "the selected agent plan must be the driving plan"

        advanced = [t for t in driving["tasks"] if t["state"] != "proposed"]
        assert advanced, "the agent's tasks must reach the gate"
        for task in advanced:
            assert task["assurance_id"] is not None, (
                f"task {task['id']} advanced with no evaluation — a second path to execution"
            )

    def test_a_well_behaved_model_is_not_refused_for_its_citations(
        self, client, incident, agent_modes
    ):
        """The corroboration-baseline defect, pinned.

        The fixture planner cites the refs `_target_refs` handed it. With only the delay-risk
        evidence as the baseline, every one was refused as `authorship.uncorroborated_evidence` and
        the agent plan was unexecutable. A check that fires on correct behaviour is noise, and noise
        gets ignored — which is worse than no check, because the one time it matters nobody looks.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = _agent_plan(client, incident)
        client.post(
            f"{PREFIX}/incidents/{incident}/plans/{plan['id']}/select",
            json={"reason": "chose the agent plan", "actor_id": "operator-1"},
        )
        client.post(f"{PREFIX}/incidents/{incident}/run")

        offenders = []
        for ev in _evaluations(client, incident):
            failed = _failed_check(ev, "policy_compliant")
            if failed and "uncorroborated_evidence" in str(failed):
                offenders.append((ev["id"], failed.get("reason")))

        assert not offenders, (
            "the agent plan was refused for citing references the orchestrator itself supplied, "
            f"so the corroboration baseline has regressed: {offenders}"
        )


class TestTheGateStillRefusesWhatItShould:
    def test_a_duplicate_action_is_refused_not_double_booked(self, client, incident, agent_modes):
        """A stated property of main's design, not a surprise.

        The agent candidate is created during the same run that assures the deterministic plan, so
        by the time a person can see it the playbook's `check_connections` is already outstanding.
        Selecting the agent plan then collides and `no_conflicts` refuses it with DUPLICATE_ACTION.

        Correct — two plans booking the same work on one flight is what that check exists to stop —
        but it means the agent plan is a real alternative only before execution begins. Pinned here
        so nobody reads the refusal as a bug.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = _agent_plan(client, incident)
        client.post(
            f"{PREFIX}/incidents/{incident}/plans/{plan['id']}/select",
            json={"reason": "late selection", "actor_id": "operator-1"},
        )
        client.post(f"{PREFIX}/incidents/{incident}/run")

        conflicted = [
            ev
            for ev in _evaluations(client, incident)
            if _failed_check(ev, "no_conflicts") is not None
        ]
        assert conflicted, (
            "selecting a second plan that proposes an already-outstanding action must be refused "
            "by no_conflicts, not silently double-booked"
        )
        for ev in conflicted:
            assert "no_conflicts" in _blocking(ev)
            # A conflict is not approvable: an operator cannot agree a double booking into being.
            assert ev["decision"] != "execute"
            assert "DUPLICATE_ACTION" in str(_failed_check(ev, "no_conflicts"))

    def test_the_high_risk_action_still_needs_a_person(self, client, incident, agent_modes):
        """Authorship changes what may be asserted. It does not change who must approve."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        held = [
            ev
            for ev in _evaluations(client, incident)
            if ev["decision"] == "needs_human" and ev.get("risk_tier") == "high"
        ]
        assert held, "a high-risk action must still require its own human decision"


class TestPhase2ParityIsPreserved:
    def test_a_deterministic_plan_is_never_refused_on_authorship(self, client, incident):
        """No `agent_modes`, so `LLM_MODE=off` and every plan is the playbook's.

        Supplying authorship must be invisible for a deterministic proposal. If this ever fails, the
        frozen gate has changed behaviour for Phase 1 and Phase 2 paths.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")

        generators = {p["generator"] for p in _plans(client, incident)}
        assert generators == {PLAYBOOK}

        for ev in _evaluations(client, incident):
            failed = _failed_check(ev, "policy_compliant")
            assert not (failed and "authorship" in str(failed)), (
                f"a deterministic proposal was refused on authorship grounds: {failed}"
            )
