"""A plan comparison must declare its real provider modes to the zero-write guard.

Both call sites used to pass the literal `{"weather": "fixture", "notification": "console"}`. That
is a lie told to a safety check: `assert_zero_write` has a `refuse_when_provider_live` policy —
enabled in `assurance.v2.yaml` — whose only job is to refuse a comparison when a provider is live,
and a hardcoded "fixture" meant it could never fire however the deployment was configured. A guard
handed its own answer is not a guard.

These tests pin the three postures the brief asks for — fixture, live, and off/unavailable — and,
critically, that the guard's verdict actually *changes* with them. A test that only asserted the
dictionary's contents would have passed against the hardcoded version too.

Owner: Stream A (declaration), Stream B (the rule it feeds).
"""

from __future__ import annotations

import pytest

from app.assurance.plan_contract import WhatIfPolicy
from app.assurance.whatif import WhatIfRefusal, WhatIfRequest, assert_zero_write
from app.config import (
    FlightStatusMode,
    LLMMode,
    NotificationMode,
    PolicyMode,
    ResolvedModes,
    WeatherMode,
    comparison_provider_modes,
)


def _modes(
    *,
    weather: WeatherMode = WeatherMode.fixture,
    flight_status: FlightStatusMode = FlightStatusMode.fixture,
    llm: LLMMode = LLMMode.fixture,
    real_email_enabled: bool = False,
) -> ResolvedModes:
    return ResolvedModes(
        llm=llm,
        weather=weather,
        notification=NotificationMode.console,
        policy=PolicyMode.charter,
        real_email_enabled=real_email_enabled,
        assurance_config_present=True,
        assurance_config_version="assurance-v1",
        assurance_config_hash="deadbeef",
        degradations=[],
        flight_status=flight_status,
    )


def _request(provider_modes: dict[str, str], *, real_dispatch: bool = False) -> WhatIfRequest:
    """A request that is otherwise entirely admissible, so only the provider modes can refuse it."""
    return WhatIfRequest(
        candidate_count=2,
        seed=20260820,
        provider_modes=provider_modes,
        real_dispatch_enabled=real_dispatch,
        writes_records=False,
        commits_inventory=False,
        creates_actions=False,
        figures_treated_as_authoritative=False,
    )


POLICY = WhatIfPolicy(
    enabled=True,
    max_candidates=4,
    require_deterministic_seed=True,
    refuse_when_provider_live=True,
    figures_are_non_authoritative=True,
)


class TestTheDeclarationTracksConfiguration:
    def test_fixture_everywhere_declares_fixture(self):
        assert comparison_provider_modes(_modes()) == {
            "weather": "fixture",
            "flight_status": "fixture",
        }

    def test_a_live_weather_provider_is_declared_live(self):
        modes = _modes(weather=WeatherMode.live)
        assert comparison_provider_modes(modes)["weather"] == "live"

    def test_a_live_flight_status_provider_is_declared_live(self):
        modes = _modes(flight_status=FlightStatusMode.live)
        assert comparison_provider_modes(modes)["flight_status"] == "live"

    def test_the_llm_is_deliberately_not_declared(self):
        """Comparison candidates are deterministic variants; no agent contributes to them.

        Declaring the LLM here would refuse a comparison on account of a provider that cannot
        influence its result — over-refusing is a defect too, just a quieter one.
        """
        assert "llm" not in comparison_provider_modes(_modes(llm=LLMMode.live))

    def test_notification_is_carried_by_real_dispatch_not_by_a_mode(self):
        """It is an output channel, and `real_dispatch_enabled` states the risk more precisely."""
        assert "notification" not in comparison_provider_modes(_modes(real_email_enabled=True))


class TestTheGuardsVerdictActuallyChanges:
    """The part that would have passed against the hardcoded version, had it only checked a dict."""

    def test_fixture_posture_is_permitted(self):
        verdict = assert_zero_write(
            request=_request(comparison_provider_modes(_modes())), policy=POLICY
        )
        assert verdict.permitted, verdict.refusals

    @pytest.mark.parametrize(
        "modes",
        [
            _modes(weather=WeatherMode.live),
            _modes(flight_status=FlightStatusMode.live),
        ],
        ids=["weather-live", "flight-status-live"],
    )
    def test_a_live_provider_is_refused(self, modes):
        verdict = assert_zero_write(
            request=_request(comparison_provider_modes(modes)), policy=POLICY
        )
        assert not verdict.permitted
        assert WhatIfRefusal.PROVIDER_LIVE in verdict.refusals

    def test_armed_real_delivery_is_refused_independently(self):
        """Off/unavailable providers with delivery armed: the other half of the posture."""
        verdict = assert_zero_write(
            request=_request(comparison_provider_modes(_modes()), real_dispatch=True),
            policy=POLICY,
        )
        assert not verdict.permitted
        assert WhatIfRefusal.DISPATCH_ARMED in verdict.refusals

    def test_the_zero_write_refusals_still_stand(self):
        """Declaring real modes must not have loosened anything else the guard checks."""
        request = _request(comparison_provider_modes(_modes())).model_copy(
            update={"writes_records": True}
        )
        verdict = assert_zero_write(request=request, policy=POLICY)
        assert not verdict.permitted
        assert WhatIfRefusal.WRITE_REQUESTED in verdict.refusals
