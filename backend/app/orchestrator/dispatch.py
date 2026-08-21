"""Service dispatch boundary — STREAM A.

The orchestrator does not implement domain work. Once the gate has authorised a task, the
task is handed to the deterministic service that owns it. Those ten services are Stream C's.

Until a service exists, dispatch returns an **explicit refusal**: status `needs_human` with
reason code `SERVICE_NOT_IMPLEMENTED`, naming the action and its owning stream.

It would be easy to return `ActionStatus.success` with an empty payload and get a green
end-to-end run today. That would be a lie in the audit trail — the very record this system
exists to make trustworthy — and it would hide the missing service until the demo. A
refusal is visible, blocks the incident for human review, and disappears on its own the
moment the real service lands.

When Stream C's services arrive, register them in SERVICE_REGISTRY. The engine's public
contract does not change: `dispatch()` returns a `ServiceResult` either way.

Owner: Stream A (boundary) / Stream C (service bodies).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.models.enums import ActionStatus, ActionType
from app.observability.logging import get_logger
from app.services.base import ServiceResult

log = get_logger(__name__)

#: Reason code recorded on a refused action. Stable, so the UI maps it to copy rather than
#: parsing the message.
SERVICE_NOT_IMPLEMENTED = "SERVICE_NOT_IMPLEMENTED"

ServiceCall = Callable[..., Awaitable[ServiceResult]]

#: Which Stream C service owns each action. Used only for the refusal message, so a
#: reviewer reading the audit trail knows exactly what is missing.
ACTION_OWNERS: dict[ActionType, str] = {
    ActionType.check_connections: "connection",
    ActionType.find_hotel_options: "hotel",
    ActionType.reserve_hotel_block: "hotel",
    ActionType.arrange_ground_transport: "transport",
    ActionType.rebook_passengers: "flight_recovery",
    ActionType.reassign_gate: "resource",
    ActionType.assess_crew_impact: "crew_impact",
    ActionType.evaluate_entitlements: "compensation",
    ActionType.notify_passengers: "communication",
    ActionType.prepare_notifications: "communication",
    ActionType.record_outcome: "analytics_learning",
}

#: Populated as Stream C's services land. Empty is the correct state today.
SERVICE_REGISTRY: dict[ActionType, ServiceCall] = {}


def register(action: ActionType, call: ServiceCall) -> None:
    """Bind an action to its deterministic service."""
    SERVICE_REGISTRY[action] = call


def is_implemented(action: ActionType) -> bool:
    return action in SERVICE_REGISTRY


def refusal(action: ActionType, *, evidence_refs: list[str] | None = None) -> ServiceResult:
    """The explicit "no service to run this yet" result."""
    owner = ACTION_OWNERS.get(action, "unassigned")
    return ServiceResult(
        status=ActionStatus.needs_human,
        reason=(
            f"{SERVICE_NOT_IMPLEMENTED}: no deterministic service is registered for "
            f"'{action.value}'. The '{owner}' service is owned by Stream C. Execution is "
            "refused rather than reported as successful."
        ),
        payload={
            "reason_code": SERVICE_NOT_IMPLEMENTED,
            "action_type": action.value,
            "owning_service": owner,
            "owning_stream": "C",
        },
        evidence_refs=evidence_refs or [],
        # Nothing happened, so nothing may be claimed about where data came from.
        provenance_kind="unavailable",
    )


async def dispatch(
    action: ActionType,
    *,
    target_refs: list[str],
    inputs: dict[str, Any],
    evidence_refs: list[str] | None = None,
    **kwargs: Any,
) -> ServiceResult:
    """Run the deterministic service that owns this action.

    Callers must have obtained authorisation from the Decision Assurance Gate first. This
    function does not re-check that; the engine does, in one place, before calling here.
    """
    call = SERVICE_REGISTRY.get(action)
    if call is None:
        log.warning(
            "service_dispatch_refused",
            outcome="needs_human",
            action_type=action.value,
            reason_code=SERVICE_NOT_IMPLEMENTED,
            owning_service=ACTION_OWNERS.get(action, "unassigned"),
        )
        return refusal(action, evidence_refs=evidence_refs)

    result = await call(
        target_refs=target_refs,
        inputs=inputs,
        evidence_refs=evidence_refs or [],
        **kwargs,
    )
    if not isinstance(result, ServiceResult):
        # A service that does not honour the contract is a failure, not a success.
        raise TypeError(
            f"service for '{action.value}' returned {type(result).__name__}, not ServiceResult"
        )
    return result
