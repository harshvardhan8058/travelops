"""Zero-write what-if guard — STREAM B, new in Phase 2.

What-if here is a **bounded, zero-write, deterministic re-evaluation** of candidate recovery plans
against facts that have already been recorded. It is explicitly **not** a simulation engine or a
digital twin: there is no world model, no projected weather, no invented future state, and no write
of any kind.

That boundary is not a comment. This module refuses a comparison that could reach anything real:

    provider in live mode        refuse — a comparison must not touch a real API
    notification dispatch armed  refuse — a rehearsal must not reach a passenger
    inventory commit requested   refuse — comparing plans must not consume the rooms
    no deterministic seed        refuse — an unreproducible comparison is not evidence
    a write of any kind          refuse — including an Action row
    figure claimed authoritative refuse — only the policy engine authorises an entitlement

The last one matters most. A comparison can show what an entitlement *would* be; it can never be
the source of the number that reaches a passenger. Only `app.policy.entitlements.calculate` does
that, against a loaded pack, with clause references.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.assurance.plan_contract import WhatIfPolicy


class WhatIfRefusal(StrEnum):
    DISABLED = "WHATIF_DISABLED"
    PROVIDER_LIVE = "WHATIF_PROVIDER_LIVE"
    DISPATCH_ARMED = "WHATIF_DISPATCH_ARMED"
    WRITE_REQUESTED = "WHATIF_WRITE_REQUESTED"
    SEED_MISSING = "WHATIF_SEED_MISSING"
    TOO_MANY_CANDIDATES = "WHATIF_TOO_MANY_CANDIDATES"
    FIGURE_CLAIMED_AUTHORITATIVE = "WHATIF_FIGURE_CLAIMED_AUTHORITATIVE"
    NO_CANDIDATES = "WHATIF_NO_CANDIDATES"


class WhatIfRequest(BaseModel):
    """A requested comparison, and every capability it would need.

    The caller declares its intentions and this module refuses the ones that are not permitted.
    Declaring nothing is the safe default: every risky capability defaults to False.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_count: int
    #: Required, and recorded on the result. Same seed, same inputs, same comparison.
    seed: int | None = None

    #: Live provider modes observed at request time, e.g. {"weather": "live"}.
    provider_modes: dict[str, str] = Field(default_factory=dict)
    #: True when real delivery is configured and armed.
    real_dispatch_enabled: bool = False

    #: Declared intent to write. Any of these is a refusal.
    writes_records: bool = False
    commits_inventory: bool = False
    creates_actions: bool = False

    #: True when the caller intends a comparison figure to be used as the entitlement.
    figures_treated_as_authoritative: bool = False


class WhatIfVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permitted: bool
    refusals: list[WhatIfRefusal] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    seed: int | None = None
    #: Recorded on every comparison result so nothing downstream can present it as an outcome.
    provenance: str = "simulated"
    authoritative: bool = False


def assert_zero_write(*, request: WhatIfRequest, policy: WhatIfPolicy) -> WhatIfVerdict:
    """Decide whether a comparison may run at all.

    Collects every refusal rather than returning the first, so a caller fixes one round of problems
    instead of discovering them one at a time. Fail-closed: a disabled or unconfigured policy
    refuses.
    """
    refusals: list[WhatIfRefusal] = []
    reasons: list[str] = []

    def refuse(refusal: WhatIfRefusal, reason: str) -> None:
        refusals.append(refusal)
        reasons.append(reason)

    if not policy.enabled:
        refuse(WhatIfRefusal.DISABLED, "what-if comparison is not enabled in the versioned config")

    if request.candidate_count <= 0:
        refuse(WhatIfRefusal.NO_CANDIDATES, "no candidates were supplied to compare")
    elif request.candidate_count > policy.max_candidates:
        refuse(
            WhatIfRefusal.TOO_MANY_CANDIDATES,
            f"{request.candidate_count} candidates exceeds the configured maximum of "
            f"{policy.max_candidates}",
        )

    if policy.require_deterministic_seed and request.seed is None:
        refuse(
            WhatIfRefusal.SEED_MISSING,
            "no deterministic seed was supplied; an unreproducible comparison is not evidence",
        )

    if policy.refuse_when_provider_live:
        live = sorted(name for name, mode in request.provider_modes.items() if mode == "live")
        if live:
            refuse(
                WhatIfRefusal.PROVIDER_LIVE,
                f"provider(s) in live mode: {', '.join(live)}; a comparison must not be able to "
                "touch a real API",
            )

    if request.real_dispatch_enabled:
        refuse(
            WhatIfRefusal.DISPATCH_ARMED,
            "real delivery is armed; a rehearsal must not be able to reach a passenger",
        )

    writes = [
        name
        for name, requested in (
            ("writes_records", request.writes_records),
            ("commits_inventory", request.commits_inventory),
            ("creates_actions", request.creates_actions),
        )
        if requested
    ]
    if writes:
        refuse(
            WhatIfRefusal.WRITE_REQUESTED,
            f"a write was requested ({', '.join(writes)}); what-if is zero-write by definition, "
            "and a comparison that writes is a simulation engine we did not build",
        )

    if request.figures_treated_as_authoritative and policy.figures_are_non_authoritative:
        refuse(
            WhatIfRefusal.FIGURE_CLAIMED_AUTHORITATIVE,
            "a comparison figure cannot be the entitlement; only the policy engine authorises a "
            "number, against a loaded pack, with clause references",
        )

    return WhatIfVerdict(
        permitted=not refusals,
        refusals=refusals,
        reasons=reasons,
        seed=request.seed,
        provenance="simulated",
        authoritative=False,
    )
