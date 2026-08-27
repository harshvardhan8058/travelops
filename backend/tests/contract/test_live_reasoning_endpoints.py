"""`/explanation` and `/reports` under `LLM_MODE=live`, against real Postgres.

Both returned 500 on Windows with a real Groq key while planner, reflection, assurance,
execution and replay all passed. The planner survives because the orchestrator catches
`LLMUnavailable` and keeps the deterministic playbook plan (`engine.py`), so a live planner
failure is journalled rather than surfaced. These two endpoints had no such handling: any live
failure escaped as a bare 500.

The Groq SDK transport is stubbed at `groq.AsyncGroq`, the boundary `LLMClient` imports. Nothing
below that is stubbed — the real `_call_groq` runs, with the real retry loop, the real
`json.loads`, the real `response_schema.model_validate` and the real router. That is where the
failure lived, so that is what these tests drive.

Owner: Stream C.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import get_settings
from app.db.seed import INCIDENT_GROUP_REFERENCE
from tests.contract.postgres_support import requires_postgres

pytestmark = [pytest.mark.anyio, requires_postgres]

PREFIX = "/api/v1"
GROUP = INCIDENT_GROUP_REFERENCE


# ------------------------------------------------------------------ the Groq SDK stub


class _StubTransport:
    """Returns whatever `content` is set to, or raises `error` if one is set."""

    def __init__(self) -> None:
        self.content: str = "{}"
        self.error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    def install(self, monkeypatch) -> None:
        import groq

        transport = self

        class _Msg:
            def __init__(self, content: str) -> None:
                self.content = content

        class _Choice:
            def __init__(self, content: str) -> None:
                self.message = _Msg(content)

        class _Usage:
            prompt_tokens = 1200
            completion_tokens = 900

        class _Resp:
            def __init__(self, content: str) -> None:
                self.choices = [_Choice(content)]
                self.usage = _Usage()

        class _Completions:
            async def create(self, **kwargs: Any):
                transport.calls.append(kwargs)
                if transport.error is not None:
                    raise transport.error
                return _Resp(transport.content)

        class _Chat:
            completions = _Completions()

        class _FakeAsyncGroq:
            def __init__(self, api_key: str | None = None, **_: Any) -> None:
                self.chat = _Chat()

        monkeypatch.setattr(groq, "AsyncGroq", _FakeAsyncGroq)


@pytest.fixture
def live(monkeypatch) -> _StubTransport:
    """`LLM_MODE=live` with a key present and the network stubbed.

    `get_settings` is `lru_cache`d, so the cache is cleared on the way in and on the way out —
    otherwise the live mode leaks into whichever test runs next.
    """
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-network-is-stubbed")
    get_settings.cache_clear()
    transport = _StubTransport()
    transport.install(monkeypatch)
    yield transport
    get_settings.cache_clear()


# ------------------------------------------------------------------- model payloads


def _explanation_json(**extra: Any) -> str:
    payload = {
        "status": "success",
        "reason": "Explains the completed recovery.",
        "evidence_refs": ["action:1"],
        "payload_type": "explanation.v1",
        "explanation": "The storm at Bengaluru held the departure. " * 6,
        "citation_refs": ["action:check_connections:1"],
    }
    payload.update(extra)
    return json.dumps(payload)


def _report_json(**extra: Any) -> str:
    payload = {
        "status": "success",
        "reason": "Executive report for the group.",
        "evidence_refs": [f"group:{GROUP}"],
        "payload_type": "report.v1",
        "summary": "Eight flights and 604 passengers were affected by the storm. " * 2,
        "sections": [
            {"heading": "Scope", "body": "Eight flights affected at VOBL."},
            {"heading": "Passenger impact", "body": "604 passengers were affected."},
            {"heading": "Recovery actions", "body": "Connections and crew were assessed."},
            {"heading": "Resolution", "body": "The group reached resolved."},
        ],
        "metric_refs": ["rollup:flights_affected:8", "rollup:passengers_affected:604"],
    }
    payload.update(extra)
    return json.dumps(payload)


# --------------------------------------------------------------------- journey driver


def _resolved_incident(client) -> str:
    """Drive the group to resolved and return a member reference with recorded actions.

    `/explanation` refuses an incident with no completed actions, so the journey has to run
    before there is anything to explain.
    """
    assert client.post(f"{PREFIX}/incident-groups/{GROUP}/open").status_code == 200
    state = client.post(f"{PREFIX}/incident-groups/{GROUP}/run").json()
    for _ in range(12):
        held: list[int] = []
        for member in state["members"]:
            reference = member.get("incident_reference")
            if not reference:
                continue
            body = client.get(f"{PREFIX}/incidents/{reference}/assurance").json()
            held += [
                evaluation["id"]
                for evaluation in body["evaluations"]
                if evaluation["decision"] == "needs_human" and not evaluation.get("human_decision")
            ]
        if not held:
            break
        for evaluation_id in held:
            client.post(
                f"{PREFIX}/assurance/{evaluation_id}/decision",
                json={"decision": "approved", "reason": "approved by the live reasoning test"},
            )
        state = client.post(f"{PREFIX}/incident-groups/{GROUP}/run").json()
    return state["members"][0]["incident_reference"]


# ------------------------------------------------------- a chatty model still gets served


async def test_a_live_explanation_survives_a_model_that_adds_fields(client, live):
    """The reported 500. A model volunteering `confidence` is the common case, not a rare one.

    The two prose prompts never forbade it, and the shared envelope was `extra="forbid"`.
    """
    incident = _resolved_incident(client)
    live.content = _explanation_json(confidence=0.92, model_self_report="llama-3.3-70b")

    response = client.get(f"{PREFIX}/incidents/{incident}/explanation")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["explanation"]) > 50
    # Metadata the contract promises, unchanged by the fix.
    assert body["source"] == "live"
    assert body["authorises_no_action"] is True
    assert body["llm_mode"] == "live"
    assert body["generator"].startswith("groq:")
    assert body["prompt_version"] == "explainer.v1"
    # The unsolicited keys are dropped rather than echoed back to the caller.
    assert "confidence" not in body
    # A junk self-report is discarded, not promoted into the audit.
    assert body["audit"]["model_self_report"] is None


async def test_a_live_report_survives_an_extra_key_inside_a_section(client, live):
    _resolved_incident(client)
    live.content = _report_json(
        sections=[
            {"heading": "Scope", "body": "Eight flights.", "bullets": ["a"]},
            {"heading": "Passenger impact", "body": "604 passengers."},
            {"heading": "Recovery actions", "body": "Connections assessed."},
        ]
    )

    response = client.get(f"{PREFIX}/reports/{GROUP}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["sections"]) == 3
    assert body["source"] == "live"
    assert body["authorises_no_action"] is True
    assert body["generator"].startswith("groq:")
    assert body["metric_refs"]


async def test_a_valid_self_report_is_recorded_rather_than_discarded(client, live):
    """Dropping junk must not mean dropping everything."""
    incident = _resolved_incident(client)
    live.content = _explanation_json(model_self_report=91)

    body = client.get(f"{PREFIX}/incidents/{incident}/explanation").json()

    assert body["audit"]["model_self_report"] == 91


# ----------------------------------------------------- a real outage is 503, never 500


async def test_a_provider_outage_is_503_and_not_500(client, live):
    """`LLMUnavailable` is a plain Exception and `app.main` only handles `TravelOpsError`.

    So before the fix this was a bare 500 with no error code and no mode information.
    """
    incident = _resolved_incident(client)
    live.error = TimeoutError("connection timed out")

    response = client.get(f"{PREFIX}/incidents/{incident}/explanation")

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["details"]["llm_mode"] == "live"
    assert "explanation" in error["message"]


async def test_live_mode_without_a_key_is_503_and_not_500(client, monkeypatch):
    incident = _resolved_incident(client)
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    try:
        response = client.get(f"{PREFIX}/incidents/{incident}/explanation")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["details"]["llm_mode"] == "live"
    finally:
        get_settings.cache_clear()


async def test_an_outage_never_serves_the_fixture_instead(client, live):
    """No fixture fallback in live mode.

    A committed fixture returned under `source: live` would put recorded prose behind a label
    that says a model wrote it just now, which makes the artifact untraceable.
    """
    _resolved_incident(client)
    live.error = TimeoutError("connection timed out")

    response = client.get(f"{PREFIX}/reports/{GROUP}")

    assert response.status_code == 503
    assert "summary" not in response.json()


async def test_truncated_json_is_reported_not_guessed(client, live):
    """`max_tokens` truncation arrives as invalid JSON. There is no half-artifact to serve."""
    _resolved_incident(client)
    live.content = '{"status":"success","reason":"r","payload_type":"report.v1","summary":"Eight'

    assert client.get(f"{PREFIX}/reports/{GROUP}").status_code == 503


# ----------------------------------------------------- the prose agents get their own ceiling


async def test_the_prose_agents_ask_for_more_tokens_than_the_planner(client, live):
    """One 4096 ceiling for a task list and a six-section report truncates the report.

    Asserted on the request the client actually sent, because the failure it causes —
    `JSONDecodeError` — is indistinguishable from a transport fault once it has happened.
    """
    _resolved_incident(client)
    live.content = _report_json()

    assert client.get(f"{PREFIX}/reports/{GROUP}").status_code == 200
    assert live.calls, "the stub was never called, so this asserts nothing"
    assert live.calls[-1]["max_tokens"] > 4096
    assert live.calls[-1]["response_format"] == {"type": "json_object"}


# --------------------------------------------------------- the other modes are untouched


async def test_off_mode_still_returns_404_naming_the_mode(client, monkeypatch):
    incident = _resolved_incident(client)
    monkeypatch.setenv("LLM_MODE", "off")
    get_settings.cache_clear()
    try:
        for path in (f"{PREFIX}/incidents/{incident}/explanation", f"{PREFIX}/reports/{GROUP}"):
            response = client.get(path)
            assert response.status_code == 404, response.text
            assert response.json()["error"]["details"]["llm_mode"] == "off"
    finally:
        get_settings.cache_clear()


async def test_fixture_mode_still_serves_the_committed_artefact(client, monkeypatch):
    """The fix must not have moved fixture mode. `source` stays `fixture`, not `live`."""
    incident = _resolved_incident(client)
    monkeypatch.setenv("LLM_MODE", "fixture")
    get_settings.cache_clear()
    try:
        body = client.get(f"{PREFIX}/incidents/{incident}/explanation").json()
        assert body["source"] == "fixture"
        assert body["generator"] == "fixture:explainer"
        assert body["authorises_no_action"] is True
        assert len(body["explanation"]) > 50

        report = client.get(f"{PREFIX}/reports/{GROUP}").json()
        assert report["source"] == "fixture"
        assert report["generator"] == "fixture:reporter"
        assert report["authorises_no_action"] is True
        assert len(report["sections"]) >= 3
    finally:
        get_settings.cache_clear()
