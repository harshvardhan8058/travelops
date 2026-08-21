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


class ExplanationResponse(AgentEnvelope):
    payload_type: Literal["explanation.v1"] = "explanation.v1"
    explanation: str = Field(min_length=1)
    citation_refs: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str
    body: str


class ReportResponse(AgentEnvelope):
    payload_type: Literal["report.v1"] = "report.v1"
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
