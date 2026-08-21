"""The deterministic fallback playbook — STREAM A.

`LLM_MODE=off` must still complete a recovery. That is a stated design rule, not a
degraded mode: the system has to survive its own AI failing, and the fallback is a demo
asset in its own right. So the playbook is the FIRST source of a plan, and the Planner
agent is an optional improvement on top of it.

The plan is **data**, not branching code. Adding a trigger type means adding a tuple here,
not editing the engine. Every action is an `ActionType` member, so the closed action enum
is enforced by construction rather than by a later validation pass.

Task order and dependencies match the committed fixture at `fixtures/api/incident_detail.json`,
because the frontend renders that plan and Stream D is building against it now.

Owner: Stream A.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import ActionType, TriggerType

#: Recorded on `plan.generator`. The API must always state which generator produced a
#: plan, so a judge never has to guess whether a model was involved.
FALLBACK_GENERATOR = "fallback-playbook"

FALLBACK_RATIONALE = (
    "Deterministic playbook: protect time-sensitive connections first, then assess "
    "resource and crew impact, then hold anything with an external effect for review."
)


@dataclass(frozen=True)
class PlaybookStep:
    """One step of a playbook.

    `depends_on` names other actions in the same playbook. The engine resolves those
    names to persisted task IDs, so a plan never carries a dangling reference.
    """

    action: ActionType
    depends_on: tuple[ActionType, ...] = ()
    inputs: dict[str, object] = field(default_factory=dict)


# Ordering rationale, in the words of docs/02-disruption-flow.md: protect
# time-sensitive connections before allocating remaining resources.
#
# The last two steps carry an external effect — a passenger notification and an
# entitlement figure — so they sit behind the assessment steps and are expected to draw
# `needs_human` from the gate. That is the correct outcome, not a limitation.
_WEATHER_PLAYBOOK: tuple[PlaybookStep, ...] = (
    PlaybookStep(action=ActionType.check_connections),
    PlaybookStep(action=ActionType.find_hotel_options),
    PlaybookStep(action=ActionType.assess_crew_impact),
    PlaybookStep(
        action=ActionType.notify_passengers,
        depends_on=(ActionType.check_connections,),
    ),
    PlaybookStep(action=ActionType.evaluate_entitlements),
)

#: Used when a trigger type has no specific playbook. Deliberately the same shape: an
#: unknown cause is not a reason to do less, and it is certainly not a reason to guess.
_DEFAULT_PLAYBOOK: tuple[PlaybookStep, ...] = _WEATHER_PLAYBOOK

FALLBACK_PLAYBOOK: dict[TriggerType, tuple[PlaybookStep, ...]] = {
    TriggerType.weather: _WEATHER_PLAYBOOK,
    TriggerType.atc: _WEATHER_PLAYBOOK,
    TriggerType.technical: _WEATHER_PLAYBOOK,
    TriggerType.crew_rostering: _WEATHER_PLAYBOOK,
    TriggerType.security: _WEATHER_PLAYBOOK,
    TriggerType.other: _DEFAULT_PLAYBOOK,
}


def playbook_for(trigger_type: TriggerType | str) -> tuple[PlaybookStep, ...]:
    """Return the playbook for a trigger, falling back to the default.

    A trigger the system does not recognise still yields a usable plan. It never yields
    an empty one, because an empty plan would silently resolve an incident that nobody
    actually worked.
    """
    try:
        key = TriggerType(trigger_type)
    except ValueError:
        return _DEFAULT_PLAYBOOK
    return FALLBACK_PLAYBOOK.get(key, _DEFAULT_PLAYBOOK)


def _validate() -> None:
    """Reject an incoherent playbook at import time rather than mid-demo."""
    for trigger, steps in FALLBACK_PLAYBOOK.items():
        assert steps, f"playbook for {trigger} is empty"
        actions = [step.action for step in steps]
        assert len(actions) == len(set(actions)), f"playbook for {trigger} repeats an action"
        for step in steps:
            for dependency in step.depends_on:
                assert dependency in actions, (
                    f"playbook for {trigger}: {step.action} depends on {dependency}, "
                    "which is not in the same playbook"
                )
                assert actions.index(dependency) < actions.index(step.action), (
                    f"playbook for {trigger}: {step.action} depends on {dependency}, "
                    "which comes later in the order"
                )


_validate()
