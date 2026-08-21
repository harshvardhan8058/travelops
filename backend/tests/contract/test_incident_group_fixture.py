"""The committed cascade fixture and the generator must not be allowed to drift.

`fixtures/api/incident_group_detail.json` is contractual. Stream A's real endpoint has to
stay byte-compatible with it and Stream D renders it directly, so it cannot be a
hand-maintained second version of the cascade — it is rendered from the same spec and the
same attribution logic the dataset generator uses.

This file enforces both halves of that:

1. The committed bytes equal a fresh render. If someone edits the JSON by hand, this fails.
2. The response SHAPE is locked independently of the content, so regenerating the cascade
   can never quietly break the two streams that consume it.

The key sets below were taken from the pre-change fixture and from Stream D's
`frontend/src/api/types.ts`. They are the contract; the values are not.
"""

from __future__ import annotations

import json

from data.generators.build_incident_group_fixture import (
    FIXTURE_PATH,
    build_payload,
    render,
)

from app.config import REPO_ROOT
from app.models.enums import PairingMechanism

TOP_LEVEL_KEYS = {
    "generated_by",
    "note",
    "id",
    "reference",
    "root_cause",
    "airport_icao",
    "severity",
    "state",
    "opened_at",
    "rollups",
    "flights",
    "crew_pairings",
    "mechanism_legend",
    "why_nine_not_eight",
    "provenance",
}

ROLLUP_KEYS = {
    "flights_affected",
    "passengers_affected",
    "connections_at_risk",
    "candidate_hotels",
    "crew_pairings_affected",
    "note",
}

FLIGHT_KEYS = {"id", "flight_number", "route", "delay_minutes", "passengers", "state"}

#: Exactly Stream D's CrewPairingImpact interface.
PAIRING_KEYS = {
    "pairing_reference",
    "base_icao",
    "source_flight",
    "affected_leg",
    "mechanism",
    "detail",
    "at_risk",
}

EXPECTED_ROLLUPS = {
    "flights_affected": 8,
    "passengers_affected": 604,
    "connections_at_risk": 22,
    "candidate_hotels": 11,
    "crew_pairings_affected": 9,
}


def _committed() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- generator agreement


def test_committed_fixture_is_byte_identical_to_a_fresh_render():
    """One source of truth. Regenerate with:

    cd backend && uv run python -m data.generators.build_incident_group_fixture
    """
    assert FIXTURE_PATH.read_text(encoding="utf-8") == render()


def test_fixture_lives_where_both_containers_resolve_it():
    assert FIXTURE_PATH == REPO_ROOT / "fixtures" / "api" / "incident_group_detail.json"


# -------------------------------------------------------------------------- shape lock


def test_top_level_shape_is_unchanged():
    assert set(_committed()) == TOP_LEVEL_KEYS


def test_rollup_shape_is_unchanged():
    assert set(_committed()["rollups"]) == ROLLUP_KEYS


def test_flight_row_shape_is_unchanged():
    for flight in _committed()["flights"]:
        assert set(flight) == FLIGHT_KEYS


def test_pairing_row_shape_matches_the_frontend_interface():
    for pairing in _committed()["crew_pairings"]:
        assert set(pairing) == PAIRING_KEYS


def test_mechanism_legend_covers_exactly_the_enum():
    legend = _committed()["mechanism_legend"]
    assert set(legend) == {mechanism.value for mechanism in PairingMechanism}
    assert all(text for text in legend.values())


def test_every_mechanism_is_a_known_value():
    known = {mechanism.value for mechanism in PairingMechanism}
    for pairing in _committed()["crew_pairings"]:
        assert pairing["mechanism"] in known


def test_provenance_is_declared_as_fixture():
    provenance = _committed()["provenance"]
    assert set(provenance) == {"kind", "provider", "source_ref"}
    assert provenance["kind"] == "fixture"


# ------------------------------------------------------------------------ derived counts


def test_rollups_hold_the_scenario_targets():
    rollups = dict(_committed()["rollups"])
    rollups.pop("note")
    assert rollups == EXPECTED_ROLLUPS


def test_rollups_are_derived_from_the_arrays_not_asserted():
    body = _committed()
    assert body["rollups"]["flights_affected"] == len(body["flights"])
    assert body["rollups"]["crew_pairings_affected"] == len(body["crew_pairings"])
    assert body["rollups"]["passengers_affected"] == sum(
        flight["passengers"] for flight in body["flights"]
    )


def test_nine_pairings_and_eight_flights():
    body = _committed()
    assert len(body["crew_pairings"]) == 9
    assert len(body["flights"]) == 8


def test_every_affected_flight_appears_in_the_cascade():
    """A flight board showing eight delays next to crew for five of them is the
    inconsistency this whole rework exists to remove."""
    body = _committed()
    flight_numbers = {flight["flight_number"] for flight in body["flights"]}

    # Each pairing names its source flight; the mechanism detail names the leg that breaks.
    referenced = {pairing["source_flight"] for pairing in body["crew_pairings"]}
    for pairing in body["crew_pairings"]:
        for number in flight_numbers:
            if number in pairing["detail"]:
                referenced.add(number)

    assert flight_numbers <= referenced, f"unrepresented: {sorted(flight_numbers - referenced)}"


def test_the_inbound_flight_is_modelled_as_an_arrival():
    """UK 705 is AMD -> BLR on purpose. It is what lets one rotation span two affected
    flights, which is the -1 in `7 + 2 = 9`."""
    body = _committed()
    inbound = next(f for f in body["flights"] if f["flight_number"] == "UK 705")
    assert inbound["route"] == "AMD \u2192 BLR"


def test_mechanism_distribution_is_the_documented_identity():
    body = _committed()
    counts: dict[str, int] = {}
    for pairing in body["crew_pairings"]:
        counts[pairing["mechanism"]] = counts.get(pairing["mechanism"], 0) + 1
    assert counts == {
        "operating": 6,
        "onward_duty": 1,
        "second_pairing": 1,
        "positioning": 1,
    }


def test_all_four_mechanisms_appear_as_edge_labels():
    body = _committed()
    present = {pairing["mechanism"] for pairing in body["crew_pairings"]}
    assert present == {mechanism.value for mechanism in PairingMechanism}


# --------------------------------------------------------------------------- honesty


def test_why_nine_not_eight_shows_the_arithmetic():
    """It must explain the count, not restate it."""
    text = _committed()["why_nine_not_eight"]
    assert "7 + 2 = 9" in text
    assert "PAIR-E1" in text
    assert "duty-time legality is not validated" in text


def test_no_pairing_detail_claims_a_legality_decision():
    banned = ("legal", "illegal", "compliance", "duty limit", "ftl", "regulation")
    for pairing in _committed()["crew_pairings"]:
        lowered = pairing["detail"].lower()
        for phrase in banned:
            assert phrase not in lowered, f"{pairing['pairing_reference']}: {phrase}"


def test_payload_builder_is_deterministic():
    assert build_payload() == build_payload()
