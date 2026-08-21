"""Fail-closed aggregation — STREAM B.

The aggregation ORDER is a contract. Implement it exactly:

    1. Missing config, unknown action type, or unknown rule operator -> FAIL
    2. Any FAIL                -> needs_human. Nothing executes.
    3. risk_tier == high       -> needs_human even when every check passes
    4. A WARN -> execute_flagged ONLY when the versioned config explicitly permits that
       warning for that action. There is no global soft-failure bypass.
    5. Otherwise -> execute. Multiple warnings never become safer by aggregation.

The result is immutable. A corrected decision requires a NEW evaluation, never an update.
"""

from __future__ import annotations

from app.assurance.contract import AssuranceConfig, AssuranceResult, CheckResult


def aggregate(
    *, checks: list[CheckResult], action_type: str, config: AssuranceConfig
) -> AssuranceResult:
    """Combine check results into a single authorisation decision."""
    raise NotImplementedError("Stream B: implement the five ordered rules above")


def load_config(path: str) -> AssuranceConfig:
    """Load and validate versioned gate config.

    A missing or unparseable file must raise AssuranceConfigMissing so the caller blocks
    execution. Returning a permissive default here would defeat the entire design.
    """
    raise NotImplementedError("Stream B: parse YAML, validate, record version + sha256")
