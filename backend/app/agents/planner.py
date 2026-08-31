"""Planner reasoning agent — produces a recovery plan from incident context + precedent.

The planner is an IMPROVEMENT on the deterministic playbook, not a replacement for it. The playbook
plan is always created first; this produces a second candidate alongside it. If this fails for any
reason — network, rate limit, malformed output, validation error — the playbook plan is already
persisted and the system continues unchanged.

The planner:
  - receives typed structured fields (never raw external text)
  - returns a validated `PlannerResponse` against the closed `ActionType` enum
  - may reference precedent incidents to inform ordering and task selection
  - never executes anything — every proposed task still passes the existing assurance gate

Owner: Stream C.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from app.agents.contract import ModelCallAudit, PlannerResponse
from app.llm.client import PLANNER_WIRE_SCHEMA, LLMClient

log = structlog.get_logger(__name__)

PROMPT_VERSION = "planner.v1"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "planner.v1.md"
GENERATOR = "planner-agent"


def _build_prompt(
    *,
    incident_reference: str,
    flight_id: int | None,
    flight_number: str | None,
    route: str | None,
    delay_minutes: int | None,
    trigger_type: str,
    severity: str,
    airport_icao: str,
    passengers_affected: int | None,
    connections_at_risk: int | None,
    crew_pairings_affected: int | None,
    precedents: list[dict[str, Any]],
) -> str:
    """Build the user prompt from typed fields. Never raw text from the outside."""
    parts = [
        "## Incident",
        f"- Reference: {incident_reference}",
        f"- Flight: {flight_number or 'unknown'} (id={flight_id})",
        f"- Route: {route or 'unknown'}",
        f"- Delay: {delay_minutes or '?'} minutes",
        f"- Trigger: {trigger_type}",
        f"- Severity: {severity}",
        f"- Airport: {airport_icao}",
        "",
        "## Affected entities",
        f"- Passengers: {passengers_affected or '?'}",
        f"- Connections at risk: {connections_at_risk or '?'}",
        f"- Crew pairings: {crew_pairings_affected or '?'}",
    ]

    if precedents:
        parts.append("")
        parts.append("## Precedent incidents (resolved successfully at this airport)")
        for prec in precedents[:3]:
            parts.append(
                f"- {prec.get('incident_reference', '?')}: "
                f"trigger={prec.get('trigger_type', '?')}, "
                f"severity={prec.get('severity', '?')}, "
                f"outcome={prec.get('outcome_state', '?')}"
            )
            if prec.get("match_reasons"):
                parts.append(f"  Match reasons: {', '.join(prec['match_reasons'])}")

    parts.append("")
    parts.append("## Instructions")
    parts.append("Produce a recovery plan as specified in the system prompt.")
    parts.append(
        f'Use target_refs: ["incident:{incident_reference}"'
        + (f', "flight:{flight_id}"]' if flight_id else "]")
    )

    return "\n".join(parts)


class PlannerAgent:
    """Invokes the planner reasoning agent and returns a validated response."""

    def __init__(self, *, client: LLMClient | None = None) -> None:
        self._client = client or LLMClient()

    async def propose(
        self,
        *,
        incident_reference: str,
        flight_id: int | None = None,
        flight_number: str | None = None,
        route: str | None = None,
        delay_minutes: int | None = None,
        trigger_type: str = "weather",
        severity: str = "high",
        airport_icao: str = "VOBL",
        passengers_affected: int | None = None,
        connections_at_risk: int | None = None,
        crew_pairings_affected: int | None = None,
        precedents: list[dict[str, Any]] | None = None,
        scenario_key: str = "bengaluru_storm",
        budget_seconds: float | None = None,
    ) -> tuple[PlannerResponse, ModelCallAudit]:
        """Call the planner and return a validated plan + audit metadata.

        Raises `LLMUnavailable` if the call cannot be made or the response is invalid.

        `budget_seconds` is the orchestrator's wall-clock allowance for this candidate. It is
        handed to the client rather than enforced by cancelling this coroutine, so the client can
        size its attempts and its retries to fit — a bound applied from outside can only destroy an
        in-flight attempt, which is how a healthy-but-slow first call became no candidate at all.
        """
        system = PROMPT_PATH.read_text(encoding="utf-8")
        prompt = _build_prompt(
            incident_reference=incident_reference,
            flight_id=flight_id,
            flight_number=flight_number,
            route=route,
            delay_minutes=delay_minutes,
            trigger_type=trigger_type,
            severity=severity,
            airport_icao=airport_icao,
            passengers_affected=passengers_affected,
            connections_at_risk=connections_at_risk,
            crew_pairings_affected=crew_pairings_affected,
            precedents=precedents or [],
        )

        response, audit = await self._client.call(
            prompt=prompt,
            system=system,
            response_schema=PlannerResponse,
            agent_name="planner",
            prompt_version=PROMPT_VERSION,
            scenario_key=scenario_key,
            budget_seconds=budget_seconds,
            # Ask the provider to constrain its output to the planner shape, rather than only to
            # valid JSON. `response_schema` above is unchanged and still validates whatever comes
            # back: this narrows what a cooperating provider can emit, it does not decide what is
            # accepted. A live 200/`stop` response of `{"final": {...}}` was the defect that made
            # the distinction matter.
            wire_schema=PLANNER_WIRE_SCHEMA,
        )
        log.info(
            "planner_agent_succeeded",
            incident_reference=incident_reference,
            tasks=len(response.tasks),
            generator=audit.generator,
        )
        return response, audit
