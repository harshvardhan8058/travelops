"""Workflow safety limits.

Its own module so caps are impossible to overlook. Exceeding a limit BLOCKS the incident;
it never loops and never silently continues.

Owner: Stream A.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.errors import WorkflowLimitExceeded


@dataclass(frozen=True)
class Limits:
    max_workflow_steps: int
    action_timeout_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Limits:
        cfg = settings or get_settings()
        return cls(
            max_workflow_steps=cfg.max_workflow_steps,
            action_timeout_seconds=cfg.action_timeout_seconds,
        )


def check_step_budget(steps_taken: int, limits: Limits, *, incident_ref: str) -> None:
    if steps_taken < limits.max_workflow_steps:
        return
    raise WorkflowLimitExceeded(
        f"incident exceeded {limits.max_workflow_steps} workflow steps",
        details={
            "incident_reference": incident_ref,
            "steps_taken": steps_taken,
            "max_workflow_steps": limits.max_workflow_steps,
            "resolution": "incident moves to 'blocked' for human review",
        },
    )
