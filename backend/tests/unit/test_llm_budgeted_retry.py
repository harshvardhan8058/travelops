"""A caller-supplied budget must bound the live call from the INSIDE.

The Phase 3 primary planner failure was a budget inversion. The orchestrator bounded the planner
with `asyncio.wait_for(..., 40s)` while the client's own per-attempt ceiling was 60s, so a healthy
call that simply took a while was cancelled mid-flight: the primary incident was never allowed one
complete provider attempt, let alone a retry. Cancelling from outside cannot make a provider
faster — it can only destroy work in progress and then report the caller's timer instead of the
provider's behaviour.

`LLMClient.call(budget_seconds=...)` fixes the shape of the problem rather than the size of a
number. Attempts are sized to the time actually remaining, and a retry is started only when a real
attempt still fits, so every outcome is a true one: an answer, a named provider failure, or an
explicit statement that the budget ran out.

`budget_seconds=None` must remain byte-for-byte the old behaviour, because the explainer and
reporter rely on it.

Owner: Stream C.
"""

from __future__ import annotations

import json

import pytest

from app.agents.contract import PlannerResponse
from app.config import get_settings
from app.llm.client import (
    MIN_ATTEMPT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    LLMClient,
    LLMUnavailable,
)
from tests.llm_transport_stub import RecordingTransport

PLANNER_JSON = json.dumps(
    {
        "status": "success",
        "reason": "Protect threatened connections before notifying passengers.",
        "evidence_refs": ["incident:INC-2026-0820-VOBL-01"],
        "tasks": [
            {
                "action": "check_connections",
                "target_refs": ["incident:INC-2026-0820-VOBL-01"],
                "inputs": {},
                "depends_on": [],
            }
        ],
    }
)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _plan(client: LLMClient, **kwargs):
    return await client.call(
        prompt="p",
        system="s",
        response_schema=PlannerResponse,
        agent_name="planner",
        prompt_version="planner.v1",
        **kwargs,
    )


class TestTheDefaultPathIsUnchanged:
    async def test_no_budget_keeps_the_full_per_attempt_ceiling(self, live, monkeypatch):
        """The prose agents pass no budget and must keep the 60-second attempt they had."""
        stub = RecordingTransport().returns(PLANNER_JSON).install(monkeypatch)

        await _plan(LLMClient())

        assert stub.timeouts == [REQUEST_TIMEOUT_SECONDS]

    async def test_no_budget_still_spends_every_retry_on_a_transient_failure(
        self, live, monkeypatch
    ):
        """Unbudgeted callers keep all three attempts. Nothing about retry policy changed."""
        stub = (
            RecordingTransport()
            .fails_with_status(429, "slow down", code="rate_limit_exceeded")
            .install(monkeypatch)
        )

        with pytest.raises(LLMUnavailable) as caught:
            await _plan(LLMClient())

        assert stub.calls == 3
        assert caught.value.phase != "budget_exhausted"


class TestAnAttemptIsSizedToWhatIsLeft:
    async def test_a_budget_under_the_ceiling_shortens_the_attempt(self, live, monkeypatch):
        """A 30s budget must not arm a 60s attempt it can never honour."""
        stub = RecordingTransport().returns(PLANNER_JSON).install(monkeypatch)

        await _plan(LLMClient(), budget_seconds=30.0)

        assert len(stub.timeouts) == 1
        assert 0 < stub.timeouts[0] <= 30.0

    async def test_a_budget_above_the_ceiling_does_not_lengthen_it(self, live, monkeypatch):
        """The primary's 75s allowance still issues a 60s attempt — with room left to retry.

        This is the property the primary incident needed and did not have: one whole provider
        attempt fits inside the allowance instead of being cut short by it.
        """
        stub = RecordingTransport().returns(PLANNER_JSON).install(monkeypatch)

        await _plan(LLMClient(), budget_seconds=75.0)

        assert stub.timeouts == [REQUEST_TIMEOUT_SECONDS]


class TestRetriesFitTheBudgetRatherThanBeingCancelled:
    async def test_a_transient_failure_is_retried_and_succeeds_inside_the_budget(
        self, live, monkeypatch
    ):
        """The case the old outer timeout could not express: fail fast, retry, succeed.

        A 429 arrives in milliseconds, so a 75-second allowance has ample room for a second
        attempt. Under the previous design the retry was possible only by luck, because the outer
        timer was smaller than a single attempt's ceiling.
        """
        stub = (
            RecordingTransport()
            .fails_with_status(429, "slow down", code="rate_limit_exceeded", times=1)
            .returns(PLANNER_JSON)
            .install(monkeypatch)
        )

        response, audit = await _plan(LLMClient(), budget_seconds=75.0)

        assert stub.calls == 2
        assert response.tasks
        assert audit.generator.startswith("openrouter:")

    async def test_a_retry_that_cannot_fit_is_refused_rather_than_started(self, live, monkeypatch):
        """Better to say the budget ran out than to issue a request guaranteed to time out.

        A sub-second attempt exists only to satisfy a retry counter. It cannot succeed, and when it
        fails the provider gets blamed for the caller's arithmetic.
        """
        stub = (
            RecordingTransport()
            .fails_with_status(429, "slow down", code="rate_limit_exceeded")
            .install(monkeypatch)
        )

        with pytest.raises(LLMUnavailable) as caught:
            await _plan(LLMClient(), budget_seconds=MIN_ATTEMPT_SECONDS + 0.5)

        assert stub.calls == 1, "a retry was started that the budget could not fund"
        assert caught.value.phase == "budget_exhausted"
        # The provider's own status survives, because that is the diagnostic half that helps.
        assert caught.value.status_code == 429
        assert "slow down" in str(caught.value)

    async def test_a_permanent_refusal_is_still_reported_as_itself_under_a_budget(
        self, live, monkeypatch
    ):
        """A budget must not relabel a 400. The retry policy is untouched by it."""
        stub = (
            RecordingTransport()
            .fails_with_status(400, "no such model", code="model_not_found")
            .install(monkeypatch)
        )

        with pytest.raises(LLMUnavailable) as caught:
            await _plan(LLMClient(), budget_seconds=75.0)

        assert stub.calls == 1
        assert caught.value.phase == "provider_status"
        assert caught.value.status_code == 400


class TestABudgetTooSmallToUseIsSaidPlainly:
    async def test_no_request_is_issued_at_all(self, live, monkeypatch):
        """No fabricated provider timeout, and no wasted call."""
        stub = RecordingTransport().returns(PLANNER_JSON).install(monkeypatch)

        with pytest.raises(LLMUnavailable) as caught:
            await _plan(LLMClient(), budget_seconds=MIN_ATTEMPT_SECONDS / 2)

        assert stub.calls == 0
        assert caught.value.phase == "budget_exhausted"
        assert "no attempt completed" in str(caught.value)
