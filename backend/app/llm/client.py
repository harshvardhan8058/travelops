"""The single LLM client. One place that can call a model.

Three modes, resolved by `app.config`:

| Mode | Behaviour |
| --- | --- |
| `off` | No call, ever. Returns `None`; the caller uses its deterministic path |
| `fixture` | No network. Returns the committed response stored next to the prompt, so the
  parse, validate and reflect path runs on every machine and in CI |
| `live` | Calls Groq with bounded tokens and near-zero temperature |

Rules this enforces so callers cannot get them wrong:

- **JSON only.** `response_format={"type": "json_object"}` on live calls, and a parse failure is a
  failure — never salvaged by string-scraping, because a half-parsed plan is worse than no plan.
- **Bounded.** A timeout and a token ceiling, so a slow model degrades to the deterministic path
  instead of holding an incident open. `docs/04`: never let an LLM timeout mean passengers are not
  notified.
- **Deterministic-ish.** Temperature from config, default 0.1.
- **Versioned prompts.** Loaded from `app/llm/prompts/<name>.md`, never inline. A prompt change is a
  new file, because `plan.prompt_version` records which one produced a plan.
- **No control flow from the model.** This returns text and audit metadata. It never decides
  anything.

Owner: Stream A.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from app.agents.contract import ModelCallAudit
from app.config import LLMMode, Settings, get_modes, get_settings

log = structlog.get_logger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"

#: Hard ceiling regardless of config. A planner needs a few hundred tokens; anything beyond this is
#: runaway generation, and paying for it during a demo is the least of the problems.
MAX_OUTPUT_TOKENS = 1024

#: Wall-clock budget for one call. Past this the deterministic path is simply better.
TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class LLMResult:
    """Raw model output plus the audit record for it. No decisions, no parsing beyond JSON."""

    payload: dict[str, Any]
    audit: ModelCallAudit
    #: `fixture` or `live`. Recorded so a reviewer can tell which produced a plan.
    source: str


def load_prompt(name: str) -> str:
    """Read a versioned prompt. Raises if it is missing, because a silent default is worse."""
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt artefact not found: {path}")
    return path.read_text(encoding="utf-8")


def render(template: str, fields: dict[str, Any]) -> str:
    """Substitute `{{field}}` placeholders from typed values only.

    Deliberately not a template engine. The planner receives typed fields, and a substitution that
    can execute logic is an instruction channel by another name.
    """
    rendered = template
    for key, value in fields.items():
        if isinstance(value, list | tuple):
            text = ", ".join(str(item) for item in value) or "none"
        elif value is None:
            text = "not recorded"
        else:
            text = str(value)
        rendered = rendered.replace(f"{{{{{key}}}}}", text)
    return rendered


def _fixture_for(name: str) -> LLMResult | None:
    path = PROMPT_DIR / f"{name}.fixture.json"
    if not path.is_file():
        log.error("llm_fixture_missing", outcome="error", prompt=name, path=str(path))
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return LLMResult(
        payload=data["response"],
        audit=ModelCallAudit(**data.get("audit", {"generator": "planner-agent"})),
        source="fixture",
    )


async def complete_json(
    *,
    prompt_name: str,
    fields: dict[str, Any],
    settings: Settings | None = None,
) -> LLMResult | None:
    """One structured completion, or `None` when the caller must use its deterministic path.

    `None` is a normal outcome, not an error: `LLM_MODE=off`, a missing key, a timeout, malformed
    JSON and a refusal all reduce to "no usable model output", and every caller already has a
    deterministic answer. Returning `None` rather than raising is what keeps a model failure from
    becoming an incident failure.
    """
    settings = settings or get_settings()
    mode = get_modes().llm

    if mode is LLMMode.off:
        return None
    if mode is LLMMode.fixture:
        return _fixture_for(prompt_name)

    prompt = render(load_prompt(prompt_name), fields)
    started = time.perf_counter()
    try:
        payload, usage = await _call_groq(prompt, settings)
    except Exception as exc:
        # Any failure is the same failure from the caller's point of view.
        log.error(
            "llm_call_failed",
            outcome="error",
            prompt=prompt_name,
            detail=type(exc).__name__,
            reason=str(exc)[:200],
        )
        return None

    latency_ms = int((time.perf_counter() - started) * 1000)
    self_report = payload.pop("confidence", None)
    return LLMResult(
        payload=payload,
        audit=ModelCallAudit(
            generator="planner-agent",
            prompt_version=prompt_name,
            # Recorded only so the record can show it did not decide anything.
            model_self_report=self_report if isinstance(self_report, int) else None,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        ),
        source="live",
    )


async def _call_groq(prompt: str, settings: Settings) -> tuple[dict[str, Any], dict[str, int]]:
    """The only network call in the codebase that talks to a model."""
    import httpx

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": settings.groq_model,
                "temperature": settings.groq_temperature,
                "max_tokens": MAX_OUTPUT_TOKENS,
                # Structured output is enforced by the API, not by hoping.
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        body = response.json()

    content = body["choices"][0]["message"]["content"]
    # A parse failure raises and is caught by the caller. Never scraped.
    return json.loads(content), body.get("usage") or {}
