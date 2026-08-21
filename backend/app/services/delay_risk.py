"""Delay Risk service — STREAM C, first slice.

Returns a deterministic risk INDEX (0-100) and LEVEL, plus the named factors that produced
it and a rule version.

It is NOT a probability. Nothing here is calibrated against observed outcomes, so calling
it "87% chance of delay" would be an unearned claim. The UI shows the index, the band, and
the contributing factors.

Inputs to consider: wind speed, crosswind component against runway heading, visibility,
ceiling, precipitation. Thresholds come from config — never hardcode a number here.

## What the index measures

**Delay** risk, not safety. 800 m visibility with a 900 ft ceiling is above CAT I minima and
perfectly flyable, but it puts an airport into low-visibility procedures: wider spacing, a
reduced arrival rate, holding. That is why delays cascade in conditions that are legally fine,
and it is why the visibility and ceiling bands carry more weight here than the wind bands do.

## Why the bands live in the database

Every number is in `DEFAULT_RULESET`, seeded into `business_constraint` and hashed. The
service reads a ruleset object; it never contains a literal threshold. `ruleset_version` and
`ruleset_hash` go into the output, so a recorded prediction can always be replayed against
the exact numbers that produced it.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ActionStatus, ProvenanceKind, RiskLevel
from app.services.base import ServiceResult

RULE_VERSION = "delay-risk-v1"


def crosswind_component_kt(
    *, wind_speed_kt: int, wind_direction_deg: int, runway_heading_deg: int
) -> float:
    """Crosswind component in knots.

    Implemented in Wave 0 because it is pure trigonometry with one correct answer, and
    because getting the units wrong here would quietly invalidate every risk score. A
    45 km/h reading mistaken for 45 kt is the classic version of that bug.
    """
    angle = math.radians(abs((wind_direction_deg - runway_heading_deg + 180) % 360 - 180))
    return abs(wind_speed_kt * math.sin(angle))


def headwind_component_kt(
    *, wind_speed_kt: int, wind_direction_deg: int, runway_heading_deg: int
) -> float:
    angle = math.radians(abs((wind_direction_deg - runway_heading_deg + 180) % 360 - 180))
    return wind_speed_kt * math.cos(angle)


# --------------------------------------------------------------------------- inputs


class RunwayOption(BaseModel):
    """One usable runway end. Both ends of a strip are separate options, because which one
    is in use is decided by the wind."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    designator: str
    heading_degrees_true: int
    #: `ourairports_true` or `designator_derived`. Carried into evidence so a score never
    #: implies a surveyed heading it does not have.
    heading_source: str = "ourairports_true"
    is_active: bool = True


class WeatherInput(BaseModel):
    """Normalised conditions. Knots, metres, feet — converted at the provider boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    airport_icao: str
    wind_speed_kt: int | None = None
    wind_direction_deg: int | None = None
    visibility_m: int | None = None
    ceiling_ft: int | None = None
    precipitation: str | None = None
    #: Minutes between the observation and the assessment. Explicit rather than computed
    #: from a wall clock, so the same input always scores the same.
    observation_age_minutes: int | None = None
    observed_at: str | None = None
    source_ref: str | None = None
    provenance_kind: str = ProvenanceKind.fixture.value
    is_stale: bool = False


# --------------------------------------------------------------------------- ruleset


class Band(BaseModel):
    """One threshold and the points it contributes.

    `at_or_below` for descending scales (visibility, ceiling) and `at_or_above` for ascending
    ones (wind, crosswind). Bands are evaluated in order and the first match wins.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    at_or_below: float | None = None
    at_or_above: float | None = None
    points: int

    def matches(self, value: float) -> bool:
        if self.at_or_below is not None:
            return value <= self.at_or_below
        if self.at_or_above is not None:
            return value >= self.at_or_above
        return False


class DelayRiskRuleset(BaseModel):
    """Every number the index depends on, in one versioned, hashable object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    visibility_m: tuple[Band, ...]
    ceiling_ft: tuple[Band, ...]
    wind_speed_kt: tuple[Band, ...]
    crosswind_kt: tuple[Band, ...]
    precipitation_points: dict[str, int]

    #: Low visibility and a low ceiling together reduce the arrival rate by more than either
    #: alone: spacing widens and a go-around costs a slot that cannot be recovered. Named as
    #: its own factor so a reviewer sees the interaction rather than finding it inside a sum.
    compounding_visibility_m: int
    compounding_ceiling_ft: int
    compounding_points: int

    #: Band ceilings for the level. `severe` is everything above `high_max`.
    low_max: int
    elevated_max: int
    high_max: int

    def hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def level_for(self, index: int) -> RiskLevel:
        if index <= self.low_max:
            return RiskLevel.low
        if index <= self.elevated_max:
            return RiskLevel.elevated
        if index <= self.high_max:
            return RiskLevel.high
        return RiskLevel.severe


DEFAULT_RULESET = DelayRiskRuleset(
    version=RULE_VERSION,
    # Visibility dominates because it is what triggers low-visibility procedures and cuts
    # the arrival rate. 800 m is flyable and still delays an airport badly.
    visibility_m=(
        Band(label="visibility_below_minima", at_or_below=550, points=34),
        Band(label="visibility_low_visibility_procedures", at_or_below=800, points=30),
        Band(label="visibility_marginal", at_or_below=1500, points=20),
        Band(label="visibility_reduced", at_or_below=3000, points=11),
        Band(label="visibility_slightly_reduced", at_or_below=5000, points=5),
    ),
    ceiling_ft=(
        Band(label="ceiling_at_minima", at_or_below=200, points=30),
        Band(label="ceiling_very_low", at_or_below=500, points=26),
        Band(label="ceiling_low", at_or_below=1000, points=22),
        Band(label="ceiling_marginal", at_or_below=1500, points=12),
        Band(label="ceiling_reduced", at_or_below=3000, points=5),
    ),
    wind_speed_kt=(
        Band(label="wind_very_strong", at_or_above=45, points=18),
        Band(label="wind_strong", at_or_above=35, points=14),
        Band(label="wind_fresh", at_or_above=25, points=10),
        Band(label="wind_moderate", at_or_above=18, points=7),
        Band(label="wind_light", at_or_above=12, points=3),
    ),
    # Against the runway in use, not raw wind. A 45 kt wind straight down the runway
    # contributes nothing here, which is the entire reason runway headings are loaded.
    crosswind_kt=(
        Band(label="crosswind_above_typical_limit", at_or_above=30, points=24),
        Band(label="crosswind_near_limit", at_or_above=25, points=19),
        Band(label="crosswind_high", at_or_above=20, points=14),
        Band(label="crosswind_moderate", at_or_above=15, points=9),
        Band(label="crosswind_noticeable", at_or_above=10, points=5),
    ),
    precipitation_points={
        "thunderstorm": 20,
        "hail": 20,
        "freezing_rain": 18,
        "snow": 16,
        "ice_pellets": 14,
        "showers": 12,
        "rain": 11,
        "unknown_precipitation": 6,
        "drizzle": 4,
    },
    compounding_visibility_m=1500,
    compounding_ceiling_ft=1000,
    compounding_points=10,
    low_max=24,
    elevated_max=49,
    high_max=74,
)

#: `business_constraint.service` value the ruleset is stored under.
BUSINESS_CONSTRAINT_SERVICE = "delay_risk_service"
BUSINESS_CONSTRAINT_KEY = "ruleset"


def ruleset_from_constraints(rows: list[dict[str, Any]] | None) -> DelayRiskRuleset:
    """Build the ruleset from `business_constraint` rows, falling back to the default.

    The fallback is the same object that gets seeded, so "no rows" and "seeded rows" score
    identically rather than diverging silently.
    """
    for row in rows or []:
        if (
            row.get("service") == BUSINESS_CONSTRAINT_SERVICE
            and row.get("constraint_key") == BUSINESS_CONSTRAINT_KEY
        ):
            return DelayRiskRuleset.model_validate(row["constraint_value"])
    return DEFAULT_RULESET


# --------------------------------------------------------------------------- output


class RiskFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    detail: str
    points: int
    observed_value: float | str | None = None


class RunwaySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    designator: str
    heading_degrees_true: int
    heading_source: str
    crosswind_kt: float
    headwind_kt: float


class DelayRiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airport_icao: str
    risk_index: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    factors: list[RiskFactor]
    runway: RunwaySelection | None = None
    missing_inputs: list[str] = Field(default_factory=list)
    rule_version: str
    ruleset_version: str
    ruleset_hash: str
    observation_age_minutes: int | None = None
    is_stale: bool = False
    #: True when the index reaches the configured event threshold. The orchestrator decides
    #: what to do about it; this service only reports.
    event_threshold: int | None = None
    event_recommended: bool = False

    @property
    def is_probability(self) -> bool:
        """Deliberately present and always False.

        The index is an ordered score from named bands. Nothing here is calibrated against
        observed outcomes, so it must never be rendered as a percentage.
        """
        return False


def select_runway(
    *,
    runways: list[RunwayOption],
    wind_speed_kt: int | None,
    wind_direction_deg: int | None,
) -> RunwaySelection | None:
    """The runway a controller would actually use: the active end with the most headwind.

    Ties break on designator so the choice is reproducible. Returns None when the wind has
    no direction (`VRB`) or no runway is usable, in which case crosswind is simply not scored
    rather than guessed.
    """
    active = [runway for runway in runways if runway.is_active]
    if not active or wind_speed_kt is None or wind_direction_deg is None:
        return None

    def key(runway: RunwayOption) -> tuple[float, str]:
        headwind = headwind_component_kt(
            wind_speed_kt=wind_speed_kt,
            wind_direction_deg=wind_direction_deg,
            runway_heading_deg=runway.heading_degrees_true,
        )
        # Negated designator ordering is not meaningful; sort ascending on it for stability.
        return (-headwind, runway.designator)

    chosen = sorted(active, key=key)[0]
    return RunwaySelection(
        designator=chosen.designator,
        heading_degrees_true=chosen.heading_degrees_true,
        heading_source=chosen.heading_source,
        crosswind_kt=round(
            crosswind_component_kt(
                wind_speed_kt=wind_speed_kt,
                wind_direction_deg=wind_direction_deg,
                runway_heading_deg=chosen.heading_degrees_true,
            ),
            1,
        ),
        headwind_kt=round(
            headwind_component_kt(
                wind_speed_kt=wind_speed_kt,
                wind_direction_deg=wind_direction_deg,
                runway_heading_deg=chosen.heading_degrees_true,
            ),
            1,
        ),
    )


def _band_factor(
    *,
    bands: tuple[Band, ...],
    value: float,
    detail: str,
) -> RiskFactor | None:
    for band in bands:
        if band.matches(value):
            return RiskFactor(
                name=band.label, detail=detail, points=band.points, observed_value=value
            )
    return None


def assess(
    *,
    weather: WeatherInput,
    runways: list[RunwayOption] | None = None,
    ruleset: DelayRiskRuleset = DEFAULT_RULESET,
    event_threshold: int | None = None,
) -> DelayRiskAssessment:
    """Score the conditions. Pure, deterministic, no clock, no I/O."""
    factors: list[RiskFactor] = []
    missing: list[str] = []

    if weather.visibility_m is None:
        missing.append("visibility_m")
    else:
        factor = _band_factor(
            bands=ruleset.visibility_m,
            value=weather.visibility_m,
            detail=f"Visibility {weather.visibility_m} m",
        )
        if factor:
            factors.append(factor)

    if weather.ceiling_ft is None:
        # No ceiling is a normal, meaningful observation: it means no broken or overcast
        # layer. It is not a missing input.
        pass
    else:
        factor = _band_factor(
            bands=ruleset.ceiling_ft,
            value=weather.ceiling_ft,
            detail=f"Ceiling {weather.ceiling_ft} ft",
        )
        if factor:
            factors.append(factor)

    if weather.wind_speed_kt is None:
        missing.append("wind_speed_kt")
    else:
        factor = _band_factor(
            bands=ruleset.wind_speed_kt,
            value=weather.wind_speed_kt,
            detail=f"Wind {weather.wind_speed_kt} kt",
        )
        if factor:
            factors.append(factor)

    selection = select_runway(
        runways=runways or [],
        wind_speed_kt=weather.wind_speed_kt,
        wind_direction_deg=weather.wind_direction_deg,
    )
    if selection is None:
        if weather.wind_direction_deg is None:
            missing.append("wind_direction_deg")
        if not runways:
            missing.append("runways")
    else:
        factor = _band_factor(
            bands=ruleset.crosswind_kt,
            value=selection.crosswind_kt,
            detail=(
                f"Crosswind {selection.crosswind_kt} kt on runway {selection.designator} "
                f"(heading {selection.heading_degrees_true}, {selection.heading_source})"
            ),
        )
        if factor:
            factors.append(factor)
        else:
            # Worth stating explicitly: it is the demo's clearest illustration of why raw
            # wind speed is not a usable rule.
            factors.append(
                RiskFactor(
                    name="crosswind_negligible",
                    detail=(
                        f"Crosswind {selection.crosswind_kt} kt on runway "
                        f"{selection.designator} (heading "
                        f"{selection.heading_degrees_true}, {selection.heading_source}): the "
                        f"wind is nearly aligned with the runway, so it contributes nothing "
                        f"despite {weather.wind_speed_kt} kt of wind"
                    ),
                    points=0,
                    observed_value=selection.crosswind_kt,
                )
            )

    if weather.precipitation:
        points = ruleset.precipitation_points.get(weather.precipitation, 0)
        factors.append(
            RiskFactor(
                name=f"precipitation_{weather.precipitation}",
                detail=f"Precipitation: {weather.precipitation}",
                points=points,
                observed_value=weather.precipitation,
            )
        )

    if (
        weather.visibility_m is not None
        and weather.ceiling_ft is not None
        and weather.visibility_m <= ruleset.compounding_visibility_m
        and weather.ceiling_ft <= ruleset.compounding_ceiling_ft
    ):
        factors.append(
            RiskFactor(
                name="low_visibility_with_low_ceiling",
                detail=(
                    f"Visibility {weather.visibility_m} m together with a "
                    f"{weather.ceiling_ft} ft ceiling: low-visibility procedures reduce the "
                    f"arrival rate by more than either condition alone"
                ),
                points=ruleset.compounding_points,
                observed_value=weather.visibility_m,
            )
        )

    index = min(100, sum(factor.points for factor in factors))
    level = ruleset.level_for(index)

    return DelayRiskAssessment(
        airport_icao=weather.airport_icao,
        risk_index=index,
        risk_level=level,
        factors=factors,
        runway=selection,
        missing_inputs=missing,
        rule_version=RULE_VERSION,
        ruleset_version=ruleset.version,
        ruleset_hash=ruleset.hash(),
        observation_age_minutes=weather.observation_age_minutes,
        is_stale=weather.is_stale,
        event_threshold=event_threshold,
        event_recommended=(event_threshold is not None and index >= event_threshold),
    )


# --------------------------------------------------------------------------- service


class DelayRiskService:
    name = "delay_risk"

    async def execute(self, **kwargs: Any) -> ServiceResult:
        """Score disruption risk.

        Inputs:
            weather:          WeatherInput (units already normalised at the boundary)
            runways:          list[RunwayOption] for the airport
            ruleset:          DelayRiskRuleset, normally loaded from business_constraint
            event_threshold:  int, from settings.delay_risk_event_threshold
        """
        weather = kwargs.get("weather")
        if weather is None:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    "Delay risk needs a weather observation. Without one there is no "
                    "assessment to make, and a default score would read as calm conditions."
                ),
                payload={"rule_version": RULE_VERSION, "missing_inputs": ["weather"]},
                provenance_kind=ProvenanceKind.unavailable.value,
            )

        if isinstance(weather, dict):
            weather = WeatherInput.model_validate(weather)

        runways_raw = kwargs.get("runways") or []
        runways = [
            RunwayOption.model_validate(runway) if isinstance(runway, dict) else runway
            for runway in runways_raw
        ]

        ruleset = kwargs.get("ruleset") or DEFAULT_RULESET
        if isinstance(ruleset, dict):
            ruleset = DelayRiskRuleset.model_validate(ruleset)

        threshold = kwargs.get("event_threshold")
        if threshold is None:
            from app.config import get_settings

            threshold = get_settings().delay_risk_event_threshold

        assessment = assess(
            weather=weather,
            runways=runways,
            ruleset=ruleset,
            event_threshold=int(threshold),
        )

        # Missing wind or visibility means the index rests on fewer factors than it should.
        # The gate decides what to do about that; this service reports it plainly and does
        # not quietly present a partial score as a complete one.
        blocking = {"wind_speed_kt", "visibility_m"} & set(assessment.missing_inputs)
        if blocking:
            return ServiceResult(
                status=ActionStatus.needs_human,
                reason=(
                    f"Delay risk for {weather.airport_icao} is incomplete: "
                    f"{', '.join(sorted(blocking))} missing from the observation"
                ),
                payload=assessment.model_dump(mode="json"),
                evidence_refs=self._evidence(weather, assessment),
                provenance_kind=weather.provenance_kind,
            )

        contributing = [factor.name for factor in assessment.factors if factor.points > 0]
        return ServiceResult(
            status=ActionStatus.success,
            reason=(
                f"{weather.airport_icao} delay risk {assessment.risk_index}/100 "
                f"({assessment.risk_level.value}) from "
                f"{len(contributing)} contributing factors"
            ),
            payload=assessment.model_dump(mode="json"),
            evidence_refs=self._evidence(weather, assessment),
            provenance_kind=weather.provenance_kind,
        )

    @staticmethod
    def _evidence(weather: WeatherInput, assessment: DelayRiskAssessment) -> list[str]:
        refs = [f"airport:{weather.airport_icao}"]
        if weather.source_ref:
            refs.append(f"observation:{weather.source_ref}")
        if assessment.runway:
            refs.append(f"runway:{weather.airport_icao}:{assessment.runway.designator}")
        refs.append(f"ruleset:{assessment.ruleset_version}:{assessment.ruleset_hash}")
        return refs
