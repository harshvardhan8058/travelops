"""Report Generator reasoning agent — executive summary of a resolved incident or group.

A read-only artifact. It never enters assurance, triggers no action, and authorises nothing.
Called when a group reaches resolved (or on demand via the reports endpoint).

Owner: Stream C.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from app.agents.contract import ModelCallAudit, ReportResponse
from app.llm.client import LLMClient

log = structlog.get_logger(__name__)

PROMPT_VERSION = "report.v1"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "report.v1.md"
GENERATOR = "report-generator"

#: The largest prose artifact: a summary plus four to six sections, roughly 700-1100 tokens.
#: 1800 covers it. See `explainer.MAX_TOKENS` for why 8192 could never be served.
MAX_TOKENS = 1800


def _build_prompt(
    *,
    group_reference: str,
    rollup: dict[str, Any],
    blast_radius: dict[str, Any] | None = None,
    actions_summary: list[dict[str, Any]] | None = None,
    hotel_summary: dict[str, Any] | None = None,
) -> str:
    parts = [
        "## Disruption group",
        f"Reference: {group_reference}",
        "",
        "## Cascade rollup",
    ]
    for key, value in rollup.items():
        parts.append(f"- {key}: {value}")

    if blast_radius:
        parts.append("")
        parts.append("## Blast radius")
        for key, value in blast_radius.items():
            parts.append(f"- {key}: {value}")

    if hotel_summary:
        parts.append("")
        parts.append("## Hotel allocation")
        for key, value in hotel_summary.items():
            parts.append(f"- {key}: {value}")

    if actions_summary:
        parts.append("")
        parts.append("## Recovery actions across the group")
        # Capped at 8 and 60 characters: input counts against the same TPM ceiling as the
        # answer, and 20 near-identical action lines told the model nothing the first few did
        # not. The figures it must cite come from the rollup above, not from this list.
        for action in actions_summary[:8]:
            parts.append(
                f"- {action.get('action_type', '?')}: {action.get('status', '?')} "
                f"| {action.get('reason', '')[:60]}"
            )

    parts.append("")
    parts.append("Produce the executive report as specified in the system prompt.")
    return "\n".join(parts)


class ReportGeneratorAgent:
    def __init__(self, *, client: LLMClient | None = None) -> None:
        self._client = client or LLMClient()

    async def generate(
        self,
        *,
        group_reference: str,
        rollup: dict[str, Any],
        blast_radius: dict[str, Any] | None = None,
        actions_summary: list[dict[str, Any]] | None = None,
        hotel_summary: dict[str, Any] | None = None,
        scenario_key: str = "bengaluru_storm",
    ) -> tuple[ReportResponse, ModelCallAudit]:
        prompt = _build_prompt(
            group_reference=group_reference,
            rollup=rollup,
            blast_radius=blast_radius,
            actions_summary=actions_summary,
            hotel_summary=hotel_summary,
        )
        response, audit = await self._client.call(
            prompt=prompt,
            system=PROMPT_PATH.read_text(encoding="utf-8"),
            response_schema=ReportResponse,
            agent_name="reporter",
            prompt_version=PROMPT_VERSION,
            scenario_key=scenario_key,
            max_tokens=MAX_TOKENS,
        )
        log.info("report_generator_succeeded", group_reference=group_reference)
        return response, audit
