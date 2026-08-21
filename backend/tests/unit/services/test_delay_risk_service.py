"""Delay Risk: thresholds, boundary conditions and reproducibility.

The crosswind maths itself is frozen in `tests/unit/test_crosswind.py` and is not retested
here. This file covers the index, the bands, the runway choice and the honesty constraints —
above all that the output is never presented as a probability.
"""

from __future__ import annotations

import pytest
from data.loaders.ourairports import load_runways

from app.models.enums import ActionStatus, ProvenanceKind, RiskLevel
from app.services.delay_risk import (
    DEFAULT_RULESET,
    RULE_VERSION,
    Band,
    DelayRiskService,
    RunwayOption,
    WeatherInput,
    assess,
    ruleset_from_constraints,
    select_runway,
)

STORM = WeatherInput(
    airport_icao="VOBL",
    wind_speed_kt=24,
    wind_direction_deg=250,
    visibility_m=800,
    ceiling_ft=900,
    precipitation="rain",
    observation_age_minutes=0,
    source_ref="fixture:bengaluru_storm:metar:VOBL",
    provenance_kind=ProvenanceKind.fixture.value,
)


@pytest.fixture(scope="module")
def bengaluru_runways() -> list[RunwayOption]:
    return [
        RunwayOption(
            designator=runway.designator,
            heading_degrees_true=runway.heading_degrees_true,
            heading_source=runway.heading_source,
            is_active=runway.is_active,
        )
        for runway in load_runways()
        if runway.airport_icao == "VOBL"
    ]


@pytest.fixture
def service() -> DelayRiskService:
    return DelayRiskService()


def _calm(**overrides) -> WeatherInput:
    base = {
        "airport_icao": "VOBL",
        "wind_speed_kt": 8,
        "wind_direction_deg": 270,
        "visibility_m": 9000,
        "ceiling_ft": None,
        "precipitation": None,
    }
    base.update(overrides)
    return WeatherInput(**base)


# ---------------------------------------------------------------- the scenario target


def test_storm_reaches_the_scenario_target(bengaluru_runways):
    """data/fixtures/bengaluru_storm.yaml expects risk_index_min 75 and level severe."""
    result = assess(weather=STORM, runways=bengaluru_runways)
    assert result.risk_index >= 75
    assert result.risk_level is RiskLevel.severe


def test_storm_index_is_the_sum_of_its_named_factors(bengaluru_runways):
    """No hidden term: the index must be readable off the factor list."""
    result = assess(weather=STORM, runways=bengaluru_runways)
    assert result.risk_index == sum(factor.points for factor in result.factors)


def test_storm_names_every_contributing_condition(bengaluru_runways):
    result = assess(weather=STORM, runways=bengaluru_runways)
    names = {factor.name for factor in result.factors}
    assert "visibility_low_visibility_procedures" in names
    assert "ceiling_low" in names
    assert "precipitation_rain" in names
    assert "low_visibility_with_low_ceiling" in names


def test_calm_weather_scores_zero(bengaluru_runways):
    result = assess(weather=_calm(), runways=bengaluru_runways)
    assert result.risk_index == 0
    assert result.risk_level is RiskLevel.low


# ------------------------------------------------------------------- not a probability


def test_the_output_is_never_a_probability(bengaluru_runways):
    """Nothing here is calibrated against observed outcomes, so a percentage would be an
    unearned claim."""
    result = assess(weather=STORM, runways=bengaluru_runways)
    assert result.is_probability is False
    dumped = result.model_dump(mode="json")
    assert "probability" not in dumped
    assert "confidence" not in dumped


async def test_payload_carries_no_confidence_field(service, bengaluru_runways):
    result = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    assert "confidence" not in result.payload
    assert "probability" not in result.payload


def test_index_is_bounded_at_100():
    """Every band at maximum must still land inside the contract."""
    worst = WeatherInput(
        airport_icao="VOBL",
        wind_speed_kt=60,
        wind_direction_deg=0,
        visibility_m=0,
        ceiling_ft=0,
        precipitation="thunderstorm",
    )
    runways = [RunwayOption(designator="09", heading_degrees_true=90)]
    result = assess(weather=worst, runways=runways)
    assert result.risk_index == 100
    assert result.risk_level is RiskLevel.severe


# ----------------------------------------------------------------------- level bands


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, RiskLevel.low),
        (24, RiskLevel.low),
        (25, RiskLevel.elevated),
        (49, RiskLevel.elevated),
        (50, RiskLevel.high),
        (74, RiskLevel.high),
        (75, RiskLevel.severe),
        (100, RiskLevel.severe),
    ],
)
def test_level_band_boundaries(index: int, expected: RiskLevel):
    assert DEFAULT_RULESET.level_for(index) is expected


@pytest.mark.parametrize(
    ("visibility_m", "expected_points"),
    [(550, 34), (551, 30), (800, 30), (801, 20), (1500, 20), (1501, 11), (5000, 5), (5001, 0)],
)
def test_visibility_band_boundaries(visibility_m: int, expected_points: int):
    result = assess(weather=_calm(visibility_m=visibility_m, ceiling_ft=None), runways=[])
    visibility_points = sum(
        factor.points for factor in result.factors if factor.name.startswith("visibility_")
    )
    assert visibility_points == expected_points


@pytest.mark.parametrize(
    ("ceiling_ft", "expected_points"),
    [(200, 30), (201, 26), (500, 26), (501, 22), (1000, 22), (1001, 12), (3000, 5), (3001, 0)],
)
def test_ceiling_band_boundaries(ceiling_ft: int, expected_points: int):
    result = assess(weather=_calm(ceiling_ft=ceiling_ft, visibility_m=9000), runways=[])
    ceiling_points = sum(
        factor.points for factor in result.factors if factor.name.startswith("ceiling_")
    )
    assert ceiling_points == expected_points


@pytest.mark.parametrize(
    ("wind_speed_kt", "expected_points"),
    [(11, 0), (12, 3), (17, 3), (18, 7), (24, 7), (25, 10), (35, 14), (45, 18), (60, 18)],
)
def test_wind_band_boundaries(wind_speed_kt: int, expected_points: int):
    result = assess(weather=_calm(wind_speed_kt=wind_speed_kt), runways=[])
    wind_points = sum(factor.points for factor in result.factors if factor.name.startswith("wind_"))
    assert wind_points == expected_points


def test_compounding_factor_needs_both_conditions():
    """Low visibility alone, or a low ceiling alone, must not trigger the interaction term."""
    visibility_only = assess(weather=_calm(visibility_m=800, ceiling_ft=None), runways=[])
    ceiling_only = assess(weather=_calm(visibility_m=9000, ceiling_ft=900), runways=[])
    both = assess(weather=_calm(visibility_m=800, ceiling_ft=900), runways=[])

    names = lambda result: {f.name for f in result.factors}  # noqa: E731
    assert "low_visibility_with_low_ceiling" not in names(visibility_only)
    assert "low_visibility_with_low_ceiling" not in names(ceiling_only)
    assert "low_visibility_with_low_ceiling" in names(both)


# --------------------------------------------------------------------- runway choice


def test_runway_in_use_is_the_one_with_most_headwind(bengaluru_runways):
    """What a controller would actually do, and the reason headings are loaded at all."""
    selection = select_runway(runways=bengaluru_runways, wind_speed_kt=24, wind_direction_deg=250)
    assert selection is not None
    assert selection.designator.startswith("27")
    assert selection.headwind_kt > 0


def test_opposite_wind_selects_the_opposite_end(bengaluru_runways):
    selection = select_runway(runways=bengaluru_runways, wind_speed_kt=24, wind_direction_deg=80)
    assert selection is not None
    assert selection.designator.startswith("09")


def test_aligned_wind_contributes_no_crosswind(bengaluru_runways):
    """The demo's clearest illustration that raw wind speed is not a usable rule: 24 kt of
    wind, nearly down the runway, adds nothing."""
    result = assess(weather=STORM, runways=bengaluru_runways)
    crosswind_factors = [f for f in result.factors if f.name.startswith("crosswind")]
    assert len(crosswind_factors) == 1
    assert crosswind_factors[0].name == "crosswind_negligible"
    assert crosswind_factors[0].points == 0


def test_strong_crosswind_does_contribute():
    """Same wind speed, rotated across the runway, must score."""
    runways = [RunwayOption(designator="09", heading_degrees_true=90)]
    across = WeatherInput(
        airport_icao="VOBL", wind_speed_kt=24, wind_direction_deg=180, visibility_m=9000
    )
    result = assess(weather=across, runways=runways)
    crosswind = next(f for f in result.factors if f.name.startswith("crosswind"))
    assert crosswind.points > 0
    assert crosswind.observed_value == pytest.approx(24.0, abs=0.1)


def test_closed_runways_are_not_selected():
    runways = [
        RunwayOption(designator="09", heading_degrees_true=90, is_active=False),
        RunwayOption(designator="18", heading_degrees_true=180, is_active=True),
    ]
    selection = select_runway(runways=runways, wind_speed_kt=20, wind_direction_deg=90)
    assert selection is not None
    assert selection.designator == "18"


def test_variable_wind_direction_omits_crosswind_rather_than_guessing(bengaluru_runways):
    """Defaulting VRB to 0 degrees would compute a crosswind against a northerly that was
    never reported."""
    result = assess(
        weather=_calm(wind_direction_deg=None, wind_speed_kt=20), runways=bengaluru_runways
    )
    assert result.runway is None
    assert "wind_direction_deg" in result.missing_inputs
    assert not [f for f in result.factors if f.name.startswith("crosswind")]


def test_runway_selection_is_reported_with_its_heading_source(bengaluru_runways):
    """A derived heading must be visible in the output, not silently equivalent to surveyed."""
    result = assess(weather=STORM, runways=bengaluru_runways)
    assert result.runway is not None
    assert result.runway.heading_source in {"ourairports_true", "designator_derived"}
    crosswind = next(f for f in result.factors if f.name.startswith("crosswind"))
    assert result.runway.heading_source in crosswind.detail


def test_runway_choice_is_deterministic_under_a_tie():
    """Two identical headings must resolve the same way every time."""
    runways = [
        RunwayOption(designator="09R", heading_degrees_true=90),
        RunwayOption(designator="09L", heading_degrees_true=90),
    ]
    first = select_runway(runways=runways, wind_speed_kt=20, wind_direction_deg=90)
    second = select_runway(runways=list(reversed(runways)), wind_speed_kt=20, wind_direction_deg=90)
    assert first is not None and second is not None
    assert first.designator == second.designator == "09L"


# --------------------------------------------------------------- thresholds are data


def test_no_threshold_literal_appears_in_the_module():
    """Every number lives in the ruleset. A literal in the scoring path would be a magic
    number by definition."""
    import ast
    import inspect

    import app.services.delay_risk as module

    source = inspect.getsource(module.assess)
    tree = ast.parse(source.strip())
    numbers = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float)
    ]
    # 100 caps the contract range; 0 is "contributes nothing" for an unknown precipitation
    # code and for the negligible-crosswind note. Neither is a threshold. Any other number
    # appearing here would be one.
    assert set(numbers) <= {0, 100}, f"unexpected literals in assess(): {numbers}"


def test_ruleset_is_read_from_business_constraints():
    custom = DEFAULT_RULESET.model_copy(
        update={
            "version": "delay-risk-test",
            "visibility_m": (Band(label="visibility_test", at_or_below=1000, points=99),),
        }
    )
    rows = [
        {
            "service": "delay_risk_service",
            "constraint_key": "ruleset",
            "constraint_value": custom.model_dump(mode="json"),
        }
    ]
    resolved = ruleset_from_constraints(rows)
    assert resolved.version == "delay-risk-test"

    result = assess(weather=_calm(visibility_m=900, ceiling_ft=None), runways=[], ruleset=resolved)
    assert result.risk_index == 99
    assert result.ruleset_version == "delay-risk-test"


def test_missing_constraint_rows_fall_back_to_the_seeded_default():
    """ "No rows" and "seeded rows" must score identically, or the fallback is a second,
    divergent ruleset."""
    assert ruleset_from_constraints(None) is DEFAULT_RULESET
    assert ruleset_from_constraints([]) is DEFAULT_RULESET


def test_ruleset_hash_pins_the_numbers_used():
    result = assess(weather=STORM, runways=[])
    assert result.ruleset_hash == DEFAULT_RULESET.hash()
    assert len(result.ruleset_hash) == 16


def test_changing_a_band_changes_the_hash():
    """A recorded prediction must be replayable against the exact numbers that made it."""
    altered = DEFAULT_RULESET.model_copy(update={"compounding_points": 11})
    assert altered.hash() != DEFAULT_RULESET.hash()


# ----------------------------------------------------------------- the service wrapper


async def test_service_returns_the_assessment(service, bengaluru_runways):
    result = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    assert result.status is ActionStatus.success
    assert result.payload["risk_index"] == 80
    assert result.payload["risk_level"] == "severe"
    assert result.payload["rule_version"] == RULE_VERSION


async def test_event_threshold_comes_from_config_not_a_literal(service, bengaluru_runways):
    at_75 = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    at_95 = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=95)
    assert at_75.payload["event_recommended"] is True
    assert at_95.payload["event_recommended"] is False


async def test_evidence_names_the_observation_runway_and_ruleset(service, bengaluru_runways):
    result = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    refs = result.evidence_refs
    assert "airport:VOBL" in refs
    assert any(ref.startswith("observation:fixture:bengaluru_storm") for ref in refs)
    assert any(ref.startswith("runway:VOBL:") for ref in refs)
    assert any(ref.startswith("ruleset:delay-risk-v1:") for ref in refs)


async def test_provenance_is_carried_from_the_observation(service, bengaluru_runways):
    result = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    assert result.provenance_kind == ProvenanceKind.fixture.value

    real = STORM.model_copy(update={"provenance_kind": ProvenanceKind.real.value})
    live = await service.execute(weather=real, runways=bengaluru_runways, event_threshold=75)
    assert live.provenance_kind == ProvenanceKind.real.value


async def test_observation_age_and_staleness_are_explicit(service, bengaluru_runways):
    stale = STORM.model_copy(update={"observation_age_minutes": 240, "is_stale": True})
    result = await service.execute(weather=stale, runways=bengaluru_runways, event_threshold=75)
    assert result.payload["observation_age_minutes"] == 240
    assert result.payload["is_stale"] is True


async def test_no_weather_is_needs_human_not_a_calm_score(service):
    """A default score would read as clear conditions, which is the worst failure mode."""
    result = await service.execute(runways=[])
    assert result.status is ActionStatus.needs_human
    assert result.provenance_kind == ProvenanceKind.unavailable.value
    assert "missing_inputs" in result.payload


@pytest.mark.parametrize("missing", ["wind_speed_kt", "visibility_m"])
async def test_missing_core_observation_field_is_needs_human(service, bengaluru_runways, missing):
    """An index resting on fewer factors than it should must not be presented as complete."""
    weather = STORM.model_copy(update={missing: None})
    result = await service.execute(weather=weather, runways=bengaluru_runways, event_threshold=75)
    assert result.status is ActionStatus.needs_human
    assert missing in result.payload["missing_inputs"]


async def test_absent_ceiling_is_an_observation_not_a_missing_input(service, bengaluru_runways):
    """No ceiling means no broken or overcast layer. That is information, not a gap."""
    weather = STORM.model_copy(update={"ceiling_ft": None})
    result = await service.execute(weather=weather, runways=bengaluru_runways, event_threshold=75)
    assert result.status is ActionStatus.success
    assert "ceiling_ft" not in result.payload["missing_inputs"]


async def test_missing_runways_is_recorded_but_still_scores(service):
    """Visibility and ceiling are airport-wide. Losing the runway list costs the crosswind
    factor, and that loss is reported rather than hidden."""
    result = await service.execute(weather=STORM, runways=[], event_threshold=75)
    assert result.status is ActionStatus.success
    assert "runways" in result.payload["missing_inputs"]
    assert result.payload["runway"] is None


async def test_accepts_plain_dicts_for_transport_across_a_queue(service, bengaluru_runways):
    result = await service.execute(
        weather=STORM.model_dump(mode="json"),
        runways=[runway.model_dump(mode="json") for runway in bengaluru_runways],
        event_threshold=75,
    )
    assert result.status is ActionStatus.success
    assert result.payload["risk_index"] == 80


# --------------------------------------------------------------------- reproducibility


async def test_identical_input_yields_identical_output(service, bengaluru_runways):
    first = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    second = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


async def test_output_is_independent_of_runway_input_order(service, bengaluru_runways):
    forward = await service.execute(weather=STORM, runways=bengaluru_runways, event_threshold=75)
    reversed_ = await service.execute(
        weather=STORM, runways=list(reversed(bengaluru_runways)), event_threshold=75
    )
    assert forward.model_dump(mode="json") == reversed_.model_dump(mode="json")


def test_assess_has_no_clock_dependency():
    """`assess` takes no `now`, so it cannot drift between runs."""
    import inspect

    signature = inspect.signature(assess)
    assert "now" not in signature.parameters
    assert "assessed_at" not in signature.parameters
