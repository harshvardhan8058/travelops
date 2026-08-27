"""LLM client layer — Groq OpenAI-compatible, with fixture replay and off-mode.

Three modes, one interface:

    live     — real Groq API call with retry/backoff, structured JSON output, validated against the
               response schema before returning. Requires the configured provider's API key.
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

#: 4xx statuses that are worth trying again. Everything else in the 4xx range is the request
#: being wrong and cannot be fixed by repeating it.
#:
#: 413 is here because Groq answers a token-per-minute overrun with `413 rate_limit_exceeded`,
#: not 429. Treating it as permanent — which is what "retry only 429" did — turned a limit that
#: clears on its own into a hard failure for the whole run.
_TRANSIENT_STATUS = frozenset({408, 413, 429})

#: Reserved completion budget for the planner's small, bounded task list. Prose agents override
#: it per call. These are RESERVATIONS, not observed sizes: Groq bills TPM as
#: `prompt_tokens + max_tokens`, so an oversized ceiling costs the same as actually generating
#: that much. Six planner tasks are roughly 400-600 tokens of JSON, so 1200 is ample.
DEFAULT_MAX_TOKENS = 1200

#: Held back from the TPM ceiling when clamping, to absorb the difference between the estimate
#: below and Groq's real tokeniser.
TPM_SAFETY_MARGIN = 800

#: Below this a prose artifact cannot be completed, so a truncated answer is worse than a clear
#: refusal naming the budget.
MIN_OUTPUT_BUDGET = 512

#: Characters per token, deliberately pessimistic. English prose runs about 4, but these prompts
#: are markdown containing JSON — braces, quotes and backticks tokenise far worse. Measured
#: against the two real 413s (prompts of 710 and 1395 tokens) this over-estimates, which is the
#: safe direction for a ceiling.
_CHARS_PER_TOKEN = 2.5


def _estimate_prompt_tokens(system: str, prompt: str) -> int:
    """Conservative token estimate for the two messages, used only to clamp the budget.

    Not a billing figure and never reported as one. `tiktoken` is not a dependency here and
    adding one to guess a number that Groq computes authoritatively would be the wrong trade —
    the real count comes back in `usage`.
    """
    return int((len(system) + len(prompt)) / _CHARS_PER_TOKEN)


def _output_budget(*, requested: int, prompt_tokens: int, tpm_limit: int) -> int:
    """The largest completion reservation that keeps `prompt + budget` inside the TPM ceiling.

    This exists because the ceiling was previously implicit. `max_tokens=8192` against an 8000
    TPM account is unsatisfiable no matter how short the prompt is, and the failure surfaced as
    HTTP 413 from the provider rather than as anything checkable locally.
    """
    headroom = tpm_limit - prompt_tokens - TPM_SAFETY_MARGIN
    return max(0, min(requested, headroom))


def _coerce_self_report(value: Any, *, agent_name: str) -> int | None:
    """`ModelCallAudit.model_self_report` or nothing, never an exception.

    `ModelCallAudit` types this as an int in 0..100, and the model is told not to send it at
    all. When one arrives anyway it is diagnostic only — the audit's own docstring says it is
    "never used for control flow" and `ProposalAuthorship` does not even have the field. So a
    self-report that does not fit the type is dropped and logged, not raised: failing the whole
    artifact over an unsolicited diagnostic would be the tail wagging the dog.

    Not coerced numerically. A model that sends `0.92` means 92% on a 0-1 scale, and `int(0.92)`
    is 0 — a confident model recorded as a diffident one. Guessing the scale would invent a
    figure, so an out-of-contract value is discarded instead.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        discarded: Any = value
    elif isinstance(value, int) and 0 <= value <= 100:
        return value
    elif isinstance(value, float) and value.is_integer() and 0 <= value <= 100:
        return int(value)
    else:
        discarded = value
    log.info(
        "llm_self_report_discarded",
        agent=agent_name,
        value_type=type(discarded).__name__,
        detail=str(discarded)[:80],
    )
    return None


class LLMUnavailable(Exception):  # noqa: N818 - see below
    """Raised when `LLM_MODE=off` or when the live call exhausts retries.

    Deliberately not `LLMUnavailableError`. It is a public symbol the orchestrator imports and
    catches, and it names a normal operating condition rather than a fault: `LLM_MODE=off` is a
    supported configuration, not an error. Renaming it would ripple through every caller for a
    naming convention, so the convention is suppressed here with the reason attached.

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
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[T, ModelCallAudit]:
        """Invoke the configured mode and return (validated_response, audit).

        Raises `LLMUnavailable` when the model is not reachable or not configured.

        `max_tokens` is per call because the agents are not the same size. The planner returns
        a handful of small task objects; the reporter is asked for four to six sections of
        prose. One ceiling for both truncates the long one mid-object, and truncated JSON
        arrives as `JSONDecodeError` — indistinguishable from a transport fault, so it burns
        all three retries before failing.
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

        return await self._call_provider(
            prompt=prompt,
            system=system,
            response_schema=response_schema,
            agent_name=agent_name,
            prompt_version=prompt_version,
            max_tokens=max_tokens,
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

    async def _call_provider(
        self,
        *,
        prompt: str,
        system: str,
        response_schema: type[T],
        agent_name: str,
        prompt_version: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[T, ModelCallAudit]:
        """Live call to the configured provider, with retry on transient failures.

        One transport for both providers. OpenRouter and Groq both serve
        `openai/gpt-oss-120b` behind the same OpenAI-compatible chat-completions contract, so the
        request below, the JSON-mode handling, the schema validation and the retry classes are
        shared rather than forked.

        The installed SDK is used as the HTTP client for both, pointed at a different `base_url`.
        That is deliberate: hand-rolling a second client over `httpx` would mean re-deriving
        `APIStatusError` and its status codes, which is exactly what the permanent-versus-transient
        distinction below depends on. A second client is how two retry policies start to drift.
        """
        import asyncio

        from groq import APIError, APIStatusError, AsyncGroq

        from app.config import provider_transport

        transport = provider_transport(self._settings)
        if not transport.api_key:
            raise LLMUnavailable(
                f"{transport.key_env_var} is not set; cannot call "
                f"{transport.provider.value} in live mode"
            )

        client_kwargs: dict[str, Any] = {"api_key": transport.api_key}
        if transport.base_url is not None:
            client_kwargs["base_url"] = transport.base_url
            # OpenRouter uses these for attribution on the account's activity page. Optional
            # for it, meaningless to Groq, so only sent when a base_url is in play.
            client_kwargs["default_headers"] = {
                "HTTP-Referer": "https://github.com/harshvardhan8058/travelops",
                "X-Title": "TravelOps AI",
            }

        client = AsyncGroq(**client_kwargs)
        model = transport.model
        temperature = self._settings.groq_temperature

        tpm_limit = transport.tpm_limit
        prompt_tokens_estimate = _estimate_prompt_tokens(system, prompt)
        budget = _output_budget(
            requested=max_tokens, prompt_tokens=prompt_tokens_estimate, tpm_limit=tpm_limit
        )
        if budget < MIN_OUTPUT_BUDGET:
            raise LLMUnavailable(
                f"the {agent_name} prompt leaves no room to answer within the "
                f"{tpm_limit} token-per-minute ceiling: prompt is about "
                f"{prompt_tokens_estimate} tokens, leaving {budget} for the response. "
                f"Shorten the prompt or raise {transport.provider.value.upper()}_TPM_LIMIT."
            )
        if budget < max_tokens:
            log.info(
                "llm_output_budget_clamped",
                agent=agent_name,
                requested=max_tokens,
                granted=budget,
                prompt_tokens_estimate=prompt_tokens_estimate,
                tpm_limit=tpm_limit,
            )
        max_tokens = budget

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
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                latency_ms = int((time.perf_counter() - start) * 1000)
                content = response.choices[0].message.content or "{}"
                usage = response.usage

                raw = json.loads(content)
                parsed = response_schema.model_validate(raw)

                audit = ModelCallAudit(
                    generator=transport.generator,
                    prompt_version=prompt_version,
                    model_self_report=_coerce_self_report(
                        raw.get("model_self_report"), agent_name=agent_name
                    ),
                    input_tokens=usage.prompt_tokens if usage else None,
                    output_tokens=usage.completion_tokens if usage else None,
                    latency_ms=latency_ms,
                )
                log.info(
                    "llm_call_succeeded",
                    agent=agent_name,
                    provider=transport.provider.value,
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
                    f"{transport.provider.value} returned JSON that does not match "
                    f"{response_schema.__name__}: "
                    f"{exc.error_count()} validation errors"
                ) from exc

            except APIStatusError as exc:
                # A 4xx that is not a rate limit is the request itself being wrong — a
                # decommissioned model, an unknown model id, a rejected parameter. Retrying
                # cannot change the answer, and retrying it three times is how the real cause
                # stayed hidden: a decommissioned model reached the operator as "Groq call
                # failed after 3 attempts" instead of the provider saying, in the first
                # sentence of the first response, which model had been retired.
                #
                # Same reasoning as the ValidationError branch above: a permanent failure is
                # reported, not repeated. 429 and 5xx are genuinely transient and still retry.
                if exc.status_code not in _TRANSIENT_STATUS and 400 <= exc.status_code < 500:
                    log.error(
                        "llm_call_refused",
                        agent=agent_name,
                        provider=transport.provider.value,
                        model=model,
                        status_code=exc.status_code,
                        detail=str(exc)[:500],
                    )
                    raise LLMUnavailable(
                        f"{transport.provider.value} refused the request for model '{model}' "
                        f"(HTTP {exc.status_code}): {exc}"
                    ) from exc
                last_error = exc
                log.warning(
                    "llm_call_retrying",
                    agent=agent_name,
                    attempt=attempt + 1,
                    error=type(exc).__name__,
                    status_code=exc.status_code,
                    detail=str(exc)[:200],
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * (attempt + 1))

            except (APIError, json.JSONDecodeError, TimeoutError) as exc:
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
            f"{transport.provider.value} call failed after {MAX_RETRIES + 1} attempts: {last_error}"
        ) from last_error
