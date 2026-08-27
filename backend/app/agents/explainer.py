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

#: Reserved completion budget. Two to four paragraphs plus the envelope is roughly 400-700
#: tokens, so 1400 leaves room without reserving a minute's worth of an 8000 TPM account.
#: 8192 here was unsatisfiable: Groq bills `prompt_tokens + max_tokens`, so a 710-token prompt
#: asked for 8902 against a 8000 ceiling and came back 413 every time.
MAX_TOKENS = 1400


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
    # 80 characters of each recorded reason. The prompt counts against the same TPM ceiling as
    # the answer, and the explanation cites the action, not the full text of its reason.
    for action in actions_summary:
        parts.append(
            f"- {action.get('action_type', '?')}: {action.get('status', '?')} "
            f"| {action.get('reason', '')[:80]}"
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
            system=PROMPT_PATH.read_text(encoding="utf-8"),
            response_schema=ExplanationResponse,
            agent_name="explainer",
            prompt_version=PROMPT_VERSION,
            scenario_key=scenario_key,
            max_tokens=MAX_TOKENS,
        )
        log.info("explainer_agent_succeeded", incident_reference=incident_reference)
        return response, audit
