"""The zero-write what-if boundary.

What-if is a bounded, deterministic re-evaluation of candidate plans against recorded facts. These
tests are the difference between that and a simulation engine we deliberately did not build.
"""

from __future__ import annotations

import pytest

from app.assurance.plan_contract import WhatIfPolicy
from app.assurance.whatif import WhatIfRefusal, WhatIfRequest, assert_zero_write

POLICY = WhatIfPolicy(
    enabled=True,
    max_candidates=4,
    require_deterministic_seed=True,
    refuse_when_provider_live=True,
    figures_are_non_authoritative=True,
)


def _request(**overrides) -> WhatIfRequest:
    payload = {"candidate_count": 2, "seed": 20260807}
    payload.update(overrides)
    return WhatIfRequest(**payload)


class TestPermitted:
    def test_a_bounded_seeded_offline_comparison_is_permitted(self):
        verdict = assert_zero_write(request=_request(), policy=POLICY)
        assert verdict.permitted
        assert verdict.refusals == []
        assert verdict.seed == 20260807

    def test_the_result_is_always_labelled_and_never_authoritative(self):
        verdict = assert_zero_write(request=_request(), policy=POLICY)
        assert verdict.provenance == "simulated"
        assert verdict.authoritative is False

    def test_fixture_mode_providers_are_fine(self):
        verdict = assert_zero_write(
            request=_request(provider_modes={"weather": "fixture", "llm": "off"}), policy=POLICY
        )
        assert verdict.permitted


class TestRefusals:
    def test_a_live_provider_refuses(self):
        """A comparison must not be able to touch a real API."""
        verdict = assert_zero_write(
            request=_request(provider_modes={"weather": "live"}), policy=POLICY
        )
        assert not verdict.permitted
        assert WhatIfRefusal.PROVIDER_LIVE in verdict.refusals

    def test_armed_dispatch_refuses(self):
        """A rehearsal must not be able to reach a passenger."""
        verdict = assert_zero_write(request=_request(real_dispatch_enabled=True), policy=POLICY)
        assert WhatIfRefusal.DISPATCH_ARMED in verdict.refusals

    @pytest.mark.parametrize(
        "capability", ["writes_records", "commits_inventory", "creates_actions"]
    )
    def test_any_declared_write_refuses(self, capability: str):
        verdict = assert_zero_write(request=_request(**{capability: True}), policy=POLICY)
        assert not verdict.permitted
        assert WhatIfRefusal.WRITE_REQUESTED in verdict.refusals

    def test_a_missing_seed_refuses(self):
        """An unreproducible comparison is not evidence."""
        verdict = assert_zero_write(request=_request(seed=None), policy=POLICY)
        assert WhatIfRefusal.SEED_MISSING in verdict.refusals

    def test_too_many_candidates_refuses(self):
        verdict = assert_zero_write(request=_request(candidate_count=5), policy=POLICY)
        assert WhatIfRefusal.TOO_MANY_CANDIDATES in verdict.refusals

    def test_no_candidates_refuses(self):
        verdict = assert_zero_write(request=_request(candidate_count=0), policy=POLICY)
        assert WhatIfRefusal.NO_CANDIDATES in verdict.refusals

    def test_claiming_a_comparison_figure_is_authoritative_refuses(self):
        """Only the policy engine authorises a number a passenger could rely on."""
        verdict = assert_zero_write(
            request=_request(figures_treated_as_authoritative=True), policy=POLICY
        )
        assert WhatIfRefusal.FIGURE_CLAIMED_AUTHORITATIVE in verdict.refusals

    def test_a_disabled_policy_refuses(self):
        verdict = assert_zero_write(request=_request(), policy=WhatIfPolicy(enabled=False))
        assert WhatIfRefusal.DISABLED in verdict.refusals

    def test_an_unconfigured_policy_refuses(self):
        """The default is off. What-if is opt-in through versioned config."""
        verdict = assert_zero_write(request=_request(), policy=WhatIfPolicy())
        assert not verdict.permitted

    def test_every_refusal_is_collected_not_just_the_first(self):
        """One round of fixes rather than discovering problems one at a time."""
        verdict = assert_zero_write(
            request=_request(
                candidate_count=9,
                seed=None,
                provider_modes={"weather": "live"},
                real_dispatch_enabled=True,
                writes_records=True,
                figures_treated_as_authoritative=True,
            ),
            policy=POLICY,
        )
        assert not verdict.permitted
        assert len(verdict.refusals) == 6
        assert len(verdict.reasons) == len(verdict.refusals)

    def test_a_permissive_policy_can_relax_only_what_it_names(self):
        relaxed = POLICY.model_copy(update={"require_deterministic_seed": False})
        assert assert_zero_write(request=_request(seed=None), policy=relaxed).permitted
        # A write is refused regardless: it is not a configurable tolerance.
        assert not assert_zero_write(
            request=_request(seed=None, creates_actions=True), policy=relaxed
        ).permitted


class TestDeterminism:
    def test_the_same_request_yields_the_same_verdict(self):
        first = assert_zero_write(request=_request(), policy=POLICY)
        second = assert_zero_write(request=_request(), policy=POLICY)
        assert first.model_dump() == second.model_dump()

    def test_the_request_rejects_an_unrecognised_capability(self):
        """A caller cannot smuggle a capability past the guard by naming it something new."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WhatIfRequest(candidate_count=1, seed=1, mutates_world_state=True)
