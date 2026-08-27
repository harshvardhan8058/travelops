"""How the live client reacts to the provider refusing the request.

Groq decommissioned `llama-3.3-70b-versatile` on 2026-08-16 for free and developer tiers. Every
live call then returned HTTP 400 `model_decommissioned`, which took the whole Phase 3 live path
down at once: the planner produced no candidate (the orchestrator caught `LLMUnavailable` and
kept the playbook), so the model-authored plan and its replay frame were absent too, and both
prose endpoints failed.

The retry loop made it worse. A decommissioned model is permanent, but the client treated every
`APIError` as transient and tried three times, so the operator's evidence was "Groq call failed
after 3 attempts" rather than the provider's own first sentence naming the retired model. These
tests pin the distinction: 4xx that is not a rate limit is reported, 429 and 5xx are retried.

Owner: Stream C.
"""

from __future__ import annotations

import pytest

from app.agents.contract import ExplanationResponse
from app.config import get_settings
from app.llm.client import MAX_RETRIES, LLMClient, LLMUnavailable
from tests.llm_transport_stub import RecordingTransport

DECOMMISSIONED_BODY = {
    "error": {
        "message": (
            "The model `llama-3.3-70b-versatile` has been decommissioned and is no longer "
            "supported. Please refer to https://console.groq.com/docs/deprecations for a "
            "recommendation on which model to use instead."
        ),
        "type": "invalid_request_error",
        "code": "model_decommissioned",
    }
}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    # These assert against the Groq model id and its decommissioning message, so the provider
    # is pinned rather than inherited from the default.
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "stub-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _call(client: LLMClient):
    return await client.call(
        prompt="p",
        system="s",
        response_schema=ExplanationResponse,
        agent_name="explainer",
        prompt_version="explainer.v1",
    )


class TestAPermanentRefusalIsReportedNotRepeated:
    async def test_a_decommissioned_model_is_not_retried(self, live, monkeypatch):
        """Three attempts against a permanent 400 buys nothing and costs the diagnosis."""
        stub = (
            RecordingTransport()
            .fails_with_status(
                400, DECOMMISSIONED_BODY["error"]["message"], code="model_decommissioned"
            )
            .install(monkeypatch)
        )

        with pytest.raises(LLMUnavailable) as caught:
            await _call(LLMClient())

        assert stub.calls == 1, f"retried a permanent 400 {stub.calls} times"
        assert "decommissioned" in str(caught.value)

    async def test_the_message_names_the_model_and_the_status(self, live, monkeypatch):
        """What the operator sees has to be enough to act on without reading the logs."""
        RecordingTransport().fails_with_status(
            400, DECOMMISSIONED_BODY["error"]["message"], code="model_decommissioned"
        ).install(monkeypatch)

        with pytest.raises(LLMUnavailable) as caught:
            await _call(LLMClient())

        message = str(caught.value)
        assert "400" in message
        assert get_settings().groq_model in message
        assert "console.groq.com/docs/deprecations" in message

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_no_four_hundred_class_error_is_retried(self, live, monkeypatch, status: int):
        stub = RecordingTransport().fails_with_status(status, "refused").install(monkeypatch)

        with pytest.raises(LLMUnavailable):
            await _call(LLMClient())

        assert stub.calls == 1


class TestTransientFailuresStillRetry:
    """The fix must not have turned the retry loop off."""

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    async def test_a_rate_limit_or_server_error_uses_every_attempt(
        self, live, monkeypatch, status: int
    ):
        monkeypatch.setattr("app.llm.client.RETRY_DELAY_SECONDS", 0)
        stub = RecordingTransport().fails_with_status(status, "try later").install(monkeypatch)

        with pytest.raises(LLMUnavailable):
            await _call(LLMClient())

        assert stub.calls == MAX_RETRIES + 1

    async def test_a_timeout_still_retries(self, live, monkeypatch):
        monkeypatch.setattr("app.llm.client.RETRY_DELAY_SECONDS", 0)
        stub = (
            RecordingTransport().raises(TimeoutError("connection timed out")).install(monkeypatch)
        )

        with pytest.raises(LLMUnavailable):
            await _call(LLMClient())

        assert stub.calls == MAX_RETRIES + 1


class TestTheShippedModelIsOneGroqStillServes:
    """Guards against shipping a retired default again.

    The failure this file is about was not a code bug — the code was correct and the model
    underneath it was withdrawn. Nothing in the suite noticed, because every test either
    replays a fixture or stubs the transport, and a stub answers for any model id.
    """

    #: Shut down for free and developer tiers per https://console.groq.com/docs/deprecations
    RETIRED = frozenset(
        {
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "llama-3.1-70b-versatile",
            "llama3-70b-8192",
            "llama3-8b-8192",
            "qwen/qwen3-32b",
            "qwen-qwq-32b",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "meta-llama/llama-guard-4-12b",
            "moonshotai/kimi-k2-instruct",
            "moonshotai/kimi-k2-instruct-0905",
            "deepseek-r1-distill-llama-70b",
            "mixtral-8x7b-32768",
            "mistral-saba-24b",
            "gemma-7b-it",
            "gemma2-9b-it",
        }
    )

    def test_the_default_model_is_not_a_retired_one(self):
        model = get_settings().groq_model
        assert model not in self.RETIRED, (
            f"GROQ_MODEL default '{model}' was decommissioned by Groq. "
            "See https://console.groq.com/docs/deprecations"
        )

    def test_the_env_example_does_not_ship_a_retired_one(self):
        """An existing `.env` overrides the application default, so the example matters.

        The Windows container kept failing after the code default was correct, because its own
        `.env` still named the retired model.
        """
        from app.config import REPO_ROOT

        for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if line.startswith("GROQ_MODEL="):
                assert line.split("=", 1)[1].strip() not in self.RETIRED, line
