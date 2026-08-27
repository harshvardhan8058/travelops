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

#: Generous enough for a reasoning model, short enough that a hung request does not hold an
#: incident's run open indefinitely.
REQUEST_TIMEOUT_SECONDS = 60.0

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


def _extra_key_paths(exc: ValidationError) -> list[tuple[str | int, ...]]:
    """Locations of `extra_forbidden` errors, or `[]` if any other kind of error is present.

    All-or-nothing on purpose. A payload that is rejected for an undeclared key AND for an
    invented action type must stay rejected — the invented action is the part that matters.
    """
    paths: list[tuple[str | int, ...]] = []
    for error in exc.errors():
        if error.get("type") != "extra_forbidden":
            return []
        paths.append(tuple(error["loc"]))
    return paths


def _without_paths(raw: Any, paths: list[tuple[str | int, ...]]) -> Any:
    """Deep copy of `raw` with exactly those locations removed."""
    import copy

    cleaned = copy.deepcopy(raw)
    for path in paths:
        cursor = cleaned
        try:
            for step in path[:-1]:
                cursor = cursor[step]
            del cursor[path[-1]]
        except (KeyError, IndexError, TypeError):
            continue
    return cleaned


def _describe_paths(paths: list[tuple[str | int, ...]]) -> list[str]:
    """`tasks.0.rationale` — names, never values, so nothing from the model reaches the log."""
    return [".".join(str(step) for step in path) for path in paths]


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
    )
    return None


_KNOWN_FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls", "error"})


def _finish_reason_for_log(value: Any) -> str | None:
    """Allowlist provider metadata before it crosses a logging or API boundary."""
    if value is None:
        return None
    if isinstance(value, str) and value in _KNOWN_FINISH_REASONS:
        return value
    return f"unknown_{type(value).__name__}"


def _usage_count_for_log(value: Any) -> int | None:
    """Return a token count only when it is already an integer, never provider text."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _validate_tolerating_decoration[M: BaseModel](
    raw: Any, *, response_schema: type[M], agent_name: str
) -> M:
    """Validate, dropping keys the model volunteered that the schema does not declare.

    Every substantive rule still applies. The closed `ActionType` enum, `target_refs` being
    non-empty, `tasks` being non-empty, `payload_type`, `status`, field types and lengths are all
    unchanged, and a payload that breaks any of them is still refused. Only keys that nothing in
    the system reads are removed, and their names are logged.

    This is what `contract.py` already asks for in its own docstring: "`confidence` is absent by
    design. If a model emits one, store it as `ModelCallAudit.model_self_report` and never branch
    on it." Storing requires surviving validation. `ExplanationResponse` and `ReportResponse` got
    that treatment when they were 500ing on live traffic; `PlannerResponse` did not, so the same
    chatty model that both prose agents tolerate cost the planner its entire candidate — which is
    exactly the asymmetry observed live: prose PASS, planner FAIL.

    Kept as strip-and-record rather than `extra="ignore"` so the contract stays strict, the tests
    asserting a confidence score cannot enter `PlannerResponse` keep holding, and an attempt to
    send one is visible rather than silent.
    """
    try:
        return response_schema.model_validate(raw)
    except ValidationError as exc:
        paths = _extra_key_paths(exc)
        if not paths:
            raise
        cleaned = _without_paths(raw, paths)
        parsed = response_schema.model_validate(cleaned)
        log.info(
            "llm_extra_keys_dropped",
            agent=agent_name,
            schema=response_schema.__name__,
            keys=_describe_paths(paths),
        )
        return parsed


class _ProviderStatusError(Exception):
    """A non-2xx chat-completions response, carrying what the retry policy needs.

    Internal. Replaces the vendor SDK's error class so the transient-versus-permanent decision
    below reads the same for both providers, off one HTTP status.
    """

    def __init__(self, response: Any) -> None:
        self.status_code: int = response.status_code
        self.body: str = (response.text or "")[:800]
        code: str | None = None
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = error.get("code") or error.get("type")
                message = error.get("message") or self.body
            else:
                message = self.body
        except Exception:
            message = self.body
        self.code = code
        super().__init__(f"HTTP {self.status_code}{f' {code}' if code else ''}: {message}")


class _ProviderPayloadError(Exception):
    """A provider HTTP response that cannot be decoded into assistant JSON."""

    def __init__(
        self,
        phase: str,
        error: BaseException,
        *,
        json_error_position: int | None = None,
    ) -> None:
        self.phase = phase
        self.error = error
        self.json_error_position = json_error_position
        super().__init__(str(error))


class LLMUnavailable(Exception):  # noqa: N818 - see below
    """Raised when `LLM_MODE=off` or when the live call cannot produce a valid artifact.

    `phase` is diagnostic metadata only. Callers still catch this one public type and preserve
    their existing fallback/503 behavior; the phase lets the HTTP boundary say whether a provider
    200 failed in its envelope, assistant JSON, response schema, or audit metadata without logging
    any model content.
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str = "unknown",
        status_code: int | None = None,
        finish_reason: str | None = None,
        content_length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.status_code = status_code
        self.finish_reason = finish_reason
        self.content_length = content_length


def _live_failure(
    message: str,
    *,
    agent_name: str,
    provider: str,
    model: str,
    phase: str,
    attempt: int = 0,
    status_code: int | None = None,
    finish_reason: str | None = None,
    content_length: int | None = None,
    error: BaseException | None = None,
) -> LLMUnavailable:
    """Log one terminal live-call boundary without secrets, prompts, or model content."""
    log.error(
        "llm_call_failed",
        agent=agent_name,
        provider=provider,
        model=model,
        phase=phase,
        attempt=attempt,
        status_code=status_code,
        finish_reason=finish_reason,
        content_length=content_length,
        error=type(error).__name__ if error is not None else None,
    )
    return LLMUnavailable(
        message,
        phase=phase,
        status_code=status_code,
        finish_reason=finish_reason,
        content_length=content_length,
    )


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

        The request is issued with `httpx` against `transport.endpoint_url`, which is the full
        URL. It previously went through a vendor SDK given a base URL, and that SDK appended its
        own `/openai/v1/chat/completions`, so OpenRouter received
        `https://openrouter.ai/api/v1/openai/v1/chat/completions` and answered 404 to every live
        call. No base URL could have fixed it — the path was being assembled by a library that
        assumed its own host's layout. Stating the URL is the fix, and `httpx` is already a
        dependency, so this is still one client and one code path, not a second one.
        """
        import asyncio

        import httpx

        from app.config import provider_transport

        transport = provider_transport(self._settings)
        model = transport.model
        provider = transport.provider.value
        if not transport.api_key:
            raise _live_failure(
                f"{transport.key_env_var} is not set; cannot call {provider} in live mode",
                agent_name=agent_name,
                provider=provider,
                model=model,
                phase="preflight_missing_key",
            )

        headers = {
            "Authorization": f"Bearer {transport.api_key}",
            "Content-Type": "application/json",
            **transport.extra_headers,
        }
        temperature = self._settings.groq_temperature

        tpm_limit = transport.tpm_limit
        prompt_tokens_estimate = _estimate_prompt_tokens(system, prompt)
        budget = _output_budget(
            requested=max_tokens, prompt_tokens=prompt_tokens_estimate, tpm_limit=tpm_limit
        )
        if budget < MIN_OUTPUT_BUDGET:
            raise _live_failure(
                f"the {agent_name} prompt leaves no room to answer within the "
                f"{tpm_limit} token-per-minute ceiling: prompt is about "
                f"{prompt_tokens_estimate} tokens, leaving {budget} for the response. "
                f"Shorten the prompt or raise {provider.upper()}_TPM_LIMIT.",
                agent_name=agent_name,
                provider=provider,
                model=model,
                phase="preflight_output_budget",
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
        last_phase = "transport"
        last_status_code: int | None = None
        last_finish_reason: str | None = None
        last_content_length: int | None = None
        for attempt in range(MAX_RETRIES + 1):
            attempt_number = attempt + 1
            last_status_code = None
            last_finish_reason = None
            last_content_length = None
            start = time.perf_counter()
            log.info(
                "llm_call_started",
                agent=agent_name,
                provider=provider,
                model=model,
                endpoint=transport.endpoint_url,
                attempt=attempt_number,
                max_tokens=max_tokens,
            )
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
                    response = await http.post(
                        transport.endpoint_url,
                        headers=headers,
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "response_format": {"type": "json_object"},
                        },
                    )
                latency_ms = int((time.perf_counter() - start) * 1000)
                response_text = getattr(response, "text", "") or ""
                last_status_code = response.status_code
                log.info(
                    "llm_provider_response_received",
                    agent=agent_name,
                    provider=provider,
                    model=model,
                    attempt=attempt_number,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    body_bytes=len(response_text.encode("utf-8")),
                )
                if response.status_code >= 400:
                    raise _ProviderStatusError(response)

                try:
                    body = response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise _ProviderPayloadError(
                        "provider_json",
                        exc,
                        json_error_position=getattr(exc, "pos", None),
                    ) from exc

                try:
                    if not isinstance(body, dict):
                        raise TypeError("provider response body is not an object")
                    choice = body["choices"][0]
                    if not isinstance(choice, dict):
                        raise TypeError("provider choice is not an object")
                    message = choice["message"]
                    if not isinstance(message, dict):
                        raise TypeError("provider message is not an object")
                    returned_content = message.get("content") or "{}"
                    if not isinstance(returned_content, str):
                        raise TypeError("provider message content is not text")
                    content = returned_content.strip()
                    usage = body.get("usage") or {}
                    if not isinstance(usage, dict):
                        raise TypeError("provider usage is not an object")
                    raw_finish_reason = choice.get("finish_reason")
                    finish_reason = _finish_reason_for_log(raw_finish_reason)
                except (KeyError, IndexError, TypeError, AttributeError) as exc:
                    raise _ProviderPayloadError("provider_envelope", exc) from exc

                content_length = len(content)
                last_finish_reason = str(finish_reason) if finish_reason is not None else None
                last_content_length = content_length
                completion_details = usage.get("completion_tokens_details") or {}
                if not isinstance(completion_details, dict):
                    completion_details = {}
                log.info(
                    "llm_provider_content_received",
                    agent=agent_name,
                    provider=provider,
                    model=model,
                    attempt=attempt_number,
                    status_code=response.status_code,
                    finish_reason=finish_reason,
                    content_length=content_length,
                    input_tokens=_usage_count_for_log(usage.get("prompt_tokens")),
                    output_tokens=_usage_count_for_log(usage.get("completion_tokens")),
                    reasoning_tokens=_usage_count_for_log(
                        completion_details.get("reasoning_tokens")
                    ),
                )
                if finish_reason == "length":
                    raise _live_failure(
                        f"{provider} truncated the {agent_name} response at "
                        f"max_tokens={max_tokens} (finish_reason=length); "
                        f"{content_length} characters returned",
                        agent_name=agent_name,
                        provider=provider,
                        model=model,
                        phase="truncated",
                        attempt=attempt_number,
                        status_code=response.status_code,
                        finish_reason=str(finish_reason),
                        content_length=content_length,
                    )

                try:
                    raw = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise _ProviderPayloadError(
                        "content_json", exc, json_error_position=exc.pos
                    ) from exc

                try:
                    parsed = _validate_tolerating_decoration(
                        raw,
                        response_schema=response_schema,
                        agent_name=agent_name,
                    )
                except ValidationError as exc:
                    # A schema failure is deterministic enough that repeating the same prompt is
                    # noise. Name the schema, fields and kinds, never their model-supplied values.
                    fields = _describe_paths([tuple(e["loc"]) for e in exc.errors()])
                    log.warning(
                        "llm_schema_validation_failed",
                        agent=agent_name,
                        provider=provider,
                        model=model,
                        phase="response_schema",
                        schema=response_schema.__name__,
                        attempt=attempt_number,
                        status_code=response.status_code,
                        finish_reason=finish_reason,
                        content_length=content_length,
                        errors=exc.error_count(),
                        fields=fields,
                        kinds=sorted({str(e.get("type")) for e in exc.errors()}),
                    )
                    raise _live_failure(
                        f"{provider} returned JSON that does not match "
                        f"{response_schema.__name__}: {exc.error_count()} validation errors at "
                        f"{', '.join(fields[:6])}",
                        agent_name=agent_name,
                        provider=provider,
                        model=model,
                        phase="response_schema",
                        attempt=attempt_number,
                        status_code=response.status_code,
                        finish_reason=(str(finish_reason) if finish_reason is not None else None),
                        content_length=content_length,
                        error=exc,
                    ) from exc

                try:
                    audit = ModelCallAudit(
                        generator=transport.generator,
                        prompt_version=prompt_version,
                        model_self_report=_coerce_self_report(
                            raw.get("model_self_report"), agent_name=agent_name
                        ),
                        input_tokens=usage.get("prompt_tokens"),
                        output_tokens=usage.get("completion_tokens"),
                        latency_ms=latency_ms,
                    )
                except ValidationError as exc:
                    fields = _describe_paths([tuple(e["loc"]) for e in exc.errors()])
                    log.warning(
                        "llm_schema_validation_failed",
                        agent=agent_name,
                        provider=provider,
                        model=model,
                        phase="audit_schema",
                        schema=ModelCallAudit.__name__,
                        attempt=attempt_number,
                        status_code=response.status_code,
                        finish_reason=finish_reason,
                        content_length=content_length,
                        errors=exc.error_count(),
                        fields=fields,
                        kinds=sorted({str(e.get("type")) for e in exc.errors()}),
                    )
                    raise _live_failure(
                        f"{provider} returned invalid usage metadata for "
                        f"{ModelCallAudit.__name__}: {exc.error_count()} validation errors at "
                        f"{', '.join(fields[:6])}",
                        agent_name=agent_name,
                        provider=provider,
                        model=model,
                        phase="audit_schema",
                        attempt=attempt_number,
                        status_code=response.status_code,
                        finish_reason=(str(finish_reason) if finish_reason is not None else None),
                        content_length=content_length,
                        error=exc,
                    ) from exc

                log.info(
                    "llm_call_succeeded",
                    agent=agent_name,
                    provider=provider,
                    model=model,
                    attempt=attempt_number,
                    latency_ms=latency_ms,
                    input_tokens=audit.input_tokens,
                    output_tokens=audit.output_tokens,
                )
                return parsed, audit

            except _ProviderStatusError as exc:
                last_phase = "provider_status"
                last_status_code = exc.status_code
                if exc.status_code not in _TRANSIENT_STATUS and 400 <= exc.status_code < 500:
                    log.error(
                        "llm_call_refused",
                        agent=agent_name,
                        provider=provider,
                        model=model,
                        status_code=exc.status_code,
                        detail=str(exc)[:500],
                    )
                    raise _live_failure(
                        f"{provider} refused the request for model '{model}' "
                        f"(HTTP {exc.status_code}): {exc}",
                        agent_name=agent_name,
                        provider=provider,
                        model=model,
                        phase=last_phase,
                        attempt=attempt_number,
                        status_code=exc.status_code,
                        error=exc,
                    ) from exc
                last_error = exc
                log.warning(
                    "llm_call_retrying",
                    agent=agent_name,
                    provider=provider,
                    model=model,
                    phase=last_phase,
                    attempt=attempt_number,
                    error=type(exc).__name__,
                    status_code=exc.status_code,
                    detail=str(exc)[:200],
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * attempt_number)

            except _ProviderPayloadError as exc:
                last_error = exc
                last_phase = exc.phase
                log.warning(
                    "llm_call_retrying",
                    agent=agent_name,
                    provider=provider,
                    model=model,
                    phase=exc.phase,
                    attempt=attempt_number,
                    status_code=last_status_code,
                    finish_reason=last_finish_reason,
                    content_length=last_content_length,
                    error=type(exc.error).__name__,
                    json_error_position=exc.json_error_position,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * attempt_number)

            except (httpx.HTTPError, TimeoutError) as exc:
                last_error = exc
                last_phase = "transport"
                last_status_code = None
                log.warning(
                    "llm_call_retrying",
                    agent=agent_name,
                    provider=provider,
                    model=model,
                    phase=last_phase,
                    attempt=attempt_number,
                    error=type(exc).__name__,
                    status_code=None,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS * attempt_number)

        raise _live_failure(
            f"{provider} call failed after {MAX_RETRIES + 1} attempts: {last_error}",
            agent_name=agent_name,
            provider=provider,
            model=model,
            phase=last_phase,
            attempt=MAX_RETRIES + 1,
            status_code=last_status_code,
            finish_reason=last_finish_reason,
            content_length=last_content_length,
            error=last_error,
        ) from last_error
