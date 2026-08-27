"""Safe diagnostics for a provider 200 that fails before an artifact is returned."""

from __future__ import annotations

import json

import pytest

from app.agents.contract import PlannerResponse, ReportResponse
from app.config import get_settings
from app.llm.client import LLMClient, LLMUnavailable
from tests.llm_transport_stub import RecordingTransport, completion


class _LogRecorder:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def _record(self, event: str, **fields) -> None:
        self.entries.append({"event": event, **fields})

    info = _record
    warning = _record
    error = _record


@pytest.fixture
def logs(monkeypatch) -> list[dict]:
    recorder = _LogRecorder()
    monkeypatch.setattr("app.llm.client.log", recorder)
    return recorder.entries


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub-key")
    monkeypatch.setattr("app.llm.client.RETRY_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _call(schema, agent_name: str):
    return await LLMClient().call(
        prompt="typed recorded context",
        system="Return one JSON object.",
        response_schema=schema,
        agent_name=agent_name,
        prompt_version=f"{agent_name}.v1",
    )


async def test_http_200_with_a_malformed_envelope_names_the_boundary(live, monkeypatch, logs):
    stub = RecordingTransport().returns_payload({"choices": []}).install(monkeypatch)

    with pytest.raises(LLMUnavailable) as caught:
        await _call(PlannerResponse, "planner")

    assert caught.value.phase == "provider_envelope"
    assert caught.value.status_code == 200
    assert stub.calls == 3
    received = [entry for entry in logs if entry["event"] == "llm_provider_response_received"]
    assert received and all(entry["status_code"] == 200 for entry in received)
    terminal = [entry for entry in logs if entry["event"] == "llm_call_failed"]
    assert terminal[-1]["phase"] == "provider_envelope"
    assert terminal[-1]["status_code"] == 200


async def test_report_payload_and_audit_validation_are_distinguished(live, monkeypatch, logs):
    report = json.dumps(
        {
            "status": "success",
            "reason": "Recorded executive summary.",
            "evidence_refs": ["group:GRP-1"],
            "payload_type": "report.v1",
            "summary": "The recorded recovery completed successfully.",
            "sections": [{"heading": "Outcome", "body": "All recorded actions completed."}],
            "metric_refs": ["rollup:flights_affected:8"],
        }
    )
    payload = completion(report)
    payload["usage"]["prompt_tokens"] = "not-an-integer"
    RecordingTransport().returns_payload(payload).install(monkeypatch)

    with pytest.raises(LLMUnavailable) as caught:
        await _call(ReportResponse, "reporter")

    assert caught.value.phase == "audit_schema"
    failures = [entry for entry in logs if entry["event"] == "llm_schema_validation_failed"]
    assert failures[-1]["schema"] == "ModelCallAudit"
    assert failures[-1]["phase"] == "audit_schema"
    assert failures[-1]["status_code"] == 200


async def test_invalid_assistant_json_logs_position_not_model_content(live, monkeypatch, logs):
    marker = "MODEL-CONTENT-MUST-NOT-REACH-LOGS"
    RecordingTransport().returns('{"summary":"' + marker).install(monkeypatch)

    with pytest.raises(LLMUnavailable) as caught:
        await _call(ReportResponse, "reporter")

    assert caught.value.phase == "content_json"
    retries = [entry for entry in logs if entry["event"] == "llm_call_retrying"]
    assert retries[-1]["phase"] == "content_json"
    assert isinstance(retries[-1]["json_error_position"], int)
    assert marker not in json.dumps(logs)


async def test_provider_finish_reason_is_allowlisted_before_logging(live, monkeypatch, logs):
    marker = "FINISH-REASON-MUST-NOT-REACH-LOGS"
    report = json.dumps(
        {
            "status": "success",
            "reason": "Recorded executive summary.",
            "evidence_refs": [],
            "payload_type": "report.v1",
            "summary": "The recorded recovery completed successfully.",
            "sections": [],
            "metric_refs": [],
        }
    )
    payload = completion(report)
    payload["choices"][0]["finish_reason"] = marker
    RecordingTransport().returns_payload(payload).install(monkeypatch)

    parsed, _audit = await _call(ReportResponse, "reporter")

    assert parsed.summary
    assert marker not in json.dumps(logs)
    received = [entry for entry in logs if entry["event"] == "llm_provider_content_received"]
    assert received[-1]["finish_reason"] == "unknown_str"
