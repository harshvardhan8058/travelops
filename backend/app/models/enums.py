"""Canonical enumerations shared by ORM models, API schemas and events.

These values are the single vocabulary for the whole system. The UI may title-case them
for display, but no layer defines an alternative set.

Owner: Stream C (definition). All streams import from here.
"""

from __future__ import annotations

from enum import StrEnum


class IncidentState(StrEnum):
    """Canonical incident lifecycle (docs/26-implementation-contracts.md).

    detected -> assessing -> planning -> assuring
                                     -> awaiting_approval
                                     -> executing -> resolved
                                     -> blocked
    any active state -> failed
    """

    detected = "detected"
    assessing = "assessing"
    planning = "planning"
    assuring = "assuring"
    awaiting_approval = "awaiting_approval"
    executing = "executing"
    resolved = "resolved"
    blocked = "blocked"
    failed = "failed"

    @classmethod
    def terminal(cls) -> frozenset[IncidentState]:
        return frozenset({cls.resolved, cls.blocked, cls.failed})

    @classmethod
    def active(cls) -> frozenset[IncidentState]:
        return frozenset(set(cls) - cls.terminal())


class TaskState(StrEnum):
    pending = "pending"
    proposed = "proposed"
    assured = "assured"
    needs_human = "needs_human"
    rejected = "rejected"
    executing = "executing"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"


class ActionStatus(StrEnum):
    success = "success"
    failure = "failure"
    skipped = "skipped"
    needs_human = "needs_human"


class AssuranceDecision(StrEnum):
    execute = "execute"
    execute_flagged = "execute_flagged"
    needs_human = "needs_human"


class CheckState(StrEnum):
    """Three states, deliberately. A WARN must never collapse into a boolean."""

    passed = "PASS"
    warn = "WARN"
    failed = "FAIL"


class RiskTier(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class RiskLevel(StrEnum):
    """Band for the deterministic risk index. Not a calibrated probability."""

    low = "low"
    elevated = "elevated"
    high = "high"
    severe = "severe"


class TriggerType(StrEnum):
    """Operational cause. This is context, never a legal verdict."""

    weather = "weather"
    atc = "atc"
    technical = "technical"
    crew_rostering = "crew_rostering"
    security = "security"
    other = "other"


class ActionType(StrEnum):
    """The closed set of executable actions.

    A reasoning agent may only propose values from this enum. Anything else is rejected
    before assurance runs.
    """

    notify_passengers = "notify_passengers"
    check_connections = "check_connections"
    find_hotel_options = "find_hotel_options"
    reserve_hotel_block = "reserve_hotel_block"
    arrange_ground_transport = "arrange_ground_transport"
    rebook_passengers = "rebook_passengers"
    reassign_gate = "reassign_gate"
    assess_crew_impact = "assess_crew_impact"
    evaluate_entitlements = "evaluate_entitlements"
    prepare_notifications = "prepare_notifications"
    record_outcome = "record_outcome"


class DeliveryMode(StrEnum):
    real = "real"
    simulated = "simulated"


class NotificationChannel(StrEnum):
    email = "email"
    sms = "sms"
    push = "push"


class PairingLegRole(StrEnum):
    """Why a crew member is on a flight. Drives the cascade explanation."""

    operating = "operating"
    positioning = "positioning"


class PairingMechanism(StrEnum):
    """Why a pairing is at risk. Rendered as the edge label in the cascade graph.

    Exactly one is attributed per affected pairing, by the deterministic precedence in
    `app.services.crew_impact`. This is a coordination label, never a legality verdict.
    """

    operating = "operating"
    onward_duty = "onward_duty"
    second_pairing = "second_pairing"
    positioning = "positioning"
    #: Phase 2. Reached at depth 2: a pairing on a flight that became at risk only because an
    #: earlier pairing's onward leg failed. Counted separately from the direct set so the
    #: headline "9 rotations" cannot move when expansion is enabled.
    downstream_flight = "downstream_flight"


#: The mechanisms reachable at depth 1 — a pairing touching an affected flight directly.
#: The headline "9 rotations" is counted over exactly these, so enabling second-order
#: expansion can add rows but can never move that number.
DIRECT_PAIRING_MECHANISMS = frozenset(
    {
        PairingMechanism.operating,
        PairingMechanism.onward_duty,
        PairingMechanism.second_pairing,
        PairingMechanism.positioning,
    }
)

#: Reachable only at depth >= 2, via bounded expansion.
EXPANSION_PAIRING_MECHANISMS = frozenset({PairingMechanism.downstream_flight})


class PriorityBand(StrEnum):
    """Band for the deterministic passenger priority index. Not a probability, and not a
    judgement about whose journey matters more — it records who is most constrained."""

    routine = "routine"
    elevated = "elevated"
    high = "high"
    critical = "critical"


class PlanSelectionState(StrEnum):
    candidate = "candidate"
    selected = "selected"
    discarded = "discarded"


class ProvenanceKind(StrEnum):
    real = "real"
    simulated = "simulated"
    synthetic = "synthetic"
    fixture = "fixture"
    unavailable = "unavailable"


class PolicyPackStatus(StrEnum):
    draft = "draft"
    official_guidance_dated = "official_guidance_dated"
    approved = "approved"
    retired = "retired"


class ApplicabilityStatus(StrEnum):
    """Tri-state. A missing required fact is `undetermined`, never `not_applicable`."""

    applicable = "applicable"
    not_applicable = "not_applicable"
    undetermined = "undetermined"


class HumanDecisionType(StrEnum):
    approved = "approved"
    rejected = "rejected"
