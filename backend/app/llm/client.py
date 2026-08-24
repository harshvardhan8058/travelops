"""LLM client layer — Groq OpenAI-compatible, with fixture replay and off-mode.

Three modes, one interface:

    live     — real Groq API call with retry/backoff, structured JSON output, validated against the
               response schema before returning. Requires `GROQ_API_KEY`.
    fixture  — replays a committed JSON response keyed by (agent_name, prompt_version, scenario).
               Deterministic, zero-latency, no network. This is the test oracle.
    off      — raises `LLMUnavailable` immediately. The engine falls back to the deterministic
               playbook, which is the Phase 1/2 path and the submission's demo moment.

The client produces a `ModelCallAudit` on every call regardless of mode, so the plan records
which generator was involved, how much it cost, and whether it was a fixture replay.

Owner: Stream C.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from app.agents.contract import ModelCallAudit
from app.config import LLMMode, get_settings

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# Retry settings for live calls. Transient failures (rate limit, timeout) retry; schema
# validation failures do not, because the same prompt will produce the same shape.
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 1.5


class LLMUnavailable(Exception):
    """Raised when `LLM_MODE=off` or when the live call exhausts retries.

    The engine catches this and falls back to the playbook. It is never fatal.
    """


class LLMClient:
    """Single entry point for all reasoning-agent calls."""

    def __init__(self, *, mode: LLMMode | None = None, settings: Any = None) -> None:
        self._settings = settings or get_settings()
        self._mode = mode if mode is not None else LLMMode(self._settings.llm_mode)

    async def call(
        self,
        *,
        prompt: str,
        system: str,
        response_schema: type[T],
        agent_name: str,
        prompt_version: str,
        scenario_key: str = "bengaluru_storm",
    ) -> tuple[T, ModelCallAudit]:
        """Invoke the configured mode and return (validated_response, audit).

        Raises `LLMUnavailable` when the model is not reachable or not configured.
        """
        if self._mode is LLMMode.off:
            raise LLMUnavailable("LLM_MODE=off; falling back to deterministic playbook")

        if self._mode is LLMMode.fixture:
            return self._replay_fixture(
                agent_name=agent_name,
                prompt_version=prompt_version,
                scenario_key=scenario_key,
                response_schema=response_schema,
            )

        return await self._call_groq(
            prompt=prompt,
            system=system,
            response_schema=response_schema,
            agent_name=agent_name,
            prompt_version=prompt_version,
        )

    def _replay_fixture(
        self,
        *,
        agent_name: str,
        prompt_version: str,
        scenario_key: str,
        response_schema: type[T],
    ) -> tuple[T, ModelCallAudit]:
        """Load and validate a committed fixture response."""
        fixture_path = FIXTURE_DIR / f"{agent_name}_{prompt_version}_{scenario_key}.json"
        if not fixture_path.exists():
            raise LLMUnavailable(
                f"fixture not found: {fixture_path.name}. "
                f"Commit the fixture or switch to live mode."
            )

        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        try:
            parsed = response_schema.model_validate(raw)
        except ValidationError as exc:
            raise LLMUnavailable(
                f"fixture {fixture_path.name} does not validate against "
                f"{response_schema.__name__}: {exc.error_count()} errors"
            ) from exc

        audit = ModelCallAudit(
            generator=f"fixture:{agent_name}",
            prompt_version=prompt_version,
            model_self_report=None,
            input_tokens=None,
            output_tokens=None,
            latency_ms=0,
        )
        log.info(
            "llm_fixture_replayed",
            agent=agent_name,
            prompt_version=prompt_version,
            fixture=fixture_path.name,
        )
        return parsed, audit

    async def _call_groq(
        self,
        *,
        prompt: str,
        system: str,
        response_schema: type[T],
        agent_name: str,
        prompt_version: str,
    ) -> tuple[T, ModelCallAudit]:
        """Live Groq call with retry on transient failures."""
        import asyncio

        from groq import APIError, AsyncGroq, RateLimitError

        api_key = self._settings.groq_api_key
        if not api_key:
            raise LLMUnavailable("GROQ_API_KEY is not set; cannot call Groq in live mode")

        client = AsyncGroq(api_key=api_key)
        model = self._settings.groq_model
        temperature = self._settings.groq_temperature

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=4096,
                    response_format={"type": "json_object"},
                )
                latency_ms = int((time.perf_counter() - start) * 1000)
                content = response.choices[0].message.content or "{}"
                usage = response.usage

                raw = json.loads(content)
                parsed = response_schema.model_validate(raw)

                audit = ModelCallAudit(
                    generator=f"groq:{model}",
                    prompt_version=prompt_version,
                    model_self_report=raw.get("model_self_report"),
                    input_tokens=usage.prompt_tokens if usage else None,
                    output_tokens=usage.completion_tokens if usage else None,
                    latency_ms=latency_ms,
                )
                log.info(
                    "llm_call_succeeded",
                    agent=agent_name,
                    model=model,
                    attempt=attempt + 1,
                    latency_ms=latency_ms,
                    input_tokens=audit.input_tokens,
                    output_tokens=audit.output_tokens,
                )
                return parsed, audit

            except ValidationError as exc:
                # Schema failure: the model returned parseable JSON that does not match the
                # contract. Retrying will likely produce the same shape, so fail immediately.
                latency_ms = int((time.perf_counter() - start) * 1000)
                log.warning(
                    "llm_schema_validation_failed",
                    agent=agent_name,
                    attempt=attempt + 1,
                    errors=exc.error_count(),
                )
                raise LLMUnavailable(
                    f"Groq returned JSON that does not match {response_schema.__name__}: "
                    f"{exc.error_count()} validation errors"
                ) from exc

            except (RateLimitError, APIError, json.JSONDecodeError, TimeoutError) as exc:
                last_error = exc
                log.warning(
                    "llm_call_retrying",
                    agent=agent_name,
                    attempt=attempt + 1,
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

        raise LLMUnavailable(
            f"Groq call failed after {MAX_RETRIES + 1} attempts: {last_error}"
        ) from last_error
