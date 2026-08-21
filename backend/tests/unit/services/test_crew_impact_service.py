"""CrewImpactService: the ServiceResult contract, the scope boundary, and determinism.

The cascade arithmetic itself is proved in `test_crew_cascade_counts.py`. This file covers
the service wrapper: what it returns, what it refuses to do, and what it never claims.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from data.generators.cascade_spec import BENGALURU_STORM

from app.models.enums import ActionStatus, PairingLegRole, PairingMechanism, ProvenanceKind
from app.services.crew_impact import (
    RULE_VERSION,
    CrewImpactService,
    RosterLeg,
    RosterPairing,
    ScheduledFlight,
    attribute_pairing_impacts,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def service() -> CrewImpactService:
    return CrewImpactService()


@pytest.fixture
def storm_kwargs() -> dict:
    return {
        "affected_flights": BENGALURU_STORM.affected_flights,
        "pairings": list(BENGALURU_STORM.pairings),
        "flights": BENGALURU_STORM.flights_by_id,
    }


# ------------------------------------------------------------------ the result contract


async def test_returns_success_with_the_nine(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    assert result.status is ActionStatus.success
    assert result.payload["pairings_at_risk"] == 9
    assert result.payload["rule_version"] == RULE_VERSION


async def test_reason_is_specific_enough_for_an_operator_under_pressure(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    assert result.reason == "9 crew rotations at risk across 8 affected flights"


async def test_evidence_refs_name_the_exact_entities(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    refs = result.evidence_refs

    # Every affected flight, every reported pairing, every compromised leg.
    assert sum(ref.startswith("flight:") for ref in refs) == 8
    assert sum(ref.startswith("pairing:") for ref in refs) == 9
    assert sum(ref.startswith("pairing_leg:") for ref in refs) == 9
    assert refs == sorted(set(refs))


async def test_provenance_is_synthetic_not_real(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    assert result.provenance_kind == ProvenanceKind.synthetic.value


async def test_mechanism_counts_are_reported(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    assert result.payload["mechanism_counts"] == {
        "operating": 6,
        "onward_duty": 1,
        "second_pairing": 1,
        "positioning": 1,
    }


async def test_identity_string_is_derived_not_asserted(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    assert "7 + 2 = 9" in result.payload["identity"]


async def test_costs_nothing(service, storm_kwargs):
    """Crew impact is an assessment. It books nothing and spends nothing."""
    result = await service.execute(**storm_kwargs)
    assert result.cost_inr is None


# ------------------------------------------------------------------------- determinism


async def test_identical_input_yields_identical_output(service, storm_kwargs):
    first = await service.execute(**storm_kwargs)
    second = await service.execute(**storm_kwargs)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


async def test_no_wall_clock_dependency(service, storm_kwargs):
    """Nothing in the payload may vary with when it ran."""
    result = await service.execute(**storm_kwargs)
    dumped = result.model_dump(mode="json")
    today = datetime.now(tz=IST).strftime("%Y-%m-%d")
    assert today not in str(dumped)


# --------------------------------------------------------------------- missing inputs


@pytest.mark.parametrize("missing", ["affected_flights", "pairings", "flights"])
async def test_missing_input_is_needs_human_not_an_empty_success(service, storm_kwargs, missing):
    """A service that returns "0 rotations affected" because it was handed nothing is the
    worst possible failure mode: it looks like good news."""
    kwargs = dict(storm_kwargs)
    kwargs.pop(missing)
    result = await service.execute(**kwargs)

    assert result.status is ActionStatus.needs_human
    assert missing in result.reason
    assert result.provenance_kind == ProvenanceKind.unavailable.value


async def test_no_affected_flights_is_needs_human(service, storm_kwargs):
    result = await service.execute(**{**storm_kwargs, "affected_flights": []})
    assert result.status is ActionStatus.needs_human


# ------------------------------------------------------------------- scope boundary


async def test_payload_states_the_scope_boundary(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    note = result.payload["scope_note"].lower()
    assert "duty-time legality is not validated" in note
    assert "no replacement roster" in note


def test_service_never_reads_duty_hours():
    """The strongest way to honour "no duty-time legality" is for the legality-adjacent
    column never to be touched by this module.

    Asserted over the AST rather than the text, so the module docstring may explain the
    boundary while any actual read of the column fails the build.
    """
    import ast
    from pathlib import Path

    import app.services.crew_impact as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

    touched = [
        node
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and "duty_hours" in node.attr)
        or (isinstance(node, ast.Name) and "duty_hours" in node.id)
    ]
    assert not touched, "crew impact must never read a duty-hours value"


async def test_no_replacement_roster_is_proposed(service, storm_kwargs):
    result = await service.execute(**storm_kwargs)
    forbidden = ("replacement", "reserve_crew", "standby", "roster_proposal")
    dumped = str(result.model_dump(mode="json")).lower()
    for term in forbidden:
        if term == "roster_proposal":
            assert term not in dumped
    assert "replacement roster" not in result.payload["identity"].lower()
    assert all(term not in str(result.payload["impacts"]).lower() for term in forbidden[:3])


# --------------------------------------------------- boundary conditions in isolation


def _flight(flight_id: int, dep: datetime, block: int, delay: int, origin: str, dest: str):
    return ScheduledFlight(
        flight_id=flight_id,
        flight_number=f"XX {flight_id}",
        origin_icao=origin,
        destination_icao=dest,
        scheduled_departure=dep,
        scheduled_arrival=dep + timedelta(minutes=block),
        delay_minutes=delay,
    )


def _two_leg_pairing(mct: int = 45) -> RosterPairing:
    return RosterPairing(
        pairing_id=99,
        reference="PAIR-T1",
        base_icao="VOBL",
        legs=(
            RosterLeg(
                leg_id=901,
                leg_order=1,
                flight_id=901,
                role=PairingLegRole.operating,
                min_connection_minutes=mct,
            ),
            RosterLeg(
                leg_id=902,
                leg_order=2,
                flight_id=902,
                role=PairingLegRole.operating,
                min_connection_minutes=mct,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("delay", "expected"),
    [
        # Turnaround is exactly the minimum connection: still feasible, delay absorbed.
        (0, PairingMechanism.operating),
        # One minute past the minimum connection: the onward duty breaks.
        (1, PairingMechanism.onward_duty),
    ],
)
def test_minimum_connection_is_the_boundary(delay: int, expected: PairingMechanism):
    """The threshold is the roster's own `min_connection_minutes`, read from the leg. There
    is no delay cutoff anywhere in the attribution."""
    inbound = _flight(901, datetime(2026, 8, 20, 18, 0, tzinfo=IST), 120, delay, "VOBL", "VIDP")
    onward = _flight(902, datetime(2026, 8, 20, 20, 45, tzinfo=IST), 120, 0, "VIDP", "VOBL")

    impacts = attribute_pairing_impacts(
        affected_flights=[inbound],
        pairings=[_two_leg_pairing()],
        flights={901: inbound, 902: onward},
    )
    assert len(impacts) == 1
    assert impacts[0].mechanism is expected


def test_a_pairing_with_no_leg_on_an_affected_flight_is_not_reported():
    unrelated = _flight(901, datetime(2026, 8, 20, 18, 0, tzinfo=IST), 120, 90, "VOBL", "VIDP")
    other = _flight(902, datetime(2026, 8, 20, 20, 45, tzinfo=IST), 120, 0, "VIDP", "VOBL")
    elsewhere = _flight(903, datetime(2026, 8, 20, 9, 0, tzinfo=IST), 90, 0, "VABB", "VOGO")

    pairing = RosterPairing(
        pairing_id=98,
        reference="PAIR-T0",
        base_icao="VABB",
        legs=(RosterLeg(leg_id=903, leg_order=1, flight_id=903, role=PairingLegRole.operating),),
    )
    impacts = attribute_pairing_impacts(
        affected_flights=[unrelated],
        pairings=[pairing],
        flights={901: unrelated, 902: other, 903: elsewhere},
    )
    assert impacts == []


def test_absorbed_positioning_is_recorded_but_not_at_risk():
    """Crew riding as passengers when nothing downstream breaks are not a disrupted
    rotation. Recording the row keeps it inspectable without inflating the count."""
    inbound = _flight(901, datetime(2026, 8, 20, 18, 0, tzinfo=IST), 120, 30, "VOBL", "VIDP")
    onward = _flight(902, datetime(2026, 8, 21, 9, 0, tzinfo=IST), 120, 0, "VIDP", "VOBL")

    pairing = RosterPairing(
        pairing_id=97,
        reference="PAIR-T2",
        base_icao="VOBL",
        legs=(
            RosterLeg(leg_id=911, leg_order=1, flight_id=901, role=PairingLegRole.positioning),
            RosterLeg(leg_id=912, leg_order=2, flight_id=902, role=PairingLegRole.operating),
        ),
    )
    impacts = attribute_pairing_impacts(
        affected_flights=[inbound], pairings=[pairing], flights={901: inbound, 902: onward}
    )
    assert len(impacts) == 1
    assert impacts[0].is_at_risk is False
    assert impacts[0].mechanism is PairingMechanism.positioning


def test_delay_is_absorbed_rather_than_propagating_forever():
    """If the crew make their next departure on time, later legs run to schedule. Carrying
    the delay onward regardless would inflate every count downstream."""
    inbound = _flight(901, datetime(2026, 8, 20, 18, 0, tzinfo=IST), 120, 20, "VOBL", "VIDP")
    second = _flight(902, datetime(2026, 8, 20, 21, 30, tzinfo=IST), 120, 0, "VIDP", "VOBL")
    third = _flight(903, datetime(2026, 8, 21, 8, 0, tzinfo=IST), 90, 0, "VOBL", "VOHS")
    fourth = _flight(904, datetime(2026, 8, 21, 10, 30, tzinfo=IST), 90, 0, "VOHS", "VOBL")

    pairing = RosterPairing(
        pairing_id=96,
        reference="PAIR-T3",
        base_icao="VOBL",
        legs=(
            RosterLeg(leg_id=921, leg_order=1, flight_id=901, role=PairingLegRole.operating),
            RosterLeg(leg_id=922, leg_order=2, flight_id=902, role=PairingLegRole.operating),
            RosterLeg(leg_id=923, leg_order=3, flight_id=903, role=PairingLegRole.operating),
            RosterLeg(leg_id=924, leg_order=4, flight_id=904, role=PairingLegRole.operating),
        ),
    )
    impacts = attribute_pairing_impacts(
        affected_flights=[inbound],
        pairings=[pairing],
        flights={901: inbound, 902: second, 903: third, 904: fourth},
    )
    assert impacts[0].mechanism is PairingMechanism.operating
    assert impacts[0].affected_leg_order == 1
