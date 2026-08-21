"""The six assurance checks — STREAM B.

Pure functions. No I/O, no database, no network: everything a check needs arrives in its
arguments so it is trivially unit-testable and reproducible.

Each returns a CheckResult with PASS / WARN / FAIL and a machine-readable reason code.

Stream B's definition of done is the 23 cases in
policy_packs/in-moca-charter-2019/2019.02/test_cases.yaml.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.assurance.contract import AssuranceConfig, CheckResult


def evidence_complete(*, required_facts: list[str], provided_facts: dict[str, Any]) -> CheckResult:
    """FAIL when any fact the selected rule requires is absent.

    A fact present but None counts as absent — that distinction is what stops a null from
    being silently treated as a legal answer.
    """
    raise NotImplementedError("Stream B")


def sources_fresh(
    *,
    sources: dict[str, datetime | None],
    now: datetime,
    config: AssuranceConfig,
    action_type: str,
) -> CheckResult:
    """FAIL when a source exceeds its configured max age.

    May be downgraded to WARN only when config.warn_permitted(action_type, sources_fresh).
    A source with no timestamp is FAIL, never assumed fresh.
    """
    raise NotImplementedError("Stream B")


def entities_valid(*, referenced_refs: list[str], resolved: dict[str, Any]) -> CheckResult:
    """FAIL when a referenced entity does not exist or its state does not match."""
    raise NotImplementedError("Stream B")


def policy_compliant(
    *, action_type: str, payload: dict[str, Any], constraints: list[dict[str, Any]]
) -> CheckResult:
    """FAIL on any breach of a business constraint or selected policy-pack constraint."""
    raise NotImplementedError("Stream B")


def no_conflicts(
    *, action_type: str, target_refs: list[str], pending_or_executed: list[dict[str, Any]]
) -> CheckResult:
    """FAIL on a duplicate action or on consuming unavailable capacity.

    This is what prevents a double-booked room or a twice-rebooked passenger.
    """
    raise NotImplementedError("Stream B")


def action_risk(*, action_type: str, config: AssuranceConfig) -> CheckResult:
    """Classify risk. Sets `tier`; may PASS while its tier still forces human approval.

    An action type absent from config.risk_tiers is HIGH. Unknown means dangerous.
    """
    raise NotImplementedError("Stream B")
