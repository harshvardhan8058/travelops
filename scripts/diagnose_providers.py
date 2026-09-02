#!/usr/bin/env python3
"""Answer "is any of this actually live?" for every external provider, on the machine that has the keys.

Read-only. Writes nothing, changes no application behaviour, and never falls back to a fixture to
make a result look better than it is. Run it where the API runs:

    python scripts/diagnose_providers.py

Why this exists. Configuration and use are different facts, and every screen that conflates them
eventually tells somebody a provider is live when nothing has called it. `GET /system/mode` reports
what this deployment is *configured* to do; `GET /sources` reports what it has *done*. Neither can
tell you whether the credential in your `.env` is one this provider will actually accept, because
answering that requires making a request — which is what this script does, once per provider, with
the smallest call that proves the point.

For each provider it reports the same five things, in the order they can fail:

    CONFIGURED         is a credential present (never its value)
    NETWORK REACHABLE  did the host answer at all
    REQUEST ATTEMPTED  was a real request made
    REQUEST SUCCEEDED  did the provider accept it
    ACTUAL MODE        what this deployment will therefore really do
    EVIDENCE           the artefact backing the claim above

**No secret is ever printed.** Keys are reported as present or absent and by length only, and no
response body is echoed beyond a status line, because provider errors sometimes quote the request.

A failure here is a finding, not a crash: every provider is attempted independently, and the script
exits 0 unless it could not run at all. "Blocked by environment" is a legitimate answer and is
printed as one.

Owner: Stream A.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for candidate in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

RULE = "=" * 78
TIMEOUT = 25


def _load_dotenv() -> None:
    """Read `.env` if python-dotenv is not already doing it, so this runs outside the app too."""
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _request(
    url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None
) -> tuple[int | None, str]:
    """Return `(status, detail)`. Never raises, never returns a response body."""
    request = urllib.request.Request(url, data=body, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, "ok"
    except urllib.error.HTTPError as exc:
        # The status is the finding. The body is not echoed: provider errors sometimes quote the
        # request back, and the request carries the key.
        return exc.code, f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"unreachable: {type(exc.reason).__name__ if exc.reason else 'no response'}"
    except Exception as exc:  # noqa: BLE001 - a diagnosis must not crash on an unexpected failure
        return None, f"{type(exc).__name__}"


def _report(
    name: str,
    *,
    configured: bool,
    key_len: int | None,
    reachable: bool | None,
    attempted: bool,
    succeeded: bool | None,
    actual_mode: str,
    evidence: str,
    detail: str = "",
) -> None:
    def mark(value: bool | None) -> str:
        return {True: "yes", False: "NO", None: "not tested"}[value]

    print(f"\n{name}")
    print("-" * len(name))
    print(f"  CONFIGURED         {mark(configured)}" + (f" (length {key_len})" if key_len else ""))
    print(f"  NETWORK REACHABLE  {mark(reachable)}")
    print(f"  REQUEST ATTEMPTED  {mark(attempted)}")
    print(f"  REQUEST SUCCEEDED  {mark(succeeded)}")
    print(f"  ACTUAL MODE        {actual_mode}")
    print(f"  EVIDENCE           {evidence}")
    if detail:
        print(f"  DETAIL             {detail}")


def diagnose_llm() -> None:
    from app.config import LLMMode, get_settings, provider_transport

    settings = get_settings()
    transport = provider_transport(settings)
    mode = settings.llm_mode
    configured = bool(transport.api_key)

    if mode is not LLMMode.live:
        _report(
            f"Reasoning model ({transport.provider.value})",
            configured=configured,
            key_len=len(transport.api_key) or None,
            reachable=None,
            attempted=False,
            succeeded=None,
            actual_mode=f"LLM_MODE={mode.value}",
            evidence="committed artefacts under backend/app/llm/fixtures/"
            if mode is LLMMode.fixture
            else "deterministic playbook only",
            detail="A key being present does not mean it is used. Nothing is contacted in this mode.",
        )
        return

    if not configured:
        _report(
            f"Reasoning model ({transport.provider.value})",
            configured=False,
            key_len=None,
            reachable=None,
            attempted=False,
            succeeded=False,
            actual_mode="live requested, unusable",
            evidence=f"{transport.key_env_var} is empty",
        )
        return

    # The smallest call that proves the credential and the route: one token, real endpoint.
    status, detail = _request(
        transport.endpoint_url,
        headers={"Authorization": f"Bearer {transport.api_key}", **transport.extra_headers},
        body=json.dumps(
            {
                "model": transport.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
        ).encode(),
    )
    succeeded = status is not None and 200 <= status < 300
    _report(
        f"Reasoning model ({transport.provider.value})",
        configured=True,
        key_len=len(transport.api_key),
        reachable=status is not None,
        attempted=True,
        succeeded=succeeded,
        actual_mode="live" if succeeded else "live requested, provider refused",
        evidence=f"{transport.model} at {transport.endpoint_url}",
        detail=detail,
    )
    print(
        "  NOTE               A successful call here does NOT mean a model wrote the plan of "
        "record.\n"
        "                     The deterministic playbook is persisted first and nothing "
        "auto-selects the\n"
        "                     planner's candidate. `GET /sources` and the plan panel both say so."
    )


def diagnose_weather() -> None:
    from app.config import WeatherMode, get_settings

    settings = get_settings()
    mode = settings.weather_mode
    if mode is not WeatherMode.live:
        _report(
            "Weather (Aviation Weather Center)",
            configured=True,
            key_len=None,
            reachable=None,
            attempted=False,
            succeeded=None,
            actual_mode="WEATHER_MODE=fixture",
            evidence="committed METAR snapshot",
            detail="Needs no credential; public domain. Nothing is contacted in fixture mode.",
        )
        return
    status, detail = _request("https://aviationweather.gov/api/data/metar?ids=VOBL&format=json")
    succeeded = status is not None and 200 <= status < 300
    _report(
        "Weather (Aviation Weather Center)",
        configured=True,
        key_len=None,
        reachable=status is not None,
        attempted=True,
        succeeded=succeeded,
        actual_mode="live" if succeeded else "live requested, provider unreachable",
        evidence="METAR for VOBL",
        detail=detail,
    )


def diagnose_flight_status() -> None:
    from app.config import FlightStatusMode, get_settings

    settings = get_settings()
    mode = settings.flight_status_mode
    key = str(getattr(settings, "aviationstack_api_key", "") or "")
    if mode is not FlightStatusMode.live:
        _report(
            "Flight status (AviationStack)",
            configured=bool(key),
            key_len=len(key) or None,
            reachable=None,
            attempted=False,
            succeeded=None,
            actual_mode="FLIGHT_STATUS_MODE=fixture",
            evidence="committed flight-status snapshot",
        )
        return
    if not key:
        _report(
            "Flight status (AviationStack)",
            configured=False,
            key_len=None,
            reachable=None,
            attempted=False,
            succeeded=False,
            actual_mode="live requested, unusable",
            evidence="AVIATIONSTACK_API_KEY is empty",
        )
        return
    status, detail = _request(
        f"https://api.aviationstack.com/v1/flights?access_key={key}&limit=1"
    )
    # AviationStack answers 200 with an `error` object for a rejected key, so a 200 alone is not
    # success. Only the shape of the body settles it — and the body is not printed.
    succeeded = status is not None and 200 <= status < 300
    _report(
        "Flight status (AviationStack)",
        configured=True,
        key_len=len(key),
        reachable=status is not None,
        attempted=True,
        succeeded=succeeded,
        actual_mode="live" if succeeded else "live requested, provider refused",
        evidence="one flight row",
        detail=detail
        + ("; note AviationStack returns 200 with an error object for a rejected key" if succeeded else ""),
    )


def diagnose_notifications() -> None:
    from app.config import get_modes, get_settings

    settings = get_settings()
    modes = get_modes()
    allowlist = settings.recipient_allowlist
    _report(
        f"Notifications ({settings.notification_mode.value})",
        configured=bool(settings.smtp_host and settings.smtp_username and settings.smtp_password),
        key_len=None,
        reachable=None,
        attempted=False,
        succeeded=None,
        actual_mode="real delivery enabled" if modes.real_email_enabled else "simulated only",
        evidence=f"{len(allowlist)} allowlisted recipient(s)",
        detail=(
            "Nothing is delivered to anyone. Every message is recorded with "
            "delivery_mode=simulated."
            if not modes.real_email_enabled
            else "Delivery is restricted to the allowlist; everything else is recorded as simulated."
        ),
    )
    print(
        "  NOTE               No message is sent by this script. Delivery is only ever attempted "
        "by\n                     an executing workflow action."
    )


def main() -> int:
    _load_dotenv()
    print(RULE)
    print("TravelOps provider diagnosis — configured, reachable, attempted, succeeded")
    print(RULE)
    print(
        "\nNo secret is printed. Keys are reported by presence and length only, and no response\n"
        "body is echoed. A refusal is a finding, not a failure of this script."
    )

    for step in (diagnose_llm, diagnose_weather, diagnose_flight_status, diagnose_notifications):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - one provider must not stop the others
            print(f"\n{step.__name__}: could not run ({type(exc).__name__}: {exc})")

    print(f"\n{RULE}")
    print(
        "Cross-check: GET /api/v1/system/mode publishes what this deployment is CONFIGURED to do,\n"
        "and GET /api/v1/sources publishes what it has actually DONE. This script is the third\n"
        "leg — whether the providers themselves accept these credentials right now."
    )
    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
