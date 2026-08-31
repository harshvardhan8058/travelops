"""Pure contract checks for Scenario Builder input validation.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.enums import TriggerType
from app.schemas.scenarios import ScenarioCreateRequest


def _payload(**overrides):
    payload = {
        "root_cause": "weather",
        "airport_icao": "VOBL",
        "severity": "high",
        "effective_at": datetime(2026, 8, 20, 15, 40, tzinfo=UTC),
        "actor_id": "operator-1",
        "members": [{"flight_id": 1, "role": "primary", "delay_minutes": 420}],
    }
    payload.update(overrides)
    return payload


def test_valid_input_uses_existing_trigger_vocabulary_and_normalises_icao():
    request = ScenarioCreateRequest(**_payload(airport_icao="vobl"))

    assert request.root_cause is TriggerType.weather
    assert request.airport_icao == "VOBL"
    assert request.members[0].role == "primary"


def test_effective_time_must_be_timezone_aware():
    with pytest.raises(ValidationError, match="timezone offset"):
        ScenarioCreateRequest(**_payload(effective_at=datetime(2026, 8, 20, 15, 40)))


def test_each_flight_may_be_declared_once():
    with pytest.raises(ValidationError, match="each flight_id may appear only once"):
        ScenarioCreateRequest(
            **_payload(
                members=[
                    {"flight_id": 1, "role": "primary", "delay_minutes": 420},
                    {"flight_id": 1, "role": "affected_departure", "delay_minutes": 60},
                ]
            )
        )


@pytest.mark.parametrize(
    "members",
    [
        [{"flight_id": 1, "role": "affected_departure", "delay_minutes": 30}],
        [
            {"flight_id": 1, "role": "primary", "delay_minutes": 30},
            {"flight_id": 2, "role": "primary", "delay_minutes": 40},
        ],
    ],
)
def test_exactly_one_primary_is_required(members):
    with pytest.raises(ValidationError, match="exactly one primary"):
        ScenarioCreateRequest(**_payload(members=members))


def test_unknown_fields_and_smallint_overflow_are_rejected():
    with pytest.raises(ValidationError):
        ScenarioCreateRequest(**_payload(unexpected=True))
    with pytest.raises(ValidationError):
        ScenarioCreateRequest(
            **_payload(members=[{"flight_id": 1, "role": "primary", "delay_minutes": 32_768}])
        )
