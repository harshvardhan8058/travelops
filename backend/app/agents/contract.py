"""Reasoning-agent response contracts.

All three reasoning agents share an ENVELOPE, but each has a purpose-specific payload.
They deliberately do NOT share one action-shaped schema: a planner returns an ordered task
list, while an explainer and a reporter return prose artifacts. Overloading a single
`action` field to carry all three would hide incompatible conventions.

Only `PlannerResponse.tasks[]` contains executable action enums and enters the Decision
Assurance Gate. Explanation and report payloads are read-only artifacts.

`confidence` is absent by design. If a model emits one, store it as
`ModelCallAudit.model_self_report` and never branch on it.

Owner: Stream A (contract) / Stream B consumes tasks / Streams E-F render payloads.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ActionStatus, ActionType


class AgentEnvelope(BaseModel):
    """Fields common to every reasoning response."""

    model_config = ConfigDict(extra="forbid")

    status: ActionStatus
    reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    payload_type: str


class PlanTask(BaseModel):
    """One proposed unit of work.

    `action` is validated against the closed ActionType enum. A model cannot invent an
    action; an unknown value is rejected before assurance runs.
    """

    model_config = ConfigDict(extra="forbid")

    action: ActionType
    target_refs: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("target_refs")
    @classmethod
    def _refs_non_empty(cls, value: list[str]) -> list[str]:
        if any(not ref.strip() for ref in value):
            raise ValueError("target_refs entries must be non-empty")
        return value


class PlannerResponse(AgentEnvelope):
    payload_type: Literal["planner.v1"] = "planner.v1"
    tasks: list[PlanTask] = Field(min_length=1)


# Prose artifacts tolerate keys the model volunteers; the planner does not.
#
# `extra="forbid"` is right for `PlannerResponse`: its tasks reach the assurance gate and then
# execution, so a field nobody declared is a field nobody validated, and rejecting the whole
# proposal is the safe answer. Applied to a read-only artifact it is the wrong trade. An
# explanation authorises nothing — `authorises_no_action` is `True` on both endpoints — so a
# model that helpfully adds `confidence` should have that key dropped, not have the entire
# explanation replaced by an error.
#
# This is what the module docstring above already asks for: "If a model emits one, store it as
# `ModelCallAudit.model_self_report` and never branch on it." Storing requires the response to
# survive validation. `LLMClient` reads `raw.get("model_self_report")` off the raw payload for
# exactly that purpose, which only works if an extra key is not fatal first.
#
# `reason` is widened here for the same reason. The envelope's 2000-character cap stops a
# planner turning a justification field into an essay; for these two the prose *is* the
# deliverable, and a verbose `reason` is not a contract breach worth a 503.
_PROSE_ARTIFACT = ConfigDict(extra="ignore")
_PROSE_REASON = Field(min_length=1, max_length=20000)


class ExplanationResponse(AgentEnvelope):
    model_config = _PROSE_ARTIFACT

    payload_type: Literal["explanation.v1"] = "explanation.v1"
    reason: str = _PROSE_REASON
    explanation: str = Field(min_length=1)
    citation_refs: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    model_config = _PROSE_ARTIFACT

    heading: str
    body: str


class ReportResponse(AgentEnvelope):
    model_config = _PROSE_ARTIFACT

    payload_type: Literal["report.v1"] = "report.v1"
    reason: str = _PROSE_REASON
    summary: str = Field(min_length=1)
    sections: list[ReportSection] = Field(default_factory=list)
    # Metrics must reference recorded values; the reporter never invents a number.
    metric_refs: list[str] = Field(default_factory=list)


ReasoningResponse = Annotated[
    PlannerResponse | ExplanationResponse | ReportResponse,
    Field(discriminator="payload_type"),
]


class ModelCallAudit(BaseModel):
    """Diagnostic metadata for one model call. Never used for control flow."""

    model_config = ConfigDict(extra="forbid")

    generator: str
    prompt_version: str | None = None
    # A model's self-assessment, recorded only so we can show it does not determine
    # execution. Not calibration data: gate outcomes are policy decisions, not ground truth.
    model_self_report: int | None = Field(default=None, ge=0, le=100)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
