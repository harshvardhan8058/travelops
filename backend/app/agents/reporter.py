"""Report Generator agent — executive summary of a resolved disruption.

A read-only artefact, on the same terms as the Explainer: no gate, no authorisation, no write. It
summarises figures the rollup already derived; it never computes one. A number in a report that
cannot be traced to `metric_refs` is a number nobody can defend in a review.

Uses Stream A's `llm.client.complete_json`, so `None` means "no usable model output" and the caller
falls back to the recorded figures.

Owner: Stream C. Client, prompt loading and mode resolution are Stream A's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.contract import ModelCallAudit, ReportResponse

log = structlog.get_logger(__name__)

PROMPT = "report.v1"


@dataclass
class ReportArtefact:
    """A validated report plus the audit record for the call that produced it."""

    response: ReportResponse
    audit: ModelCallAudit
    source: str


def _format_mapping(values: dict[str, Any] | None) -> list[str]:
    if not values:
        return []
    return [
        f"{key}={value}"
        for key, value in sorted(values.items())
        # Nested structures are not flattened into a prompt: a list of allocations is not a
        # figure, and offering it invites the model to aggregate one.
        if not isinstance(value, (list, dict))
    ]


async def generate(
    *,
    reference: str,
    rollup: dict[str, Any],
    hotel_summary: dict[str, Any] | None = None,
) -> ReportArtefact | None:
    """Produce an executive report, or `None` when no model output is available."""
    from app.llm import client

    result = await client.complete_json(
        prompt_name=PROMPT,
        fields={
            "reference": reference,
            "rollup": _format_mapping(rollup),
            "hotel_summary": _format_mapping(hotel_summary),
        },
    )
    if result is None:
        return None

    try:
        response = ReportResponse.model_validate(result.payload)
    except ValidationError as exc:
        log.error(
            "report_response_invalid",
            outcome="error",
            reference=reference,
            errors=exc.error_count(),
        )
        return None

    log.info(
        "report_generator_succeeded",
        reference=reference,
        source=result.source,
        sections=len(response.sections),
        metrics=len(response.metric_refs),
    )
    return ReportArtefact(response=response, audit=result.audit, source=result.source)
