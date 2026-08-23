"""Reflection: check a proposed plan before it is persisted.

This is **not** a second assurance gate and it authorises nothing. The Decision Assurance Gate still
evaluates every task afterwards, and a person still approves anything high risk. Reflection can only
ever *narrow* a proposal or reject it outright.

It exists because a model proposal and an executable plan are different things, and the difference
is
knowable deterministically:

| Finding | Why it is dropped rather than passed on |
| --- | --- |
| Action has no registered service | Proposing work nothing can carry out stops the run at the… |
| Duplicate action | Two identical actions on one target is a double booking or a double… |
| Unknown dependency | A dangling edge is permanently unsatisfiable, so the task can never… |
| Self-dependency or a cycle | Same, and it deadlocks the run loop |
| Target refs the orchestrator did not supply | A model inventing an entity reference is a… |
| Nothing left after all of the above | An empty plan lets an incident resolve without doing… |

**The tool-invocation boundary lives here.** Agents never call a service. They propose an action
from
a closed enum, and the only thing that turns an action into a call is the orchestrator's dispatch.
`app/agents/` imports no service, no session and no provider — a guard test asserts it.

Owner: Stream A.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.contract import PlanTask
from app.models.enums import ActionType


@dataclass
class Finding:
    """One reason a proposed task was dropped. Recorded, never silently applied."""

    action: str
    code: str
    detail: str


@dataclass
class Reflection:
    """The outcome of reflecting on a proposal."""

    tasks: list[PlanTask] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: True when nothing survived, so the caller must fall back to the playbook.
    rejected: bool = False
    rejection_reason: str | None = None

    @property
    def dropped_actions(self) -> list[str]:
        return sorted({finding.action for finding in self.findings})

    def as_detail(self) -> dict[str, object]:
        """Shape for `decision_log`, so the reasoning is in the record a reviewer reads."""
        return {
            "kept_actions": [task.action.value for task in self.tasks],
            "dropped_actions": self.dropped_actions,
            "findings": [
                {"action": f.action, "code": f.code, "detail": f.detail} for f in self.findings
            ],
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
        }


CODE_NO_SERVICE = "NO_REGISTERED_SERVICE"
CODE_DUPLICATE = "DUPLICATE_ACTION"
CODE_UNKNOWN_DEPENDENCY = "UNKNOWN_DEPENDENCY"
CODE_SELF_DEPENDENCY = "SELF_DEPENDENCY"
CODE_INVENTED_TARGET = "TARGET_REF_NOT_SUPPLIED"
CODE_EMPTY = "NOTHING_EXECUTABLE_PROPOSED"


def reflect(
    tasks: list[PlanTask],
    *,
    available_actions: set[ActionType],
    allowed_target_refs: list[str],
) -> Reflection:
    """Narrow a proposal to what can actually be executed, recording every drop.

    `available_actions` comes from the dispatch registry, so the boundary widens by itself as
    services land. There is no second list of capabilities to keep in step.
    """
    result = Reflection()
    permitted_refs = set(allowed_target_refs)
    seen: set[ActionType] = set()

    for task in tasks:
        if task.action not in available_actions:
            result.findings.append(
                Finding(
                    action=task.action.value,
                    code=CODE_NO_SERVICE,
                    detail=(
                        "no deterministic service is registered for this action, so it cannot be "
                        "carried out; deferred rather than proposed and failed"
                    ),
                )
            )
            continue

        if task.action in seen:
            result.findings.append(
                Finding(
                    action=task.action.value,
                    code=CODE_DUPLICATE,
                    detail="already proposed once in this plan",
                )
            )
            continue

        invented = [ref for ref in task.target_refs if ref not in permitted_refs]
        if invented:
            result.findings.append(
                Finding(
                    action=task.action.value,
                    code=CODE_INVENTED_TARGET,
                    detail=(
                        f"references the orchestrator did not supply: {', '.join(sorted(invented))}"
                    ),
                )
            )
            continue

        seen.add(task.action)
        # Targets are replaced with the orchestrator's own list rather than trusted from the
        # proposal: the model orders work, it does not decide which entities exist.
        result.tasks.append(
            PlanTask(
                action=task.action,
                target_refs=list(allowed_target_refs),
                inputs=dict(task.inputs),
                depends_on=list(task.depends_on),
            )
        )

    kept = {task.action.value for task in result.tasks}
    for task in list(result.tasks):
        resolved: list[str] = []
        for dependency in task.depends_on:
            if dependency == task.action.value:
                result.findings.append(
                    Finding(
                        action=task.action.value,
                        code=CODE_SELF_DEPENDENCY,
                        detail="a task cannot depend on itself",
                    )
                )
                continue
            if dependency not in kept:
                result.findings.append(
                    Finding(
                        action=task.action.value,
                        code=CODE_UNKNOWN_DEPENDENCY,
                        detail=(
                            f"depends on '{dependency}', which is not in the surviving plan; the "
                            "edge is dropped rather than left unsatisfiable"
                        ),
                    )
                )
                continue
            resolved.append(dependency)
        task.depends_on = resolved

    if not result.tasks:
        result.rejected = True
        result.rejection_reason = (
            "no proposed task could be executed, so the deterministic playbook is used instead"
        )
        result.findings.append(Finding(action="-", code=CODE_EMPTY, detail=result.rejection_reason))
    return result
