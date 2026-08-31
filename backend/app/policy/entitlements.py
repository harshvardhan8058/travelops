"""The entitlement calculation, as a callable — STREAM B owns the law.

This is the single entry point Stream C's compensation service calls. That service assembles
facts and calls `calculate`; it must never compute an amount itself, and it must never infer a
legal outcome from `trigger_type`.

What comes back is deliberately more than a number:

    CitedEntitlement(
        cash_inr=5000,
        formula="least_of_cap_and_basic_fare_plus_fuel_charge",
        formula_used="least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000",
        source_clause_refs=["charter:p3:flight-cancellation:scenario-2-B"],
        pack_version="2019.02",
        ...
    )

so the UI can render the derivation rather than a bare figure. A number a passenger could rely
on has to show where it came from.

The whole pipeline runs here in order: load the pack under the running POLICY_MODE, resolve
applicability, then evaluate. Any step may block, and a block is a normal outcome that arrives
as `requires_human`, not an exception — except for pack loading, which raises, because a
missing or ineligible pack is a configuration fault rather than a fact about this passenger.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.config import PolicyMode, Settings, get_settings, resolve_repo_path
from app.policy.engine import (
    OUTCOME_NEEDS_HUMAN,
    EntitlementResult,
    evaluate,
)
from app.policy.loader import LoadedPack, load_pack
from app.policy.resolver import PROCEED, Resolution, select

#: Recorded when the resolver could not settle which pack governs the trip.
REASON_APPLICABILITY_UNRESOLVED: Final = "APPLICABILITY_UNRESOLVED"


class CitedEntitlement(BaseModel):
    """An entitlement with everything needed to defend it.

    Every field the UI needs to render a citation card is here, so no caller has to reach back
    into the pack or re-derive a figure.
    """

    model_config = ConfigDict(extra="forbid")

    #: 'evaluated' | 'suppressed' | 'needs_human'
    outcome: str
    cash_inr: int | None = None
    currency: str | None = None
    cash_reason_codes: list[str] = Field(default_factory=list)

    #: The named formula the pack selected, e.g. least_of_cap_and_basic_fare_plus_fuel_charge.
    formula: str | None = None
    #: Its rendered derivation, e.g. "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000".
    formula_used: str | None = None

    entitlements: list[dict[str, Any]] = Field(default_factory=list)
    obligations: list[dict[str, Any]] = Field(default_factory=list)
    source_clause_refs: list[str] = Field(default_factory=list)

    rules_fired: list[str] = Field(default_factory=list)
    excluded_rules: list[str] = Field(default_factory=list)
    undetermined_rules: list[dict[str, Any]] = Field(default_factory=list)
    notices: list[dict[str, Any]] = Field(default_factory=list)

    blocking_reasons: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)

    pack_id: str | None = None
    pack_version: str | None = None
    pack_hash: str | None = None
    pack_status: str | None = None
    verified_mode_eligible: bool = False
    source_document_verified: bool = False
    pack_ui_label: str | None = None
    policy_mode: str
    engine_version: str | None = None

    applicability: list[dict[str, Any]] = Field(default_factory=list)
    resolver_version: str | None = None

    @property
    def requires_human(self) -> bool:
        return self.outcome == OUTCOME_NEEDS_HUMAN

    @property
    def may_be_presented_as_current_law(self) -> bool:
        """Only approved, verified-eligible, source-verified policy has that standing."""
        return (
            self.pack_status == "approved"
            and self.verified_mode_eligible
            and self.source_document_verified
        )

    @property
    def has_citation(self) -> bool:
        return bool(self.source_clause_refs)


def _from_resolution(
    *, resolution: Resolution, pack: LoadedPack, mode: PolicyMode
) -> CitedEntitlement:
    """A block raised by the resolver, before any rule was evaluated."""
    return CitedEntitlement(
        outcome=OUTCOME_NEEDS_HUMAN,
        cash_inr=None,
        currency=pack.currency,
        blocking_reasons=[REASON_APPLICABILITY_UNRESOLVED, *resolution.blocking_reasons],
        missing_facts=list(resolution.missing_facts),
        pack_id=pack.pack_id,
        pack_version=pack.version,
        pack_hash=pack.pack_hash,
        pack_status=pack.status.value,
        verified_mode_eligible=pack.verified_mode_eligible,
        source_document_verified=pack.source_document_verified,
        pack_ui_label=pack.ui_label,
        policy_mode=mode.value,
        applicability=[candidate.model_dump(mode="json") for candidate in resolution.candidates],
        resolver_version=resolution.resolver_version,
    )


def _from_result(
    *,
    result: EntitlementResult,
    resolution: Resolution,
    mode: PolicyMode,
) -> CitedEntitlement:
    return CitedEntitlement(
        outcome=result.outcome,
        cash_inr=result.cash_inr,
        currency=result.currency,
        cash_reason_codes=list(result.cash_reason_codes),
        formula=result.formula,
        formula_used=result.formula_used,
        entitlements=list(result.entitlements),
        obligations=list(result.obligations),
        source_clause_refs=list(result.source_clause_refs),
        rules_fired=list(result.rules_fired),
        excluded_rules=list(result.excluded_rules),
        undetermined_rules=list(result.undetermined_rules),
        notices=list(result.notices),
        blocking_reasons=list(result.blocking_reasons),
        missing_facts=list(result.missing_facts),
        pack_id=result.pack_id,
        pack_version=result.pack_version,
        pack_hash=result.pack_hash,
        pack_status=result.pack_status,
        verified_mode_eligible=result.verified_mode_eligible,
        source_document_verified=result.source_document_verified,
        pack_ui_label=result.pack_ui_label,
        policy_mode=mode.value,
        engine_version=result.engine_version,
        applicability=[candidate.model_dump(mode="json") for candidate in resolution.candidates],
        resolver_version=resolution.resolver_version,
    )


#: The fictional pack `POLICY_MODE=demo` loads. Fixed here rather than read from
#: `POLICY_PACK_ID`, deliberately: demo mode must not be pointable at a real authority's pack.
#: Selecting it by configuration would mean one wrong environment variable puts the charter's
#: real figures behind a badge that says the numbers are invented.
DEMO_PACK_ID: Final = "demo-fixture"
DEMO_PACK_VERSION: Final = "1.0"


def active_pack_coordinates(settings: Settings) -> tuple[str, str]:
    """Which pack id and version the running POLICY_MODE resolves to.

    One authoritative answer, used by every caller that loads the active pack, so demo mode cannot
    be honoured in one code path and ignored in another.
    """
    if settings.policy_mode is PolicyMode.demo:
        return DEMO_PACK_ID, DEMO_PACK_VERSION
    return settings.policy_pack_id, settings.policy_pack_version


def load_active_pack(settings: Settings | None = None) -> LoadedPack:
    """Load the pack for the configured POLICY_MODE.

    In `demo` mode this resolves to the fictional fixture pack regardless of `POLICY_PACK_ID`.
    That is not a convenience: the loader already refuses to load a non-fixture pack in demo mode,
    so honouring the configured id there would simply fail, and honouring it *successfully* would
    be worse — invented-figure labelling over real figures.

    Raises PolicyPackUnavailable or PackNotVerifiedEligible. Both are configuration faults, so
    they surface as errors rather than as a needs_human outcome about a passenger.
    """
    active = settings or get_settings()
    pack_id, pack_version = active_pack_coordinates(active)
    return load_pack(
        # Resolved against the repo root when relative, exactly as the assurance config is.
        # `POLICY_PACK_DIR=./policy_packs` otherwise means a different directory depending on
        # whether the process started from the repo root, from `backend/`, or in the container,
        # and the symptom is a legal pack "not found" rather than an obvious path error.
        pack_dir=resolve_repo_path(Path(active.policy_pack_dir)),
        pack_id=pack_id,
        version=pack_version,
        mode=active.policy_mode,
    )


def calculate(
    *,
    facts: dict[str, Any],
    pack: LoadedPack | None = None,
    settings: Settings | None = None,
    resolve_applicability: bool = True,
) -> CitedEntitlement:
    """Compute an entitlement, cited and pinned to the pack that produced it.

    This is the function Stream C's compensation service calls. `facts` is the trip context
    described in docs/13-compensation-and-policy.md — event, flight, fare, passenger,
    alternate, carrier and cause evidence.

    Pass `pack` to avoid re-reading it per passenger. Set `resolve_applicability=False` only
    when the caller has already run the resolver and is evaluating the selected pack.

    Never raises for a missing fact. A gap in the facts is a `needs_human` outcome that names
    what was absent, because that is information an operator can act on.
    """
    active = settings or get_settings()
    loaded = pack or load_active_pack(active)

    resolution = Resolution(decision=PROCEED)
    if resolve_applicability:
        resolution = select(trip_context=facts, packs=[loaded])
        if resolution.decision != PROCEED:
            return _from_resolution(resolution=resolution, pack=loaded, mode=active.policy_mode)

    result = evaluate(facts=facts, pack=loaded)
    return _from_result(result=result, resolution=resolution, mode=active.policy_mode)
