"""A credential a vendor requires in the URL must not reach the logs.

`httpx` logs every request at INFO as ``HTTP Request: GET <full url> "<status>"``, and a full URL
includes the query string. AviationStack authenticates with `access_key` **as a query parameter**
and offers no header alternative, so at the service's own default `LOG_LEVEL=INFO` every live
flight-status call wrote the API key into the application log — and from there into any aggregator,
screen share or pasted excerpt.

The structlog redaction processor cannot catch it. That processor inspects event-dict keys, and
this is a pre-formatted message from the standard-library logger with the secret embedded in a
string. So the fix is at the logger level, and this file is the guard that keeps it.

Bearer-token providers were never exposed this way, because httpx does not log headers. These tests
pin both facts, so a future change that re-enables httpx INFO logging fails here rather than
quietly leaking on the next live run.

Cross-cutting invariant guard: SHARED by intent, per `OWNERS`. No stream may relax it alone.
"""

from __future__ import annotations

import io
import json
import logging

import httpx
import pytest

from app.config import Settings, resolve_modes
from app.observability.logging import configure_logging
from app.providers.flight_status import LiveFlightStatusProvider

KEY = "TEST-ONLY-aviationstack-9f3a2c11b7"


def _restore_deployed_log_levels() -> None:
    """Put the loggers back where the deployed service has them, then run the code under test.

    This step is load-bearing. pytest's logging plugin pins the ROOT logger at WARNING, and
    `httpx` carries no level of its own, so under pytest it inherits WARNING and never emits the
    INFO line that carries the URL. Without restoring the deployed default first, every assertion
    in this file would pass whether or not the fix exists — which is exactly how a leak like this
    survives a green suite.
    """
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    # The code under test has to undo what the two lines above just did.
    configure_logging()


@pytest.fixture
def captured_root_log():
    """Everything that reaches the root logger, as the deployed service would emit it."""
    root = logging.getLogger()
    saved = {name: logging.getLogger(name).level for name in ("", "httpx", "httpcore")}
    _restore_deployed_log_levels()
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    root.addHandler(handler)
    try:
        yield buffer
    finally:
        root.removeHandler(handler)
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


def _provider(handler) -> LiveFlightStatusProvider:
    return LiveFlightStatusProvider(
        api_key=KEY,
        flight_index={1: "6E2134"},
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestTheKeyNeverReachesTheLogs:
    def test_the_noisy_third_party_loggers_are_held_at_warning(self):
        """Stated as configuration rather than hoped for.

        WARNING and above still propagate, so a transport failure is still visible; only the
        per-request INFO line carrying the URL is suppressed.

        Asserted on the resulting logger levels rather than on the module constant, so the
        behavioural guarantee is what is pinned and not one particular way of achieving it.
        """
        saved = {name: logging.getLogger(name).level for name in ("", "httpx", "httpcore")}
        try:
            _restore_deployed_log_levels()

            for name in ("httpx", "httpcore"):
                assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
        finally:
            for name, level in saved.items():
                logging.getLogger(name).setLevel(level)

    async def test_a_successful_live_lookup_logs_no_key(self, captured_root_log):
        payload = {
            "data": [
                {
                    "flight_status": "active",
                    "departure": {
                        "icao": "VOBL",
                        "scheduled": "2026-08-20T15:40:00+00:00",
                        "estimated": "2026-08-20T17:15:00+00:00",
                        "delay": 95,
                    },
                    "arrival": {"icao": "VIDP", "scheduled": "2026-08-20T18:25:00+00:00"},
                    "flight": {"iata": "6E2134", "number": "2134"},
                }
            ]
        }
        provider = _provider(lambda _request: httpx.Response(200, json=payload))

        status = await provider.get_status(1)

        assert status["delay_minutes"] == 95, "the call must really have succeeded"
        assert KEY not in captured_root_log.getvalue()

    async def test_a_refused_key_is_not_echoed_when_it_is_rejected(self, captured_root_log):
        """The most dangerous moment: the provider is complaining about the credential itself."""
        from app.providers.base import ProviderError

        provider = _provider(
            lambda _request: httpx.Response(
                401, json={"error": {"code": "invalid_access_key", "message": "bad key"}}
            )
        )

        with pytest.raises(ProviderError) as caught:
            await provider.get_status(1)

        assert KEY not in str(caught.value)
        assert KEY not in captured_root_log.getvalue()

    async def test_a_transport_failure_logs_no_key(self, captured_root_log):
        from app.providers.base import ProviderError

        def explode(_request: httpx.Request):
            raise httpx.ConnectError("no route to host")

        provider = _provider(explode)

        with pytest.raises(ProviderError):
            await provider.get_status(1)

        assert KEY not in captured_root_log.getvalue()

    async def test_the_key_does_go_on_the_wire_because_the_vendor_requires_it(self):
        """The complement of the rule above, so the fix cannot be "stop sending the key".

        AviationStack has no header auth. The credential must be in the query string; what must
        not happen is that string being written to a log.
        """
        seen: list[str] = []

        def record(request: httpx.Request):
            seen.append(str(request.url))
            return httpx.Response(200, json={"data": []})

        from app.providers.base import ProviderError

        with pytest.raises(ProviderError):
            await _provider(record).get_status(1)

        assert seen and f"access_key={KEY}" in seen[0]


class TestTheKeyNeverReachesAnApiResponse:
    def test_the_published_runtime_mode_carries_the_mode_and_not_the_credential(self):
        modes = resolve_modes(
            Settings(_env_file=None, flight_status_mode="live", aviationstack_api_key=KEY)
        )

        published = json.dumps(modes.to_dict())
        assert '"flight_status_mode": "live"' in published
        assert KEY not in published

    def test_a_missing_key_is_reported_by_name_never_by_value(self):
        """The refusal has to name the variable to set without quoting a neighbouring secret."""
        from app.config import ConfigurationError

        with pytest.raises(ConfigurationError) as caught:
            resolve_modes(
                Settings(_env_file=None, flight_status_mode="live", aviationstack_api_key="")
            )

        assert "AVIATIONSTACK_API_KEY" in str(caught.value)
        assert KEY not in str(caught.value)
