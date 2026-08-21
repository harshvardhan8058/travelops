"""Crosswind component.

Pure trigonometry with one correct answer. Tested in Wave 0 because a units or angle error
here would quietly invalidate every risk score downstream.
"""

from __future__ import annotations

import pytest

from app.services.delay_risk import crosswind_component_kt, headwind_component_kt


@pytest.mark.parametrize(
    ("wind_dir", "runway", "speed", "expected"),
    [
        # Straight down the runway: no crosswind.
        (90, 90, 30, 0.0),
        # Directly across: full crosswind.
        (180, 90, 30, 30.0),
        (0, 90, 30, 30.0),
        # 45 degrees off: speed / sqrt(2).
        (135, 90, 20, 14.142),
        # Direct tailwind is still zero crosswind.
        (270, 90, 25, 0.0),
    ],
)
def test_crosswind_component(wind_dir: int, runway: int, speed: int, expected: float):
    result = crosswind_component_kt(
        wind_speed_kt=speed, wind_direction_deg=wind_dir, runway_heading_deg=runway
    )
    assert result == pytest.approx(expected, abs=0.01)


def test_crosswind_is_never_negative():
    for wind_dir in range(0, 360, 15):
        value = crosswind_component_kt(
            wind_speed_kt=20, wind_direction_deg=wind_dir, runway_heading_deg=90
        )
        assert value >= 0.0


def test_wraparound_is_handled():
    """350 degrees against runway 010 is a 20-degree offset, not 340."""
    a = crosswind_component_kt(wind_speed_kt=30, wind_direction_deg=350, runway_heading_deg=10)
    b = crosswind_component_kt(wind_speed_kt=30, wind_direction_deg=30, runway_heading_deg=10)
    assert a == pytest.approx(b, abs=0.01)


def test_headwind_sign_distinguishes_tailwind():
    headwind = headwind_component_kt(wind_speed_kt=20, wind_direction_deg=90, runway_heading_deg=90)
    tailwind = headwind_component_kt(
        wind_speed_kt=20, wind_direction_deg=270, runway_heading_deg=90
    )
    assert headwind > 0 and tailwind < 0
