"""Request sizing against the account's tokens-per-minute ceiling.

Groq bills a request against TPM as `prompt_tokens + max_tokens` — the RESERVED completion
budget, not what comes back — and answers `HTTP 413 rate_limit_exceeded` when one request
exceeds it. On an 8000 TPM account the real Windows logs showed:

    explainer  prompt  710 + max_tokens 8192 = 8902 requested   413
    reporter   prompt 1395 + max_tokens 8192 = 9587 requested   413

`max_tokens=8192` was therefore 85-92% of an unsatisfiable request, and no prompt length could
have rescued it. It was introduced to stop the reporter truncating, which fixed truncation by
making the call impossible.

Nothing local could see it: fixture mode never sizes a request, and every other test stubs the
transport, which accepts any ceiling. `test_no_agent_can_exceed_the_ceiling` below is the guard
that closes that gap — it fails if any agent's prompt plus its reservation crosses the limit.

Owner: Stream C.
"""

from __future__ import annotations

import httpx
import pytest
from groq import APIStatusError

from app.agents import explainer, reporter
from app.agents.contract import ExplanationResponse
from app.config import get_settings
from app.llm.client import (
    MAX_RETRIES,
    MIN_OUTPUT_BUDGET,
    TPM_SAFETY_MARGIN,
    LLMClient,
    LLMUnavailable,
    _estimate_prompt_tokens,
    _output_budget,
)

TPM = 8000


class TestTheBudgetArithmetic:
    def test_a_request_never_reserves_more_than_the_ceiling_allows(self):
        granted = _output_budget(requested=8192, prompt_tokens=1395, tpm_limit=TPM)
        assert 1395 + granted <= TPM - TPM_SAFETY_MARGIN + 1

    def test_the_two_observed_413s_would_now_fit(self):
        """The exact numbers from the Windows logs."""
        for prompt_tokens, observed in ((710, 8902), (1395, 9587)):
            assert prompt_tokens + 8192 == observed  # reproduces the refused request
            granted = _output_budget(requested=8192, prompt_tokens=prompt_tokens, tpm_limit=TPM)
            assert prompt_tokens + granted < TPM

    def test_a_modest_request_is_left_alone(self):
        assert _output_budget(requested=1400, prompt_tokens=900, tpm_limit=TPM) == 1400

    def test_a_larger_tier_gets_the_full_request(self):
        assert _output_budget(requested=8192, prompt_tokens=1395, tpm_limit=30000) == 8192

    def test_an_enormous_prompt_yields_no_budget_rather_than_a_negative_one(self):
        assert _output_budget(requested=1400, prompt_tokens=99999, tpm_limit=TPM) == 0

    def test_the_estimate_is_pessimistic_against_the_measured_prompts(self):
        """It must over-estimate, because it is used to hold a ceiling down.

        The real tokeniser reported 710 for the explainer prompt; a cheap chars/4 guess would
        have said far less and clamped too generously.
        """
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")
        assert _estimate_prompt_tokens(system, "") > len(system) / 4


class TestNoAgentCanExceedTheCeiling:
    """The guard. If this fails, a live call will come back 413."""

    @pytest.mark.parametrize(
        ("name", "module", "budget"),
        [
            ("explainer", explainer, explainer.MAX_TOKENS),
            ("reporter", reporter, reporter.MAX_TOKENS),
        ],
    )
    def test_no_agent_can_exceed_the_ceiling(self, name: str, module, budget: int):
        system = module.PROMPT_PATH.read_text(encoding="utf-8")
        # A deliberately padded user prompt: long recorded reasons, a full action list.
        padded = "\n".join(f"- action_{i}: success | {'x' * 200}" for i in range(20))
        estimate = _estimate_prompt_tokens(system, padded)
        granted = _output_budget(requested=budget, prompt_tokens=estimate, tpm_limit=TPM)
        assert granted >= MIN_OUTPUT_BUDGET, f"{name} has no room to answer"
        assert estimate + granted <= TPM, f"{name} would request {estimate + granted} against {TPM}"

    def test_the_planner_default_also_fits(self):
        from app.agents import planner
        from app.llm.client import DEFAULT_MAX_TOKENS

        system = planner.PROMPT_PATH.read_text(encoding="utf-8")
        estimate = _estimate_prompt_tokens(system, "x" * 2000)
        granted = _output_budget(
            requested=DEFAULT_MAX_TOKENS, prompt_tokens=estimate, tpm_limit=TPM
        )
        assert estimate + granted <= TPM
        assert granted >= MIN_OUTPUT_BUDGET

    def test_the_reservations_are_all_smaller_than_the_ceiling_on_their_own(self):
        """A reservation at or above the ceiling can never be served, whatever the prompt."""
        from app.llm.client import DEFAULT_MAX_TOKENS

        for budget in (DEFAULT_MAX_TOKENS, explainer.MAX_TOKENS, reporter.MAX_TOKENS):
            assert budget < TPM - TPM_SAFETY_MARGIN


# ------------------------------------------------------------------ the live request itself


class _Spy:
    """Records the kwargs of each call; optionally raises for the first N calls."""

    def __init__(self, error: Exception | None = None, fail_times: int = 0) -> None:
        self.calls: list[dict] = []
        self.error = error
        self.fail_times = fail_times

    def install(self, monkeypatch) -> None:
        import json as _json

        import groq

        spy = self

        class _Msg:
            def __init__(self, c):
                self.content = c
                self.reasoning = None

        class _Choice:
            def __init__(self, c):
                self.message = _Msg(c)

        class _Usage:
            prompt_tokens = 700
            completion_tokens = 300

        class _Resp:
            def __init__(self, c):
                self.choices = [_Choice(c)]
                self.usage = _Usage()

        payload = _json.dumps(
            {
                "status": "success",
                "reason": "r",
                "evidence_refs": ["action:1"],
                "payload_type": "explanation.v1",
                "explanation": "Bengaluru storm recovery explained.",
                "citation_refs": ["action:check_connections:1"],
            }
        )

        class _Completions:
            async def create(self, **kwargs):
                spy.calls.append(kwargs)
                if spy.error is not None and len(spy.calls) <= spy.fail_times:
                    raise spy.error
                return _Resp(payload)

        class _Chat:
            completions = _Completions()

        class _Fake:
            def __init__(self, api_key=None, **_):
                self.chat = _Chat()

        monkeypatch.setattr(groq, "AsyncGroq", _Fake)


def _status_error(status: int, message: str, code: str) -> APIStatusError:
    body = {"error": {"message": message, "type": "invalid_request_error", "code": code}}
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return APIStatusError(
        message, response=httpx.Response(status, json=body, request=request), body=body
    )


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    # Named explicitly: the 8000-token-per-minute ceiling these tests are about is the Groq
    # free/developer tier. OpenRouter is the default provider and has no equivalent cap.
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "stub-key")
    monkeypatch.setenv("GROQ_TPM_LIMIT", str(TPM))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _call(client: LLMClient, *, system: str, max_tokens: int):
    return await client.call(
        prompt="explain the recovery",
        system=system,
        response_schema=ExplanationResponse,
        agent_name="explainer",
        prompt_version="explainer.v1",
        max_tokens=max_tokens,
    )


class TestTheRequestActuallySent:
    async def test_the_sent_max_tokens_keeps_the_request_inside_the_ceiling(
        self, live, monkeypatch
    ):
        spy = _Spy()
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        await _call(LLMClient(), system=system, max_tokens=explainer.MAX_TOKENS)

        sent = spy.calls[-1]["max_tokens"]
        estimate = _estimate_prompt_tokens(system, "explain the recovery")
        assert estimate + sent <= TPM

    async def test_an_oversized_reservation_is_clamped_not_forwarded(self, live, monkeypatch):
        """The regression itself: 8192 must never reach the provider on an 8000 account."""
        spy = _Spy()
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        await _call(LLMClient(), system=system, max_tokens=8192)

        assert spy.calls[-1]["max_tokens"] < 8192
        assert (
            _estimate_prompt_tokens(system, "explain the recovery") + spy.calls[-1]["max_tokens"]
            <= TPM
        )

    async def test_a_prompt_with_no_room_to_answer_says_so_instead_of_being_sent(
        self, live, monkeypatch
    ):
        spy = _Spy()
        spy.install(monkeypatch)

        with pytest.raises(LLMUnavailable) as caught:
            await _call(LLMClient(), system="x" * 40000, max_tokens=1400)

        assert spy.calls == [], "an unanswerable request must not reach the provider"
        message = str(caught.value)
        assert "token-per-minute" in message
        assert str(TPM) in message

    async def test_a_bigger_tier_is_respected(self, live, monkeypatch):
        monkeypatch.setenv("GROQ_TPM_LIMIT", "30000")
        get_settings.cache_clear()
        spy = _Spy()
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        await _call(LLMClient(), system=system, max_tokens=8192)

        assert spy.calls[-1]["max_tokens"] == 8192


class TestATpmOverrunIsTransient:
    """413 `rate_limit_exceeded` clears on its own; treating it as permanent stopped the run."""

    async def test_a_413_is_retried(self, live, monkeypatch):
        monkeypatch.setattr("app.llm.client.RETRY_DELAY_SECONDS", 0)
        spy = _Spy(
            error=_status_error(413, "Request too large for TPM", "rate_limit_exceeded"),
            fail_times=1,
        )
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        response, _audit = await _call(LLMClient(), system=system, max_tokens=1400)

        assert len(spy.calls) == 2, "a 413 must be retried, not failed on the first attempt"
        assert response.explanation

    async def test_a_429_is_still_retried(self, live, monkeypatch):
        monkeypatch.setattr("app.llm.client.RETRY_DELAY_SECONDS", 0)
        spy = _Spy(error=_status_error(429, "rate limited", "rate_limit_exceeded"), fail_times=1)
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        await _call(LLMClient(), system=system, max_tokens=1400)
        assert len(spy.calls) == 2

    async def test_a_persistent_413_still_gives_up_after_the_retries(self, live, monkeypatch):
        monkeypatch.setattr("app.llm.client.RETRY_DELAY_SECONDS", 0)
        spy = _Spy(
            error=_status_error(413, "Request too large", "rate_limit_exceeded"), fail_times=99
        )
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        with pytest.raises(LLMUnavailable):
            await _call(LLMClient(), system=system, max_tokens=1400)
        assert len(spy.calls) == MAX_RETRIES + 1

    async def test_a_decommissioned_model_is_still_not_retried(self, live, monkeypatch):
        """The permanent class must stay permanent."""
        spy = _Spy(
            error=_status_error(400, "has been decommissioned", "model_decommissioned"),
            fail_times=99,
        )
        spy.install(monkeypatch)
        system = explainer.PROMPT_PATH.read_text(encoding="utf-8")

        with pytest.raises(LLMUnavailable):
            await _call(LLMClient(), system=system, max_tokens=1400)
        assert len(spy.calls) == 1
