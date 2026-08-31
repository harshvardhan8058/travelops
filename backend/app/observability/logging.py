"""Structured logging.

Required fields on every record: timestamp, level, service, event, correlation_id,
incident_id, actor, duration_ms, outcome, error_code — whichever apply.

Never log secrets, raw personal addresses, or full prompt context.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

SERVICE_NAME = "travelops-api"

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
incident_id_var: ContextVar[str | None] = ContextVar("incident_id", default=None)

_REDACTED = "[redacted]"
_SENSITIVE_KEYS = frozenset(
    {
        "groq_api_key",
        "openrouter_api_key",
        "api_key",
        "smtp_password",
        "password",
        "authorization",
        "token",
        "secret",
    }
)


def _add_service(_logger: object, _name: str, event_dict: dict) -> dict:
    event_dict["service"] = SERVICE_NAME
    return event_dict


def _add_context(_logger: object, _name: str, event_dict: dict) -> dict:
    correlation_id = correlation_id_var.get()
    if correlation_id and "correlation_id" not in event_dict:
        event_dict["correlation_id"] = correlation_id
    incident_id = incident_id_var.get()
    if incident_id and "incident_id" not in event_dict:
        event_dict["incident_id"] = incident_id
    return event_dict


def _redact(_logger: object, _name: str, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


#: Third-party loggers held at WARNING because their INFO output leaks a secret.
#:
#: `httpx` logs every request at INFO as `HTTP Request: GET <full url> "<status>"`, and a full URL
#: includes the query string. AviationStack authenticates with `access_key` **as a query
#: parameter** — it offers no header alternative — so at the service's default `LOG_LEVEL=INFO`
#: every live flight-status call wrote the API key into the application log, where it would then
#: travel into any log aggregator, screen share or pasted excerpt.
#:
#: The redaction processor above cannot catch this: it inspects structlog event keys, and this is a
#: pre-formatted message from the standard-library logger with the secret embedded in a string.
#:
#: Nothing diagnostic is lost. The client already emits its own structured events for the same
#: calls — `llm_call_started` and `llm_provider_response_received` carry the endpoint, attempt,
#: status code and latency, and the endpoint is recorded WITHOUT its query string precisely so it
#: stays safe to log. WARNING and above still propagate, so httpx's own transport failures remain
#: visible.
#:
#: Bearer-token providers (OpenRouter, Groq) were never exposed this way, because httpx does not
#: log headers. This is specifically about credentials that a vendor requires in the URL.
_QUIET_THIRD_PARTY_LOGGERS = ("httpx", "httpcore")


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    for name in _QUIET_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_service,
            _add_context,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    return structlog.get_logger(name)
