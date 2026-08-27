"""A recording stand-in for the chat-completions HTTP call.

Shared by every live-transport test so there is one stub rather than four drifting ones, and so
they all assert against the same recorded shape — including the request URL.

The URL matters more than anything else here. The live path was broken for a full round because
the endpoint was assembled by a vendor SDK from a base URL: it appended its own
`/openai/v1/chat/completions`, producing
`https://openrouter.ai/api/v1/openai/v1/chat/completions` and a 404 on every call. Every stub
before this one accepted whatever URL it was given, so nothing failed locally. This one records
it and the tests assert it.

Owner: Stream C.
"""

from __future__ import annotations

import json as _json
from typing import Any


class FakeResponse:
    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str | None = None
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else _json.dumps(payload or {})

    def json(self) -> Any:
        if self._payload is not None:
            return self._payload
        return _json.loads(self.text)


def completion(content: str, *, prompt_tokens: int = 700, completion_tokens: int = 200) -> Any:
    """The OpenAI-compatible success envelope both providers return."""
    return {
        "id": "gen-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def error_body(message: str, *, code: str | None = None) -> dict:
    error: dict[str, Any] = {"message": message}
    if code:
        error["code"] = code
    return {"error": error}


class RecordingTransport:
    """Records each request and replays a scripted outcome.

    `requests` entries are `{"url", "headers", "json"}` — exactly what went on the wire.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        #: Set to return a JSON string as the assistant message content.
        self.content: str | None = None
        #: Set to return a non-2xx response.
        self.status: int | None = None
        self.status_payload: Any = None
        #: Set to raise a transport-level exception instead of responding.
        self.error: BaseException | None = None
        #: How many leading calls the scripted failure applies to. 0 means all of them.
        self.fail_times: int = 0

    # ---------------------------------------------------------------- scripting

    def returns(self, content: str) -> RecordingTransport:
        self.content = content
        return self

    def fails_with_status(
        self, status: int, message: str, *, code: str | None = None, times: int = 0
    ) -> RecordingTransport:
        self.status = status
        self.status_payload = error_body(message, code=code)
        self.fail_times = times
        return self

    def raises(self, exc: BaseException, *, times: int = 0) -> RecordingTransport:
        self.error = exc
        self.fail_times = times
        return self

    # ---------------------------------------------------------------- install

    def install(self, monkeypatch) -> RecordingTransport:
        import httpx

        stub = self

        class _FakeAsyncClient:
            def __init__(self, *_: Any, **__: Any) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_: Any) -> None:
                return None

            async def post(self, url: str, *, headers: dict, json: dict, **_: Any):
                stub.requests.append({"url": url, "headers": headers, "json": json})
                attempt = len(stub.requests)
                scripted = stub.fail_times == 0 or attempt <= stub.fail_times
                if scripted and stub.error is not None:
                    raise stub.error
                if scripted and stub.status is not None:
                    return FakeResponse(stub.status, stub.status_payload)
                return FakeResponse(200, completion(stub.content or "{}"))

        monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
        return self

    # ---------------------------------------------------------------- reading

    @property
    def last(self) -> dict[str, Any]:
        assert self.requests, "the transport was never called, so this asserts nothing"
        return self.requests[-1]

    @property
    def calls(self) -> int:
        return len(self.requests)


EXPLANATION_JSON = _json.dumps(
    {
        "status": "success",
        "reason": "Explains the recorded recovery.",
        "evidence_refs": ["action:1"],
        "payload_type": "explanation.v1",
        "explanation": "A severe thunderstorm over Bengaluru held the departure on stand.",
        "citation_refs": ["action:check_connections:1"],
    }
)
