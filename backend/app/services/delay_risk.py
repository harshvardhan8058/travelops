"""Delay Risk service — STREAM D, first slice.

Returns a deterministic risk INDEX (0-100) and LEVEL, plus the named factors that produced
it and a rule version.

It is NOT a probability. Nothing here is calibrated against observed outcomes, so calling
it "87% chance of delay" would be an unearned claim. The UI shows the index, the band, and
the contributing factors.

Inputs to consider: wind speed, crosswind component against runway heading, visibility,
ceiling, precipitation. Thresholds come from config — never hardcode a number here.
"""

from __future__ import annotations

import math

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


class DelayRiskService:
    name = "delay_risk"

    async def execute(self, **kwargs: object) -> ServiceResult:
        """Score disruption risk.

        Stream D: build the rule set, read thresholds from config, and return
        payload={"risk_index": int, "risk_level": str, "factors": [...],
                 "rule_version": RULE_VERSION} with evidence_refs naming the exact
        observation and runway used.
        """
        raise NotImplementedError("Stream D: implement deterministic risk scoring")
