"""Response contract for `GET /incidents/{ref}/policy` — G4.

Shaped to the committed `fixtures/api/policy.json`, because that fixture is a **contract**: Stream D
renders it verbatim and Stream C owns the file. Where the authoritative policy layer cannot supply a
field, this contract says so with `null` or an empty list rather than inventing a value — a policy
surface that fabricates a figure is worse than one that admits a gap.

Two absences are deliberate and load-bearing:

* **`cause_assessment` is not asserted from `trigger_type`.** `db/trip_context.py` refuses to infer
  "external to carrier, unavoidable despite reasonable measures" from the word `weather`, because
  that is a legal exemption and inferring one from an operational label is exactly the inference the
  compensation service promises never to make. So the block reports what is *recorded*, and when
  nothing is recorded it says `undetermined`.
* **`pack.source_hash`** is the archived source-document hash. Nothing in the loader exposes one yet
  (that is G3, Stream B), so it is `null` here rather than echoed from a literal.

Owner: Stream A. Every figure originates in Stream B's policy engine.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PolicyPackInfo(BaseModel):
    """Pack identity and standing, read from the pack rather than hardcoded."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    status: str
    verified_mode_eligible: bool
    #: Rendered verbatim by the console. Never case-transformed, never composed here.
    ui_label: str
    authority: str
    document: str | None = None
    pack_hash: str
    #: SHA-256 of the archived source document. `null` until G3 records one; never a placeholder
    #: invented at this layer.
    source_hash: str | None = None


class PolicyApplicability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    pack_version: str
    #: `applicable` | `not_applicable` | `undetermined`. Tri-state, never collapsed to a boolean.
    status: str
    basis: dict[str, Any] = Field(default_factory=dict)
    required_facts: list[str] = Field(default_factory=list)
    #: Named, not counted. A missing fact is something an operator can go and find.
    missing_facts: list[str] = Field(default_factory=list)
    resolver_version: str | None = None


class PolicyEntitlement(BaseModel):
    """One entitlement, with the rules and clauses that produced it.

    `outcome` carries the tri-state. `not_owed` is a computed result; `undetermined` means the
    engine could not decide and names what was absent. Neither is rendered as zero.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    outcome: str
    amount_inr: int | None = None
    currency: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    rules_fired: list[str] = Field(default_factory=list)
    source_clause_refs: list[str] = Field(default_factory=list)
    formula_used: str | None = None


class PolicyCauseAssessment(BaseModel):
    """What is *recorded* about the cause, not what the trigger implies.

    Every flag is `bool | None`. `None` means no assessment exists, which is a different statement
    from `false` — and the difference decides whether an exemption applies.
    """

    model_config = ConfigDict(extra="forbid")

    operational_cause: str | None = None
    clearly_attributable: bool | None = None
    external_to_carrier: bool | None = None
    unavoidable_despite_reasonable_measures: bool | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    note: str


class PolicyCauseAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    operational_cause: str
    external_to_carrier: bool
    #: The engine's outcome for the counterfactual. `undetermined` when a formula input was absent.
    outcome: str
    cash_inr: int | None = None
    formula_used: str | None = None
    rules_fired: list[str] = Field(default_factory=list)
    source_clause_refs: list[str] = Field(default_factory=list)
    #: Named when the counterfactual could not be computed. Without this the comparison renders
    #: blank and reads as "nothing changes", which is a different claim from "we could not tell".
    missing_facts: list[str] = Field(default_factory=list)
    note: str


class PolicyCauseComparison(BaseModel):
    """The same incident re-evaluated under a substituted cause.

    A bounded, zero-write re-evaluation of the same pack over altered facts — the P2-D2 pattern. It
    is not a forecast and not a second ruleset: identical rules, different inputs.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    description: str
    alternative: PolicyCauseAlternative | None = None


class PolicyExcludedRule(BaseModel):
    """A rule the pack itself withholds, with the reason. Shown, never silently dropped."""

    model_config = ConfigDict(extra="forbid")

    rule_key: str
    status: str
    reason: str
    evaluated: Literal[False] = False


class PolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_by: str
    note: str
    policy_mode: str
    pack: PolicyPackInfo
    applicability: list[PolicyApplicability] = Field(default_factory=list)
    event: dict[str, Any] = Field(default_factory=dict)
    entitlements: list[PolicyEntitlement] = Field(default_factory=list)
    cause_assessment: PolicyCauseAssessment
    cause_comparison: PolicyCauseComparison | None = None
    excluded_rules: list[PolicyExcludedRule] = Field(default_factory=list)
    #: Legal standing, stated once. Derived from the pack's status, never composed per screen.
    disclaimer: str
    #: Facts the engine needed and did not have, across applicability and evaluation. Present so a
    #: reader can tell "nothing is owed" from "we could not tell".
    missing_facts: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
