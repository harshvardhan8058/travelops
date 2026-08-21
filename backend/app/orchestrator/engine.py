"""Workflow engine — STREAM A.

Wave 0 fixes the interface so Streams B, D, E and F can code against it. The bodies are
Stream A's first slice.

Required behaviour (docs/26-implementation-contracts.md):
  * task ordering with dependency resolution, dispatching independent tasks in parallel
  * every mutation carries an Idempotency-Key; a replay returns the original result
  * step and timeout caps enforced via app.orchestrator.limits
  * every proposed task goes through the Decision Assurance Gate before execution
  * an action row may not exist without the evaluation that authorised it, and when that
    evaluation said needs_human it also needs an approved human decision
  * every step appends to decision_log
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.contract import PlanTask
from app.assurance.contract import AssuranceResult
from app.models.enums import IncidentState


@dataclass
class WorkflowContext:
    """Everything the engine needs to run one incident forward."""

    incident_id: int
    incident_reference: str
    state: IncidentState
    correlation_id: str
    steps_taken: int = 0
    evidence_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    """Deterministic control plane. Contains no open-ended language reasoning."""

    async def open_incident(self, flight_id: int, trigger_type: str) -> WorkflowContext:
        """Create an incident, or return the existing active one for this flight.

        The partial unique index uq_incident_active_per_flight makes duplicate creation a
        database error rather than a race; catch it and return the existing incident.
        """
        raise NotImplementedError("Stream A: implement incident creation with dedup")

    async def propose_tasks(self, ctx: WorkflowContext) -> list[PlanTask]:
        """Get a plan from the Planner, or the deterministic fallback playbook.

        With LLM_MODE=off the fallback must still produce a usable plan. Reject any task
        whose action type is not in ActionType before returning.
        """
        raise NotImplementedError("Stream A: implement plan acquisition + fallback")

    async def assure(self, ctx: WorkflowContext, task: PlanTask) -> AssuranceResult:
        """Run the Decision Assurance Gate. Delegates to Stream B's gate."""
        raise NotImplementedError("Stream A: call app.assurance.gate.evaluate")

    async def execute(
        self, ctx: WorkflowContext, task: PlanTask, assurance: AssuranceResult
    ) -> Any:
        """Dispatch to the owning deterministic service.

        Must refuse when `assurance.executable` is False. Must require an approved human
        decision when the gate returned needs_human.
        """
        raise NotImplementedError("Stream A: implement guarded dispatch")

    async def advance(self, ctx: WorkflowContext) -> WorkflowContext:
        """Drive the incident one step, honouring the state machine and limits."""
        raise NotImplementedError("Stream A: implement the run loop")
