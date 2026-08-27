"""Why the planner alone lost its candidate on live traffic.

Live verification after the URL fix: explanation PASS, report PASS, both with
`generator=openrouter:openai/gpt-oss-120b` — so transport, auth, model and JSON mode were all
working — and the planner FAIL. Four things differ between the planner and the prose agents:

    contract     PlannerResponse/PlanTask are extra="forbid"; prose are extra="ignore"
    reason cap   planner 2000 characters; prose 20000
    budget       planner max_tokens 1200; prose 1400 and 1800
    reflection   planner output passes through reflect(); prose has no such stage

Driving the real agents over the real client with one response shape at a time isolated it:
budget and truncation break BOTH agents, which contradicts prose passing, so they are not the
cause. An undeclared key breaks the planner ONLY. That asymmetry was introduced when the two
prose responses were relaxed to stop them returning 500 on chatty model output and the planner
was left strict.

The fix keeps the contract strict and drops only keys nothing reads, recording their names —
which is what `contract.py` already asks for: "`confidence` is absent by design. If a model
emits one, store it as `ModelCallAudit.model_self_report` and never branch on it."

Owner: Stream C.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.agents.contract import PlannerResponse
from app.config import get_settings
from app.llm.client import LLMClient, LLMUnavailable
from tests.llm_transport_stub import RecordingTransport

REFS = ["incident:INC-2026-0820-VOBL-01", "flight:1"]


def _plan(**overrides) -> dict:
    payload = {
        "status": "success",
        "reason": "Weather disruption at VOBL. Protect time-sensitive connections first.",
        "evidence_refs": REFS,
        "payload_type": "planner.v1",
        "tasks": [
            {"action": "check_connections", "target_refs": REFS, "inputs": {}, "depends_on": []},
            {"action": "assess_crew_impact", "target_refs": REFS, "inputs": {}, "depends_on": []},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _propose(content: str, monkeypatch, *, finish_reason: str = "stop"):
    stub = RecordingTransport().returns(content).install(monkeypatch)
    if finish_reason != "stop":
        _force_finish_reason(monkeypatch, finish_reason, content)
    parsed, audit = await LLMClient().call(
        prompt="plan the recovery",
        system="You are the Recovery Planner. Return JSON.",
        response_schema=PlannerResponse,
        agent_name="planner",
        prompt_version="planner.v1",
    )
    return parsed, audit, stub


def _force_finish_reason(monkeypatch, finish_reason: str, content: str) -> None:
    """The stub's success envelope with `finish_reason` set, which is what truncation looks like."""
    import httpx

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, url, *, headers, json, **k):
            body = {
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": finish_reason,
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 1200},
            }
            return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


class TestADecoratedProposalStillProducesACandidate:
    """The planner-only failure. Each of these cost the whole candidate before the fix."""

    async def test_a_confidence_score_at_the_top_level_is_dropped_not_fatal(
        self, live, monkeypatch
    ):
        parsed, _audit, _stub = await _propose(json.dumps(_plan(confidence=0.93)), monkeypatch)

        assert len(parsed.tasks) == 2
        assert not hasattr(parsed, "confidence")

    async def test_an_undeclared_key_inside_a_task_is_dropped_not_fatal(self, live, monkeypatch):
        plan = _plan()
        plan["tasks"][0]["rationale"] = "connections are least recoverable"

        parsed, _audit, _stub = await _propose(json.dumps(plan), monkeypatch)

        assert [t.action.value for t in parsed.tasks] == [
            "check_connections",
            "assess_crew_impact",
        ]
        assert not hasattr(parsed.tasks[0], "rationale")

    async def test_several_undeclared_keys_at_once_are_all_dropped(self, live, monkeypatch):
        parsed, _audit, _stub = await _propose(
            json.dumps(_plan(confidence=0.9, certainty="high", notes="n")), monkeypatch
        )
        assert len(parsed.tasks) == 2


class TestTheContractItselfIsUnchanged:
    """Strict where it matters. None of this is relaxed by the transport-level tolerance."""

    def test_a_confidence_score_still_cannot_enter_the_contract(self):
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(_plan(confidence=91))

    @pytest.mark.parametrize(
        "action", ["wire_money", "delete_bookings", "reserve_hotel", "NOTIFY_PASSENGERS", ""]
    )
    async def test_an_invented_action_is_still_refused(self, live, monkeypatch, action: str):
        plan = _plan()
        plan["tasks"][0]["action"] = action

        with pytest.raises(LLMUnavailable):
            await _propose(json.dumps(plan), monkeypatch)

    async def test_an_invented_action_is_refused_even_when_a_decorative_key_is_present(
        self, live, monkeypatch
    ):
        """All-or-nothing: dropping decoration must not rescue a payload that is also wrong."""
        plan = _plan(confidence=0.9)
        plan["tasks"][0]["action"] = "wire_money"

        with pytest.raises(LLMUnavailable):
            await _propose(json.dumps(plan), monkeypatch)

    async def test_an_empty_task_list_is_still_refused(self, live, monkeypatch):
        with pytest.raises(LLMUnavailable):
            await _propose(json.dumps(_plan(tasks=[])), monkeypatch)

    async def test_a_blank_target_ref_is_still_refused(self, live, monkeypatch):
        plan = _plan()
        plan["tasks"][0]["target_refs"] = ["  "]

        with pytest.raises(LLMUnavailable):
            await _propose(json.dumps(plan), monkeypatch)

    async def test_a_wrong_payload_type_is_still_refused(self, live, monkeypatch):
        with pytest.raises(LLMUnavailable):
            await _propose(json.dumps(_plan(payload_type="explanation.v1")), monkeypatch)

    async def test_an_over_long_reason_is_still_refused(self, live, monkeypatch):
        """Deliberately left strict.

        The prompt now bounds `reason` to one sentence under 300 characters. The cap is not
        relaxed, so if a live model ever overruns it the new log line names the field rather than
        the plan quietly disappearing.
        """
        with pytest.raises(LLMUnavailable, match="reason"):
            await _propose(json.dumps(_plan(reason="R" * 2500)), monkeypatch)


class TestAFailureNowNamesItself:
    """`errors=N` alone is why this needed a second investigation round."""

    async def test_the_message_names_the_offending_field(self, live, monkeypatch):
        plan = _plan()
        plan["tasks"][0]["action"] = "wire_money"

        with pytest.raises(LLMUnavailable) as caught:
            await _propose(json.dumps(plan), monkeypatch)

        assert "tasks.0.action" in str(caught.value)

    async def test_truncation_is_reported_as_truncation_not_as_bad_json(self, live, monkeypatch):
        """A budget overrun and a malformed answer were the same log line before."""
        with pytest.raises(LLMUnavailable, match="truncated"):
            await _propose("", monkeypatch, finish_reason="length")

    async def test_truncation_names_the_budget_it_hit(self, live, monkeypatch):
        with pytest.raises(LLMUnavailable, match="max_tokens="):
            await _propose(json.dumps(_plan())[:120], monkeypatch, finish_reason="length")


class TestTheProseAgentsAreUnaffected:
    async def test_an_explanation_still_tolerates_decoration(self, live, monkeypatch):
        from app.agents.contract import ExplanationResponse

        RecordingTransport().returns(
            json.dumps(
                {
                    "status": "success",
                    "reason": "r",
                    "evidence_refs": ["action:1"],
                    "payload_type": "explanation.v1",
                    "explanation": "The storm held the departure.",
                    "citation_refs": ["action:check_connections:1"],
                    "confidence": 0.9,
                }
            )
        ).install(monkeypatch)

        parsed, _audit = await LLMClient().call(
            prompt="p",
            system="You are the Recovery Explainer. Return JSON.",
            response_schema=ExplanationResponse,
            agent_name="explainer",
            prompt_version="explainer.v1",
        )
        assert parsed.explanation


class TestThePlannerPromptForbidsWhatTheContractForbids:
    """The gap that let this happen: the contract was stricter than the prompt asked for."""

    def test_the_prompt_forbids_fields_outside_the_schema(self):
        from app.agents.planner import PROMPT_PATH

        text = PROMPT_PATH.read_text(encoding="utf-8").lower()
        assert "not in the schema" in text or "not in the schema above" in text
        assert "confidence" in text

    def test_the_prompt_bounds_the_reason_field(self):
        from app.agents.planner import PROMPT_PATH

        assert "reason" in PROMPT_PATH.read_text(encoding="utf-8").lower()

    def test_the_prompt_warns_against_copying_the_example_ids(self):
        """Reflection drops a task whose refs the orchestrator did not supply."""
        from app.agents.planner import PROMPT_PATH

        text = PROMPT_PATH.read_text(encoding="utf-8").lower()
        assert "placeholder" in text
