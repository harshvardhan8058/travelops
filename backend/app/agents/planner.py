"""The Planner agent, and the agent lifecycle around it.

One function the orchestrator calls: `propose`. It runs the model, validates the response against
the
closed contract, reflects on it deterministically, and returns tasks plus the audit record — or
`None`, meaning "use the playbook".

**The playbook is not a fallback in the apologetic sense.** With `LLM_MODE=off` it is the whole
planner and the demo runs on it. This agent is an improvement on it when a model is available, never
a replacement for it.

Lifecycle, in order, with the reason each step exists:

1. **Gather typed facts.** The orchestrator supplies them. The agent holds no session and reads no
   table, so there is nothing for a prompt injection to reach.
2. **Call the model** through the one client, bounded and structured.
3. **Validate against `PlannerResponse`.** An unknown action fails the enum here, before assurance
   ever sees it. One retry, then the playbook — malformed output is rejected, never repaired.
4. **Reflect.** Drop what cannot be executed and record why (`agents/reflection.py`).
5. **Return tasks + audit.** The orchestrator persists, journals and gates. The agent decides
nothing.

Owner: Stream A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import ValidationError

from app.agents.contract import ModelCallAudit, PlannerResponse, PlanTask
from app.agents.reflection import Reflection, reflect
from app.llm import client
from app.models.enums import ActionStatus, ActionType

log = structlog.get_logger(__name__)

GENERATOR = "planner-agent"
PROMPT = "planner.v1"

#: How many times a malformed response is re-requested before the playbook is used. One retry: a
#: model that cannot produce valid JSON twice will not produce it on the third attempt either, and
#: an incident is waiting.
MAX_ATTEMPTS = 2


@dataclass
class PlanningFacts:
    """Everything the planner is allowed to know, all of it typed and supplied by the caller."""

    incident_reference: str
    trigger_type: str
    severity: str
    target_refs: list[str]
    available_actions: set[ActionType]
    evidence_refs: list[str] = field(default_factory=list)
    flight_summary: str | None = None
    risk_summary: str | None = None
    precedent_refs: list[str] = field(default_factory=list)

    def as_prompt_fields(self) -> dict[str, Any]:
        return {
            "incident_reference": self.incident_reference,
            "trigger_type": self.trigger_type,
            "severity": self.severity,
            "flight_summary": self.flight_summary,
            "risk_summary": self.risk_summary,
            "evidence_refs": self.evidence_refs,
            "target_refs": self.target_refs,
            "allowed_actions": sorted(action.value for action in self.available_actions),
            "precedent_refs": self.precedent_refs,
        }


@dataclass
class PlannerProposal:
    """What the orchestrator receives. It persists this; it does not interpret it."""

    tasks: list[PlanTask]
    audit: ModelCallAudit
    reflection: Reflection
    rationale: str
    source: str
    #: Set when the agent asked for a person rather than proposing work.
    needs_human_reason: str | None = None

    def as_detail(self) -> dict[str, Any]:
        """For `decision_log`, so the agent's contribution is auditable like everything else."""
        return {
            "generator": self.audit.generator,
            "prompt_version": self.audit.prompt_version,
            "model_self_report": self.audit.model_self_report,
            "source": self.source,
            "latency_ms": self.audit.latency_ms,
            "input_tokens": self.audit.input_tokens,
            "output_tokens": self.audit.output_tokens,
            "needs_human_reason": self.needs_human_reason,
            **self.reflection.as_detail(),
        }


async def propose(facts: PlanningFacts) -> PlannerProposal | None:
    """Ask the planner for an ordered plan. `None` means the caller uses the playbook.

    Every `None` path is logged with its reason, because "the model was not used" and "the model was
    used and produced nothing" look identical on a screen and are very different facts.
    """
    if not facts.available_actions:
        # Nothing is registered, so there is nothing to order. The playbook's own handling of this
        # case is better than an empty plan.
        return None

    fields = facts.as_prompt_fields()
    last_error: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = await client.complete_json(prompt_name=PROMPT, fields=fields)
        if result is None:
            # off, missing key, timeout, transport failure — all the same to the caller.
            return None

        try:
            response = PlannerResponse.model_validate(result.payload)
        except ValidationError as exc:
            # The closed ActionType enum fails here, which is the point: an invented action never
            # reaches the gate.
            last_error = _first_error(exc)
            log.warning(
                "planner_response_rejected",
                outcome="error",
                incident_reference=facts.incident_reference,
                attempt=attempt,
                detail=last_error,
            )
            continue

        if response.status is ActionStatus.needs_human:
            # A request for review. It authorises nothing and blocks nothing; the orchestrator
            # continues with the playbook and the request is recorded.
            log.info(
                "planner_requested_human",
                incident_reference=facts.incident_reference,
                reason=response.reason[:200],
            )
            return None

        reflection = reflect(
            list(response.tasks),
            available_actions=facts.available_actions,
            allowed_target_refs=facts.target_refs,
        )
        if reflection.rejected:
            log.info(
                "planner_proposal_rejected_by_reflection",
                incident_reference=facts.incident_reference,
                dropped=reflection.dropped_actions,
                reason=reflection.rejection_reason,
            )
            return None

        log.info(
            "planner_proposed",
            incident_reference=facts.incident_reference,
            source=result.source,
            kept=[task.action.value for task in reflection.tasks],
            dropped=reflection.dropped_actions,
        )
        return PlannerProposal(
            tasks=reflection.tasks,
            audit=result.audit,
            reflection=reflection,
            rationale=_rationale(response, reflection),
            source=result.source,
        )

    log.error(
        "planner_unusable",
        outcome="error",
        incident_reference=facts.incident_reference,
        attempts=MAX_ATTEMPTS,
        detail=last_error,
    )
    return None


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "validation failed"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}"[:300]


def _rationale(response: PlannerResponse, reflection: Reflection) -> str:
    """The model's reason, plus what was dropped. Both, in the record a reviewer reads."""
    rationale = f"Planner agent ({PROMPT}): {response.reason.strip()}"
    if reflection.findings:
        dropped = ", ".join(f"{finding.action} ({finding.code})" for finding in reflection.findings)
        rationale = (
            f"{rationale} Dropped before assurance because they cannot be executed: {dropped}."
        )
    return rationale
