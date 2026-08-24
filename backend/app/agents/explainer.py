"""Explainer agent — justifies a completed recovery in natural language.

A read-only artefact. It enters no gate, authorises nothing and writes nothing. The Decision
Assurance Gate has already run and the actions have already executed by the time this is asked;
an explanation that could change an outcome would be a second decision path.

Uses Stream A's `llm.client.complete_json`, so the failure protocol is A's: `None` means "no
usable model output" and the caller shows the deterministic record instead. `LLM_MODE=off`, a
missing key, a timeout and malformed JSON all reduce to the same thing, and none is an error here.

Owner: Stream C. Client, prompt loading and mode resolution are Stream A's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.contract import ExplanationResponse, ModelCallAudit
from app.llm import client

log = structlog.get_logger(__name__)

PROMPT = "explainer.v1"


@dataclass
class ExplanationArtefact:
    """A validated explanation plus the audit record for the call that produced it."""

    response: ExplanationResponse
    audit: ModelCallAudit
    source: str


def _format_actions(actions: list[dict[str, Any]]) -> list[str]:
    """One line per recorded action. Typed values only — never raw external text."""
    return [
        f"{item.get('action_type', 'unknown')}: {item.get('status', 'unknown')}"
        f" | {str(item.get('reason', ''))[:160]}"
        for item in actions
    ]


def _format_rollup(rollup: dict[str, Any] | None) -> list[str]:
    if not rollup:
        return []
    return [f"{key}={value}" for key, value in sorted(rollup.items())]


async def explain(
    *,
    incident_reference: str,
    actions: list[dict[str, Any]],
    rollup: dict[str, Any] | None = None,
) -> ExplanationArtefact | None:
    """Explain a completed recovery, or return `None` when no model output is available.

    `actions` must be the actions that actually ran, with the reason each recorded. The model is
    given no session, no provider and no tool — only these typed facts.
    """
    result = await client.complete_json(
        prompt_name=PROMPT,
        fields={
            "incident_reference": incident_reference,
            "actions": _format_actions(actions),
            "rollup": _format_rollup(rollup),
        },
    )
    if result is None:
        return None

    try:
        response = ExplanationResponse.model_validate(result.payload)
    except ValidationError as exc:
        # A malformed explanation is discarded, never repaired. The console shows the
        # deterministic record, which is complete without it.
        log.error(
            "explainer_response_invalid",
            outcome="error",
            incident_reference=incident_reference,
            errors=exc.error_count(),
        )
        return None

    log.info(
        "explainer_succeeded",
        incident_reference=incident_reference,
        source=result.source,
        citations=len(response.citation_refs),
    )
    return ExplanationArtefact(response=response, audit=result.audit, source=result.source)
