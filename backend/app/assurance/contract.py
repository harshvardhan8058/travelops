"""Decision Assurance Gate contracts.

This is the deterministic authorisation boundary. It replaces LLM self-reported
confidence entirely.

Six checks, each returning PASS / WARN / FAIL plus a machine-readable reason code.
Aggregation is fail-closed and ordered:

    1. Missing config, unknown action type or unknown rule operator -> FAIL
    2. Any FAIL                 -> needs_human, nothing executes
    3. risk_tier == high        -> needs_human even when every check passes
    4. A WARN may yield execute_flagged ONLY where versioned config explicitly permits
       that warning for that low-risk reversible action. There is no global bypass.
    5. Otherwise all checks must pass. Multiple warnings never become safer together.

Owner: Stream B implements the check bodies. This contract is fixed in Wave 0 so Streams
A, E and F can build against it immediately.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssuranceDecision, CheckState, RiskTier


class CheckName(StrEnum):
    evidence_complete = "evidence_complete"
    sources_fresh = "sources_fresh"
    entities_valid = "entities_valid"
    policy_compliant = "policy_compliant"
    no_conflicts = "no_conflicts"
    action_risk = "action_risk"


# Fixed order, so a UI panel and an audit record always agree on presentation.
CHECK_ORDER: tuple[CheckName, ...] = (
    CheckName.evidence_complete,
    CheckName.sources_fresh,
    CheckName.entities_valid,
    CheckName.policy_compliant,
    CheckName.no_conflicts,
    CheckName.action_risk,
)


class ReasonCode(StrEnum):
    """Stable codes. The UI maps these to copy; it never parses free text."""

    OK = "OK"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    SOURCE_STALE = "SOURCE_STALE"
    SOURCE_MISSING_TIMESTAMP = "SOURCE_MISSING_TIMESTAMP"
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    ENTITY_STATE_MISMATCH = "ENTITY_STATE_MISMATCH"
    POLICY_CONSTRAINT_BREACH = "POLICY_CONSTRAINT_BREACH"
    POLICY_PACK_UNAVAILABLE = "POLICY_PACK_UNAVAILABLE"
    DUPLICATE_ACTION = "DUPLICATE_ACTION"
    CAPACITY_UNAVAILABLE = "CAPACITY_UNAVAILABLE"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    UNKNOWN_ACTION_TYPE = "UNKNOWN_ACTION_TYPE"
    UNKNOWN_RULE_OPERATOR = "UNKNOWN_RULE_OPERATOR"
    CONFIG_MISSING = "CONFIG_MISSING"
    MISSING_REQUIRED_FACT = "MISSING_REQUIRED_FACT"


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CheckName
    state: CheckState
    reason_code: ReasonCode = ReasonCode.OK
    reason: str | None = None
    # Risk tier is a classification, so `action_risk` may PASS while its tier still blocks.
    tier: RiskTier | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.state is CheckState.failed


class AssuranceResult(BaseModel):
    """The immutable record persisted as `assurance_evaluation`."""

    model_config = ConfigDict(extra="forbid")

    decision: AssuranceDecision
    risk_tier: RiskTier
    checks: list[CheckResult]
    blocking: list[CheckName] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    config_version: str
    config_hash: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def executable(self) -> bool:
        return self.decision in {
            AssuranceDecision.execute,
            AssuranceDecision.execute_flagged,
        }

    @property
    def requires_human(self) -> bool:
        return self.decision is AssuranceDecision.needs_human


class FreshnessLimits(BaseModel):
    """Max age per source kind, in minutes. From versioned config, never hardcoded."""

    model_config = ConfigDict(extra="forbid")

    metar_minutes: int = 60
    taf_minutes: int = 360
    flight_status_minutes: int = 5
    policy_pack_days: int = 3650


class AssuranceConfig(BaseModel):
    """Versioned gate configuration.

    `warn_allowed_actions` is the ONLY route to `execute_flagged`. Absence means a WARN
    blocks, which is the safe default.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    freshness: FreshnessLimits = Field(default_factory=FreshnessLimits)
    risk_tiers: dict[str, RiskTier] = Field(default_factory=dict)
    warn_allowed_actions: dict[str, list[CheckName]] = Field(default_factory=dict)
    high_risk_requires_human: bool = True

    def tier_for(self, action_type: str) -> RiskTier:
        """Unknown action types are treated as high risk, not low."""
        return self.risk_tiers.get(action_type, RiskTier.high)

    def warn_permitted(self, action_type: str, check: CheckName) -> bool:
        return check in self.warn_allowed_actions.get(action_type, [])
