"""Actor classification — one mapping, shared.

`actor` is a free string on `decision_log`; the console groups by *kind*. That mapping lived in
`incidents.py` and is now here because replay needs it too.

**Exactly one mapping must exist in the codebase.** Two copies would let the timeline and replay
disagree about whether a human authorised something, which is the one thing Phase 1 closed. A
test asserts both endpoints report the same kind for the same row.
"""

from __future__ import annotations

#: actor -> actor_kind. `assurance_gate` is part of the deterministic control plane, which is
#: why it is not its own kind: it is the orchestrator applying rules, not a separate agent.
ACTOR_KINDS: dict[str, str] = {
    "orchestrator": "orchestrator",
    "assurance_gate": "orchestrator",
    "human": "human",
    "provider": "provider",
}


def actor_kind(actor: str) -> str:
    """Classify an actor for the UI, which groups by kind rather than by name."""
    if actor in ACTOR_KINDS:
        return ACTOR_KINDS[actor]
    if actor.endswith("_service"):
        return "service"
    if actor.endswith("_agent"):
        return "agent"
    return "orchestrator"
