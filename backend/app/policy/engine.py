"""Jurisdiction-neutral rules engine — STREAM B.

The engine knows operators (comparisons, date windows, capped formulas, evidence presence).
It does NOT know the word "DGCA". Jurisdiction-specific content lives entirely in packs.

Specification: policy_packs/in-moca-charter-2019/2019.02/rules.yaml (40 rules) and
test_cases.yaml (23 cases). Those cases are the definition of done.

Non-negotiable behaviours:
  * A rule marked excluded_from_evaluation NEVER evaluates. Surface a supersession notice.
  * A missing required fact yields needs_human, never a guessed amount.
  * An exemption requires its evidence facts. A weather trigger alone never exempts.
  * Every result carries pack id, version, hash, rule id and source clause refs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ApplicabilityStatus


class EntitlementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 'evaluated' | 'needs_human' | 'suppressed'
    outcome: str
    entitlements: list[dict[str, Any]] = Field(default_factory=list)
    cash_inr: int | None = None
    cash_reason_codes: list[str] = Field(default_factory=list)
    rules_fired: list[str] = Field(default_factory=list)
    excluded_rules: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)

    pack_id: str | None = None
    pack_version: str | None = None
    pack_hash: str | None = None
    pack_status: str | None = None
    source_clause_refs: list[str] = Field(default_factory=list)
    # Human-readable derivation, e.g. "least_of(cap 7500, 4200 + 800) = 5000"
    formula_used: str | None = None


class ApplicabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ApplicabilityStatus
    pack_id: str
    pack_version: str
    basis: dict[str, Any] = Field(default_factory=dict)
    required_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def evaluate(*, facts: dict[str, Any], pack: Any) -> EntitlementResult:
    """Evaluate a reviewed pack's rules against trip facts."""
    raise NotImplementedError("Stream B: implement the rule DSL evaluator")
