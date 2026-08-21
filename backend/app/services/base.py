"""Deterministic service contract — STREAM D.

Ten services. Each is typed, unit-tested, and returns a result plus evidence references.

HARD RULE: nothing under app/services/ may import an LLM client. A test asserts this
(tests/unit/test_no_llm_in_services.py), because the boundary is the architecture.

A service does not decide whether it is allowed to run. The orchestrator asks the Decision
Assurance Gate first, then dispatches here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActionStatus


class ServiceResult(BaseModel):
    """What every deterministic service returns."""

    model_config = ConfigDict(extra="forbid")

    status: ActionStatus
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    cost_inr: int | None = None
    # real | simulated | synthetic | fixture | unavailable
    provenance_kind: str = "simulated"


@runtime_checkable
class DeterministicService(Protocol):
    name: str

    async def execute(self, **kwargs: Any) -> ServiceResult: ...
