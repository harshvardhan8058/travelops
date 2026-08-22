"""The plan hash and what a plan approval may cover.

Written as the specification for P2-D3. Every test here corresponds to a way an approval could
quietly authorise something the operator did not agree to.
"""

from __future__ import annotations

from app.db.plan_identity import (
    HASH_LENGTH,
    approval_covers,
    compute_plan_hash,
)

GENERATOR = "playbook-v1"
PROMPT = "none"


def _tasks():
    return [
        {
            "id": 11,
            "action_type": "predict_delay_risk",
            "target_ref": "flight:1",
            "risk_tier": "low",
        },
        {
            "id": 12,
            "action_type": "check_connections",
            "target_ref": "flight:1",
            "risk_tier": "low",
        },
        {
            "id": 13,
            "action_type": "book_hotel",
            "target_ref": "flight:1",
            "risk_tier": "medium",
        },
    ]


def _hash(tasks):
    return compute_plan_hash(tasks, generator=GENERATOR, prompt_version=PROMPT)


# ------------------------------------------------------------------------------- the hash


def test_hash_is_stable_and_short_enough_to_read_aloud():
    assert _hash(_tasks()) == _hash(_tasks())
    assert len(_hash(_tasks())) == HASH_LENGTH
    assert _hash(_tasks()) == _hash(_tasks()).lower()


def test_row_ids_and_timestamps_do_not_participate():
    """A reseed reassigns primary keys. If ids fed the hash, an identical plan would stop
    matching its own approval after a reset and the demo would break for an invisible reason."""
    tasks = _tasks()
    for offset, task in enumerate(tasks):
        task["id"] = 9000 + offset
        task["created_at"] = "2026-08-20T21:10:00Z"
        task["rationale"] = "reworded by the planner"
    assert _hash(tasks) == _hash(_tasks())


def test_reordering_the_same_tasks_changes_the_hash():
    """Order is part of what the operator read. A plan that books a hotel before checking
    connections is a different plan, and an approval must not carry across the two."""
    reordered = list(reversed(_tasks()))
    assert _hash(reordered) != _hash(_tasks())


def test_appending_a_task_changes_the_hash():
    """The core protection. Without it, "approve the plan" grows to cover later additions."""
    grown = [
        *_tasks(),
        {
            "id": 14,
            "action_type": "issue_compensation",
            "target_ref": "flight:1",
            "risk_tier": "high",
        },
    ]
    assert _hash(grown) != _hash(_tasks())


def test_changing_a_risk_tier_changes_the_hash():
    """A task silently re-tiered from high to medium after signing would otherwise slip
    inside the approval's scope."""
    retiered = _tasks()
    retiered[2]["risk_tier"] = "low"
    assert _hash(retiered) != _hash(_tasks())


def test_a_different_generator_is_a_different_plan():
    same_tasks = _tasks()
    assert compute_plan_hash(
        same_tasks, generator="llm-planner-v2", prompt_version=PROMPT
    ) != _hash(same_tasks)


def test_key_order_in_the_input_is_irrelevant():
    """Canonical JSON. Serialisation order is an implementation detail of the caller."""
    shuffled = [
        {
            "risk_tier": t["risk_tier"],
            "target_ref": t["target_ref"],
            "action_type": t["action_type"],
            "id": t["id"],
        }
        for t in _tasks()
    ]
    assert _hash(shuffled) == _hash(_tasks())


# -------------------------------------------------------------------------- what it covers


BASE = {
    "approved_plan_hash": "abc123",
    "current_plan_hash": "abc123",
    "covered_task_ids": [11, 12, 13],
    "task_id": 12,
    "risk_tier": "low",
    "has_failed_check": False,
}


def test_a_low_risk_covered_task_on_an_unchanged_plan_is_authorised():
    covered, reason = approval_covers(**BASE)
    assert covered is True
    assert "abc123" in reason


def test_high_risk_is_never_covered():
    """P2-D3, and the reason must name the tier so the operator sees why."""
    covered, reason = approval_covers(**{**BASE, "risk_tier": "high"})
    assert covered is False
    assert "never covers high risk" in reason


def test_a_failed_check_is_never_covered_at_any_tier():
    """Approval accepts risk. It cannot accept failed evidence — that is the Phase 1
    invariant and a plan-level signature must not become a way around it."""
    for tier in ("low", "medium"):
        covered, reason = approval_covers(**{**BASE, "risk_tier": tier, "has_failed_check": True})
        assert covered is False
        assert "never failed evidence" in reason


def test_a_replanned_plan_loses_its_approval():
    covered, reason = approval_covers(**{**BASE, "current_plan_hash": "def456"})
    assert covered is False
    assert "changed after it was approved" in reason


def test_an_unhashed_plan_is_not_covered():
    """A NULL `plan_hash` means nothing can be proven about the task set. Defaulting to
    covered would authorise on absence of evidence."""
    covered, reason = approval_covers(**{**BASE, "current_plan_hash": None})
    assert covered is False
    assert "unset" in reason


def test_a_task_added_after_signing_is_not_covered():
    covered, reason = approval_covers(**{**BASE, "task_id": 99})
    assert covered is False
    assert "not part of the approved plan" in reason


def test_the_refusal_reason_names_the_first_failing_condition():
    """A refusal that lists everything wrong at once is harder to act on than one that names
    the blocker. Hash first, because a stale plan makes the other checks meaningless."""
    covered, reason = approval_covers(
        **{**BASE, "current_plan_hash": "stale", "risk_tier": "high", "has_failed_check": True}
    )
    assert covered is False
    assert "changed after it was approved" in reason
    assert "high risk" not in reason
