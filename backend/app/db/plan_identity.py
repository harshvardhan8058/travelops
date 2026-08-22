"""Deterministic identity for a plan's task set — STREAM C.

`plan.plan_hash` is what makes a plan-level approval honest. An operator signs a specific set
of tasks; if the plan is regenerated, reordered or has a task appended, the hash changes and
the signature stops covering it. Without that, "approve the plan" silently grows to authorise
work nobody reviewed, which is exactly the failure P2-D3 exists to prevent.

Two properties the hash must have, and both are tested:

* **Order independent over task identity, order dependent over sequencing.** Two plans with
  the same tasks in a different execution order are genuinely different plans — the order is
  what the operator read — so they hash differently. But re-serialising the same plan with keys
  in a different order must not change anything, hence canonical JSON with sorted keys.
* **Blind to timestamps and row ids.** Seeding the same dataset twice produces different
  primary keys. If those fed the hash, an identical plan would fail to match its own approval
  after a reset, and the demo would look broken for a reason nobody could see.

Owner: Stream C. Stream A calls `compute_plan_hash` when it persists a plan; Stream B compares
the stored hash when it validates an approval.

**Ownership note on `approval_covers`.** Stream B's PR #48 lands
`app/assurance/approval.py:plan_approval_covers`, which implements the same four P2-D3 conditions
against their richer policy objects. **That function is authoritative for the gate.** The one here
is a data-layer convenience for callers holding raw column values rather than assurance types —
`plan_approval` rows, a migration backfill, a test. Two implementations of the same rule is a real
risk, so it is written down rather than left for someone to discover: if they ever disagree, B is
right and this one is the bug. `compute_plan_hash` has no such overlap and is owned solely here,
because plan identity is a property of the stored task set.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

#: Bumped only when the hashed shape changes. A different version means old hashes are not
#: comparable, which is the honest outcome — not something to paper over.
PLAN_IDENTITY_VERSION = "plan-identity-v1"

#: 32 hex characters. Enough that a collision is not a practical concern, short enough to
#: read out loud during a demo and compare against a screen.
HASH_LENGTH = 32

#: The only task fields that participate. Everything else — ids, timestamps, free-text
#: rationale — is excluded deliberately: an approval must survive a reseed, and must not be
#: invalidated by an LLM rewording a sentence.
TASK_FIELDS = ("action_type", "target_ref", "risk_tier", "sequence")


def canonical_task(task: Mapping[str, Any], *, sequence: int) -> dict[str, Any]:
    """Reduce a task to the fields that define what is being authorised.

    `sequence` is passed in rather than read from the task so the caller's ordering is what
    counts. A plan is a sequence; two plans with identical tasks executed in a different order
    authorise different things.
    """
    return {
        "action_type": str(task.get("action_type") or ""),
        "target_ref": str(task.get("target_ref") or ""),
        "risk_tier": str(task.get("risk_tier") or ""),
        "sequence": int(sequence),
    }


def compute_plan_hash(
    tasks: Sequence[Mapping[str, Any]],
    *,
    generator: str,
    prompt_version: str,
) -> str:
    """Hash the task set together with what produced it.

    `generator` and `prompt_version` are included because a plan produced by a different
    generator is a different plan even when the tasks coincide. An approval carried across a
    generator change would be a signature on work the operator never saw the provenance of.

    Returns 32 lowercase hex characters.
    """
    document = {
        "version": PLAN_IDENTITY_VERSION,
        "generator": str(generator),
        "prompt_version": str(prompt_version),
        "tasks": [
            canonical_task(task, sequence=index) for index, task in enumerate(tasks, start=1)
        ],
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:HASH_LENGTH]


def approval_covers(
    *,
    approved_plan_hash: str,
    current_plan_hash: str | None,
    covered_task_ids: Sequence[int],
    task_id: int,
    risk_tier: str,
    has_failed_check: bool,
) -> tuple[bool, str]:
    """Whether a plan approval authorises one specific task. Returns `(covered, reason)`.

    All four conditions must hold, and the reason names the first that does not, so a refusal
    is explainable on screen rather than a silent `False`:

    1. The plan still hashes to what was approved. A re-plan invalidates the signature.
    2. The task was in the approved set. A task appended afterwards is not covered.
    3. The tier is low or medium. P2-D3: high risk always needs its own approval.
    4. No check FAILED. Approval can cover *risk*; it can never cover failed evidence.

    The database enforces (3) on the tiers table as well. Both layers, because this is the
    rule most costly to get wrong: the whole authorisation story rests on it.
    """
    if not current_plan_hash or current_plan_hash != approved_plan_hash:
        return False, (
            "The plan changed after it was approved "
            f"(approved {approved_plan_hash}, current {current_plan_hash or 'unset'}). "
            "The approval no longer covers it and a fresh one is required."
        )
    # Compared as strings: Stream B carries `task_id` as a string and this table's JSON column
    # has to match whatever the gate compares against, or a coverage check would pass here and
    # fail there.
    if str(task_id) not in {str(item) for item in covered_task_ids}:
        return False, (
            f"Task {task_id} was not part of the approved plan, so the approval does not "
            "extend to it."
        )
    if str(risk_tier).lower() not in {"low", "medium"}:
        return False, (
            f"Task {task_id} is {risk_tier} risk. A plan approval never covers high risk — "
            "this action needs its own authorisation."
        )
    if has_failed_check:
        return False, (
            f"Task {task_id} has a failed assurance check. An approval can accept risk, "
            "never failed evidence."
        )
    return True, f"Covered by the plan approval for {approved_plan_hash}."
