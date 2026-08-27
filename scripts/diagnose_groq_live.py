#!/usr/bin/env python3
"""Capture the exact Groq error for the first failed live call, then name the parameter.

Read-only diagnosis. Changes no application behaviour, writes nothing to the database, and
never falls back to a fixture. Run it in the container that has the key:

    docker compose exec api python scripts/diagnose_groq_live.py

Three stages:

  1. Resolved configuration, so the run is attributable to a specific model and mode.
  2. One call per agent through the EXISTING `LLMClient`, with the real prompt files and the
     real request parameters. This is the call `verify_phase3.py` makes; the error printed here
     is the error the planner, explainer and reporter each got.
  3. If any call was refused with a 4xx, a parameter bisection against the raw SDK. Each request
     is the same as the one the client sends with exactly one thing changed, so the first
     variant that succeeds names the incompatibility rather than suggesting one.

Stage 3 is why this exists. `LLMUnavailable` deliberately does not carry the provider's
`code`/`param` fields, so the application log tells you a call failed but not which argument
Groq objected to.

Owner: Stream C. Phase 3 live-mode diagnosis.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for candidate in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

RULE = "=" * 78


def _mask(value: str) -> str:
    if not value:
        return "<absent>"
    return f"{value[:4]}...{value[-4:]} (len {len(value)})"


def _describe_exception(exc: BaseException) -> dict[str, Any]:
    """Pull status, code, param and message out of a Groq SDK error.

    Every field is optional and read defensively: this must report the failure it was given,
    not fail while trying to describe it.
    """
    out: dict[str, Any] = {"exception": type(exc).__name__, "str": str(exc)[:600]}
    out["status_code"] = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(error, dict):
            for key in ("message", "type", "code", "param"):
                if error.get(key) is not None:
                    out[key] = error[key]
    response = getattr(exc, "response", None)
    if out.get("message") is None and response is not None:
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                for key in ("message", "type", "code", "param"):
                    if error.get(key) is not None:
                        out[key] = error[key]
        except Exception:
            with contextlib.suppress(Exception):
                out["raw_body"] = response.text[:600]
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        out["cause"] = _describe_exception(cause)
    return out


def _provider_layer(info: dict[str, Any]) -> dict[str, Any]:
    """The innermost layer that actually carries a provider status code.

    The agents raise `LLMUnavailable`, which has no `status_code` — the Groq error is the
    `__cause__` underneath it. Reading only the top layer reports `HTTP None`, which is how a
    diagnosis ends up saying less than the log it was meant to replace.
    """
    deepest = info
    layer: Any = info
    while isinstance(layer, dict):
        if isinstance(layer.get("status_code"), int):
            deepest = layer
        layer = layer.get("cause")
    return deepest


def _print_error(label: str, info: dict[str, Any], indent: str = "    ") -> None:
    for key in ("exception", "status_code", "code", "type", "param", "message"):
        if info.get(key) is not None:
            print(f"{indent}{key:12} {info[key]}")
    if info.get("message") is None and info.get("raw_body"):
        print(f"{indent}{'raw_body':12} {info['raw_body']}")
    if info.get("message") is None and info.get("code") is None:
        print(f"{indent}{'str':12} {info.get('str')}")
    cause = info.get("cause")
    if isinstance(cause, dict):
        print(f"{indent}caused by:")
        _print_error(label, cause, indent + "    ")


# ------------------------------------------------------------------ stage 1: configuration


def stage_configuration() -> Any:
    from app.config import get_settings

    settings = get_settings()
    print(RULE)
    print("1. RESOLVED CONFIGURATION")
    print(RULE)
    from app.config import provider_transport

    transport = provider_transport(settings)
    print(f"  LLM_MODE               {getattr(settings.llm_mode, 'value', settings.llm_mode)}")
    print(f"  LLM_PROVIDER           {transport.provider.value}")
    print(f"  endpoint               {transport.endpoint_url}")
    print(f"  model                  {transport.model}")
    print(f"  {transport.key_env_var:22} {_mask(transport.api_key)}")
    print(f"  GROQ_TEMPERATURE       {settings.groq_temperature}")
    print(f"  ALLOW_LLM_DEGRADATION  {settings.allow_llm_degradation}")

    import groq

    print(f"  groq sdk               {groq.__version__}")

    from app.agents import explainer, reporter
    from app.llm.client import DEFAULT_MAX_TOKENS

    print(f"  tpm_limit              {transport.tpm_limit}")
    print(f"  max_tokens planner     {DEFAULT_MAX_TOKENS}")
    print(f"  max_tokens explainer   {explainer.MAX_TOKENS}")
    print(f"  max_tokens reporter    {reporter.MAX_TOKENS}")

    if not transport.api_key:
        print()
        print(f"  {transport.key_env_var} is empty. Run this inside the API container.")
        raise SystemExit(2)
    return settings


# ------------------------------------------------- stage 2: one call per agent, real client


async def stage_agents() -> dict[str, dict[str, Any]]:
    """Call each agent once through the existing client. Returns {agent: error_info | {}}."""
    from app.agents.explainer import ExplainerAgent
    from app.agents.planner import PlannerAgent
    from app.agents.reporter import ReportGeneratorAgent

    print()
    print(RULE)
    print("2. ONE CALL PER AGENT, THROUGH THE EXISTING LLMClient")
    print(RULE)

    actions = [
        {"action_type": "check_connections", "status": "success", "reason": "22 at risk"},
        {"action_type": "assess_crew_impact", "status": "success", "reason": "10 pairings"},
    ]
    rollup = {
        "flights_affected": 8,
        "passengers_affected": 604,
        "connections_at_risk": 22,
        "crew_pairings_affected": 10,
        "candidate_hotels": 6,
    }

    async def planner() -> Any:
        return await PlannerAgent().propose(
            incident_reference="INC-2026-0820-VOBL-01",
            flight_id=1,
            flight_number="6E 2134",
            route="VOBL-VIDP",
            delay_minutes=143,
            trigger_type="weather",
            severity="high",
            airport_icao="VOBL",
            passengers_affected=174,
            connections_at_risk=22,
            crew_pairings_affected=10,
        )

    async def explainer_call() -> Any:
        return await ExplainerAgent().explain(
            incident_reference="INC-2026-0820-VOBL-01", actions_summary=actions
        )

    async def reporter_call() -> Any:
        return await ReportGeneratorAgent().generate(
            group_reference="GRP-2026-0820-VOBL", rollup=rollup
        )

    results: dict[str, dict[str, Any]] = {}
    for name, call in (
        ("planner", planner),
        ("explainer", explainer_call),
        ("reporter", reporter_call),
    ):
        print()
        print(f"  --- {name} ---")
        try:
            _response, audit = await call()
            print(f"    OK  generator={audit.generator}  prompt_version={audit.prompt_version}")
            print(
                f"        input_tokens={audit.input_tokens} "
                f"output_tokens={audit.output_tokens} latency_ms={audit.latency_ms}"
            )
            results[name] = {}
        except BaseException as exc:  # diagnosis reports anything it is given
            info = _describe_exception(exc)
            _print_error(name, info)
            results[name] = info
    return results


# --------------------------------------------------------- stage 3: which parameter is it


async def stage_bisect(settings: Any) -> None:
    """Same request the client sends, one thing changed at a time, straight to the SDK.

    The client's own error path raises `LLMUnavailable`, which drops the provider's `code` and
    `param`. Going direct keeps them, and keeps the comparison honest: every variant below is
    the production request with a single documented difference.
    """
    import httpx

    from app.config import provider_transport

    print()
    print(RULE)
    print("3. PARAMETER BISECTION (direct HTTP, one change per request)")
    print(RULE)

    transport = provider_transport(settings)
    url = transport.endpoint_url
    headers = {
        "Authorization": f"Bearer {transport.api_key}",
        "Content-Type": "application/json",
        **transport.extra_headers,
    }
    print(f"  POST {url}")
    model = transport.model
    system = "You are a JSON API. Reply with JSON only."
    user = 'Return exactly {"ok": true} as JSON.'
    both = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    user_only = [{"role": "user", "content": f"{system}\n\n{user}"}]

    # Ordered narrowest-first so the first success is the most specific answer.
    variants: list[tuple[str, dict[str, Any]]] = [
        (
            "production request, exactly as the client sends it",
            {
                "model": model,
                "messages": both,
                "temperature": settings.groq_temperature,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "max_completion_tokens instead of max_tokens",
            {
                "model": model,
                "messages": both,
                "temperature": settings.groq_temperature,
                "max_completion_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "no token ceiling at all",
            {
                "model": model,
                "messages": both,
                "temperature": settings.groq_temperature,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "max_tokens lowered to 1024",
            {
                "model": model,
                "messages": both,
                "temperature": settings.groq_temperature,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "max_tokens 8192, the value the two prose agents send",
            {
                "model": model,
                "messages": both,
                "temperature": settings.groq_temperature,
                "max_tokens": 8192,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "no response_format (JSON mode off)",
            {
                "model": model,
                "messages": both,
                "temperature": settings.groq_temperature,
                "max_tokens": 4096,
            },
        ),
        (
            "no system message, instructions folded into the user turn",
            {
                "model": model,
                "messages": user_only,
                "temperature": settings.groq_temperature,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "default temperature (parameter omitted)",
            {
                "model": model,
                "messages": both,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "minimal: model + messages only",
            {"model": model, "messages": user_only},
        ),
        (
            "model probe: is the model id itself accepted?",
            {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
        ),
    ]

    async with httpx.AsyncClient(timeout=60.0) as http:
        for label, kwargs in variants:
            try:
                response = await http.post(url, headers=headers, json=kwargs)
                if response.status_code >= 400:
                    body = response.json() if response.text.startswith("{") else {}
                    error = body.get("error") or {}
                    print(
                        f"  FAIL  {label}   "
                        f"[HTTP {response.status_code} {error.get('code') or error.get('type')}]"
                    )
                    print(f"        {str(error.get('message') or response.text)[:220]}")
                    continue
                payload = response.json()
                message = payload["choices"][0]["message"]
                content = (message.get("content") or "")[:60].replace("\n", " ")
                usage = payload.get("usage") or {}
                print(f"  PASS  {label}")
                print(f"        content={content!r}")
                print(f"        reasoning field present: {message.get('reasoning') is not None}")
                print(
                    f"        prompt_tokens={usage.get('prompt_tokens')} "
                    f"completion_tokens={usage.get('completion_tokens')}"
                )
            except BaseException as exc:
                info = _describe_exception(exc)
                print(f"  FAIL  {label}   [{info.get('exception')}]")
                print(f"        {str(info.get('message') or info.get('str'))[:220]}")

    print()
    print("  Read the first PASS after the failing production request: the single difference")
    print("  between it and the request above it is the incompatibility.")


# ---------------------------------------------------------------------- available models


async def stage_models(settings: Any) -> None:
    import httpx

    from app.config import provider_transport

    print()
    print(RULE)
    print("4. MODELS THIS KEY CAN SEE")
    print(RULE)
    transport = provider_transport(settings)
    models_url = transport.endpoint_url.replace("/chat/completions", "/models")
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            listing = (
                await http.get(models_url, headers={"Authorization": f"Bearer {transport.api_key}"})
            ).json()
        ids = sorted(m["id"] for m in listing.get("data") or [])
        print(f"  {len(ids)} models")
        for model_id in ids:
            mark = "  <== configured" if model_id == transport.model else ""
            print(f"    {model_id}{mark}")
        if transport.model not in ids:
            print()
            print(f"  {transport.model} is NOT in the list this key can serve.")
    except BaseException as exc:
        _print_error("models", _describe_exception(exc), indent="  ")


async def main() -> int:
    settings = stage_configuration()
    results = await stage_agents()

    failed = {name: info for name, info in results.items() if info}
    if failed:
        await stage_bisect(settings)
    await stage_models(settings)

    refused = {
        name: layer
        for name, info in failed.items()
        if 400 <= (layer := _provider_layer(info)).get("status_code", 0) < 500
    }

    print()
    print(RULE)
    print("SUMMARY")
    print(RULE)
    for name in ("planner", "explainer", "reporter"):
        info = results.get(name) or {}
        if not info:
            print(f"  {name:10} OK")
            continue
        layer = _provider_layer(info)
        print(
            f"  {name:10} FAILED  HTTP {layer.get('status_code')} "
            f"{layer.get('code')}  param={layer.get('param')}"
        )
        print(f"             {str(layer.get('message') or layer.get('str'))[:150]}")
    if len(failed) == 3:
        print()
        print("  All three agents failed. They share one code path and one set of request")
        print("  parameters, so this is the shared client, not any single agent.")
    elif failed:
        print()
        print(f"  Only {', '.join(sorted(failed))} failed, so the difference is in that")
        print("  agent's prompt or its max_tokens, not in the shared client.")
    print()
    print(f"  4xx (permanent, configuration): {sorted(refused) or 'none'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
