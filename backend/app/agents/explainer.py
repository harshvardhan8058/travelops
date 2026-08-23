"""Explainer reasoning agent — justifies a completed recovery plan in natural language.

A read-only artifact. It never enters assurance, triggers no action, and authorises nothing.
It is called after an incident reaches resolved, and its output is stored alongside the plan.

Owner: Stream C.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from app.agents.contract import ExplanationResponse, ModelCallAudit
from app.llm.client import LLMClient

log = structlog.get_logger(__name__)

PROMPT_VERSION = "explainer.v1"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "explainer.v1.md"
GENERATOR = "explainer-agent"

SYSTEM_PROMPT = """\
You are the Recovery Explainer. Given a completed recovery plan and its outcomes, produce a
clear natural-language explanation of what happened, why each action was taken, and what the
results were. Cite evidence references for every claim.

Return JSON matching this schema:
{
  "status": "success",
  "reason": "...",
  "evidence_refs": ["action:1", ...],
  "payload_type": "explanation.v1",
  "explanation": "... multi-paragraph explanation ...",
  "citation_refs": ["action:check_connections:1", ...]
}

Rules:
- Every factual claim must reference a recorded action or metric.
- Never invent figures — use only what is provided in the context.
- Write for an operations manager, not a developer.
"""


def _build_prompt(
    *,
    incident_reference: str,
    actions_summary: list[dict[str, Any]],
    rollup: dict[str, Any] | None = None,
) -> str:
    parts = [
        "## Incident",
        f"Reference: {incident_reference}",
        "",
        "## Completed actions",
    ]
    for action in actions_summary:
        parts.append(
            f"- {action.get('action_type', '?')}: {action.get('status', '?')} "
            f"| {action.get('reason', '')[:120]}"
        )
    if rollup:
        parts.append("")
        parts.append("## Group rollup")
        for key, value in rollup.items():
            parts.append(f"- {key}: {value}")
    parts.append("")
    parts.append("Produce the explanation as specified in the system prompt.")
    return "\n".join(parts)


class ExplainerAgent:
    def __init__(self, *, client: LLMClient | None = None) -> None:
        self._client = client or LLMClient()

    async def explain(
        self,
        *,
        incident_reference: str,
        actions_summary: list[dict[str, Any]],
        rollup: dict[str, Any] | None = None,
        scenario_key: str = "bengaluru_storm",
    ) -> tuple[ExplanationResponse, ModelCallAudit]:
        prompt = _build_prompt(
            incident_reference=incident_reference,
            actions_summary=actions_summary,
            rollup=rollup,
        )
        response, audit = await self._client.call(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            response_schema=ExplanationResponse,
            agent_name="explainer",
            prompt_version=PROMPT_VERSION,
            scenario_key=scenario_key,
        )
        log.info("explainer_agent_succeeded", incident_reference=incident_reference)
        return response, audit
