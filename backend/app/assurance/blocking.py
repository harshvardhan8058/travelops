"""Why a decision blocked, and whether a human may approve past it — STREAM B.

An operator looking at `needs_human` has one question: can I approve this, or do I have to fix
something? The answer is not a matter of judgement, so it is derived here rather than left to
the UI or to whoever is on shift.

Three kinds, from the reason code of every blocking check:

    risk      the action is dangerous but the evidence is sound
    evidence  something is missing, stale, unresolved or unreadable
    conflict  something is contradicted, duplicated, exhausted or breached

**Only `risk` is approvable.** Evidence is not: approval grants permission for known risk, and
it cannot manufacture a fact nobody has. Neither is conflict: a double-booked room or a breached
constraint is resolved, not waved through. Both cases require the inputs to change, which
produces a NEW evaluation — never an approval bolted onto the old one.

Derived, never stored, and no new field on the frozen `CheckResult`. Callers that need it in an
API payload call these helpers.
"""

from __future__ import annotations

from typing import Final, Literal, Protocol

from app.assurance.contract import ReasonCode
from app.models.enums import CheckState

BlockingKind = Literal["risk", "evidence", "conflict"]

KIND_RISK: Final[BlockingKind] = "risk"
KIND_EVIDENCE: Final[BlockingKind] = "evidence"
KIND_CONFLICT: Final[BlockingKind] = "conflict"

#: Reported in this order, so a record lists the most actionable cause first: fix the evidence,
#: resolve the conflict, then decide on the risk.
KIND_ORDER: Final[tuple[BlockingKind, ...]] = (KIND_EVIDENCE, KIND_CONFLICT, KIND_RISK)

_KIND_BY_REASON: Final[dict[ReasonCode, BlockingKind]] = {
    ReasonCode.HUMAN_APPROVAL_REQUIRED: KIND_RISK,
    # Something we do not have, cannot read, or cannot trust.
    ReasonCode.MISSING_REQUIRED_FACT: KIND_EVIDENCE,
    ReasonCode.MISSING_EVIDENCE: KIND_EVIDENCE,
    ReasonCode.SOURCE_STALE: KIND_EVIDENCE,
    ReasonCode.SOURCE_MISSING_TIMESTAMP: KIND_EVIDENCE,
    ReasonCode.ENTITY_NOT_FOUND: KIND_EVIDENCE,
    ReasonCode.ENTITY_STATE_MISMATCH: KIND_EVIDENCE,
    ReasonCode.CONFIG_MISSING: KIND_EVIDENCE,
    ReasonCode.POLICY_PACK_UNAVAILABLE: KIND_EVIDENCE,
    ReasonCode.UNKNOWN_ACTION_TYPE: KIND_EVIDENCE,
    ReasonCode.UNKNOWN_RULE_OPERATOR: KIND_EVIDENCE,
    # Something that contradicts, duplicates, exhausts or breaches.
    ReasonCode.DUPLICATE_ACTION: KIND_CONFLICT,
    ReasonCode.CAPACITY_UNAVAILABLE: KIND_CONFLICT,
    ReasonCode.POLICY_CONSTRAINT_BREACH: KIND_CONFLICT,
}


class _HasChecks(Protocol):
    """Anything carrying a list of checks with a state and a reason code.

    Deliberately structural, so the same helpers serve the action gate and the plan gate without
    either contract importing the other.
    """

    checks: list


def kind_for(reason_code: ReasonCode) -> BlockingKind:
    """Classify one reason code. An unmapped code is `evidence`, never `risk`.

    Unknown means dangerous: a new code must not become silently approvable because nobody
    remembered to classify it.
    """
    return _KIND_BY_REASON.get(reason_code, KIND_EVIDENCE)


def blocking_kinds(result: _HasChecks) -> list[BlockingKind]:
    """The kinds present among the checks that blocked, in KIND_ORDER."""
    found = {
        kind_for(check.reason_code) for check in result.checks if check.state is CheckState.failed
    }
    # A high tier blocks while its own check PASSES, so the risk kind has to be read from the
    # classification rather than from a failure. Without this, an all-passing high-risk action
    # would report no blocking kind at all and read as approvable by accident.
    for check in result.checks:
        if check.reason_code is ReasonCode.HUMAN_APPROVAL_REQUIRED:
            found.add(KIND_RISK)

    return [kind for kind in KIND_ORDER if kind in found]


def is_approvable(result: _HasChecks) -> bool:
    """True only when the sole reason a human is needed is risk.

    This is the rule the whole approval model rests on. An evidence or conflict block is not
    approvable at any level, by any actor: the inputs must change and the decision must be made
    again.
    """
    return blocking_kinds(result) == [KIND_RISK]


def unapprovable_reasons(result: _HasChecks) -> list[str]:
    """Reason codes standing in the way of approval, for an operator to act on."""
    return sorted(
        {
            check.reason_code.value
            for check in result.checks
            if check.state is CheckState.failed and kind_for(check.reason_code) is not KIND_RISK
        }
    )
