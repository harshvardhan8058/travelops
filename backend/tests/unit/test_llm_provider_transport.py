"""Which endpoint `live` mode actually calls, and what it records.

The live transport is configurable because Groq's free and developer tiers cap an account at
8000 tokens per minute, counted as `prompt_tokens + max_tokens`, and refused the explainer and
reporter outright. OpenRouter serves the same `openai/gpt-oss-120b` behind the same
OpenAI-compatible chat-completions contract, so the switch is a base URL, a key and a recorded
generator — not a second client and not a second request shape.

These tests pin that: one code path, the right endpoint, the right key, and a generator that
identifies the provider without any consumer branching on it.

Owner: Stream C.
"""

from __future__ import annotations

import json

import pytest

from app.agents.contract import ExplanationResponse
from app.config import LLMProvider, get_settings, provider_transport
from app.llm.client import LLMClient, LLMUnavailable

PAYLOAD = json.dumps(
    {
        "status": "success",
        "reason": "r",
        "evidence_refs": ["action:1"],
        "payload_type": "explanation.v1",
        "explanation": "The storm held the departure.",
        "citation_refs": ["action:check_connections:1"],
    }
)


class _Spy:
    """Records how the client was constructed and what it was asked to send."""

    def __init__(self) -> None:
        self.init_kwargs: dict = {}
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> None:
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
            completion_tokens = 200

        class _Resp:
            def __init__(self, c):
                self.choices = [_Choice(c)]
                self.usage = _Usage()

        class _Completions:
            async def create(self, **kwargs):
                spy.calls.append(kwargs)
                return _Resp(PAYLOAD)

        class _Chat:
            completions = _Completions()

        class _Fake:
            def __init__(self, **kwargs):
                spy.init_kwargs = kwargs
                self.chat = _Chat()

        monkeypatch.setattr(groq, "AsyncGroq", _Fake)


async def _call(client: LLMClient):
    return await client.call(
        prompt="explain",
        system="You are the Recovery Explainer. Reply with JSON.",
        response_schema=ExplanationResponse,
        agent_name="explainer",
        prompt_version="explainer.v1",
    )


@pytest.fixture
def openrouter(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def groq_provider(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestTheResolvedTransport:
    def test_openrouter_is_the_default_provider(self, monkeypatch):
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        get_settings.cache_clear()
        try:
            assert provider_transport(get_settings()).provider is LLMProvider.openrouter
        finally:
            get_settings.cache_clear()

    def test_openrouter_resolves_to_the_openai_compatible_endpoint(self, openrouter):
        transport = provider_transport(get_settings())
        assert transport.base_url == "https://openrouter.ai/api/v1"
        assert transport.model == "openai/gpt-oss-120b"
        assert transport.key_env_var == "OPENROUTER_API_KEY"
        assert transport.generator == "openrouter:openai/gpt-oss-120b"

    def test_groq_still_resolves_and_keeps_its_own_key(self, groq_provider):
        transport = provider_transport(get_settings())
        assert transport.base_url is None, "the Groq SDK default host is correct for Groq"
        assert transport.key_env_var == "GROQ_API_KEY"
        assert transport.generator.startswith("groq:")

    def test_each_provider_carries_its_own_ceiling(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        get_settings.cache_clear()
        groq_limit = provider_transport(get_settings()).tpm_limit
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        get_settings.cache_clear()
        openrouter_limit = provider_transport(get_settings()).tpm_limit
        get_settings.cache_clear()
        assert groq_limit == 8000, "the tier that caused the 413s"
        assert openrouter_limit > groq_limit


class TestTheRequestGoesToOpenRouter:
    async def test_the_client_is_pointed_at_the_openrouter_base_url(self, openrouter, monkeypatch):
        spy = _Spy()
        spy.install(monkeypatch)

        await _call(LLMClient())

        assert spy.init_kwargs["base_url"] == "https://openrouter.ai/api/v1"
        assert spy.init_kwargs["api_key"] == "or-test-key"

    async def test_the_openrouter_attribution_headers_are_sent(self, openrouter, monkeypatch):
        spy = _Spy()
        spy.install(monkeypatch)

        await _call(LLMClient())

        headers = spy.init_kwargs.get("default_headers") or {}
        assert "HTTP-Referer" in headers
        assert headers.get("X-Title") == "TravelOps AI"

    async def test_the_request_shape_is_unchanged(self, openrouter, monkeypatch):
        """The whole point of an OpenAI-compatible endpoint: same body, different host."""
        spy = _Spy()
        spy.install(monkeypatch)

        await _call(LLMClient())

        sent = spy.calls[-1]
        assert sent["model"] == "openai/gpt-oss-120b"
        assert sent["response_format"] == {"type": "json_object"}
        assert [m["role"] for m in sent["messages"]] == ["system", "user"]
        assert isinstance(sent["max_tokens"], int)
        assert "temperature" in sent

    async def test_the_audit_records_the_provider_in_the_generator(self, openrouter, monkeypatch):
        spy = _Spy()
        spy.install(monkeypatch)

        _response, audit = await _call(LLMClient())

        assert audit.generator == "openrouter:openai/gpt-oss-120b"
        assert audit.prompt_version == "explainer.v1"

    async def test_no_base_url_is_forced_when_the_provider_is_groq(
        self, groq_provider, monkeypatch
    ):
        spy = _Spy()
        spy.install(monkeypatch)

        await _call(LLMClient())

        assert "base_url" not in spy.init_kwargs
        assert "default_headers" not in spy.init_kwargs


class TestTheGeneratorStaysReadableToConsumers:
    def test_an_openrouter_generator_reads_as_live_not_fixture(self):
        """`_source_of` keys on the `fixture:` prefix only, so a new prefix must read as live."""
        from app.api.reasoning import _source_of

        assert _source_of("openrouter:openai/gpt-oss-120b") == "live"
        assert _source_of("groq:openai/gpt-oss-120b") == "live"
        assert _source_of("fixture:explainer") == "fixture"

    def test_assurance_records_the_generator_without_branching_on_it(self):
        """A new prefix must not change how a model-authored proposal is treated."""
        from app.assurance.authorship import ProposalAuthorship

        openrouter = ProposalAuthorship.from_model("openrouter:openai/gpt-oss-120b")
        groq = ProposalAuthorship.from_model("groq:openai/gpt-oss-120b")
        assert openrouter.authored_by == groq.authored_by
        assert openrouter.generator == "openrouter:openai/gpt-oss-120b"


class TestTheKeyIsProviderSpecific:
    async def test_a_missing_openrouter_key_names_openrouter(self, monkeypatch):
        monkeypatch.setenv("LLM_MODE", "live")
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "")
        monkeypatch.setenv("GROQ_API_KEY", "a-groq-key-that-must-not-be-used")
        get_settings.cache_clear()
        try:
            with pytest.raises(LLMUnavailable, match="OPENROUTER_API_KEY"):
                await _call(LLMClient())
        finally:
            get_settings.cache_clear()

    def test_the_openrouter_key_is_redacted_from_logs(self):
        from app.observability.logging import _SENSITIVE_KEYS

        assert "openrouter_api_key" in _SENSITIVE_KEYS


class TestThereIsStillOneClient:
    def test_no_second_transport_module_appeared(self):
        """A second client is how two retry policies and two request shapes start to drift."""
        from pathlib import Path

        llm_dir = Path(__file__).resolve().parents[2] / "app" / "llm"
        modules = sorted(p.name for p in llm_dir.glob("*.py") if p.name != "__init__.py")
        assert modules == ["client.py"], f"unexpected transport modules: {modules}"

    def test_only_the_client_imports_the_sdk(self):
        """The existing guard, restated here because a new provider is the moment it slips."""
        import ast
        from pathlib import Path

        app_dir = Path(__file__).resolve().parents[2] / "app"
        offenders = []
        for path in app_dir.rglob("*.py"):
            if path.name == "client.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(n.split(".")[0] in {"groq", "openai"} for n in names):
                    offenders.append(str(path.relative_to(app_dir)))
        assert offenders == [], f"a second model call path exists: {sorted(set(offenders))}"
