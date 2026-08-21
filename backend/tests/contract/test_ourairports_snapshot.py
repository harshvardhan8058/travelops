"""The archived OurAirports snapshot is evidence, so it is checked like evidence.

Runs entirely offline against `data/snapshots/ourairports/<date>/`. CI and a demo laptop on
a dead venue network behave identically, which is the whole reason the subset is archived
rather than fetched.

The assertions that matter most are about runway headings. Crosswind is a function of wind
direction relative to runway orientation, so a missing or wrong heading produces a risk
index that is plausible and quietly wrong — and nothing downstream can detect it.
"""

from __future__ import annotations

import pytest
from data.loaders.ourairports import (
    AIRPORT_ICAOS,
    HEADING_DERIVED,
    HEADING_TRUE,
    load_airports,
    load_runways,
    read_manifest,
    sha256_bytes,
    snapshot_dir,
    verify_snapshot,
)

from app.services.delay_risk import crosswind_component_kt

EXPECTED_AIRPORTS = 10
EXPECTED_PHYSICAL_RUNWAYS = 19
EXPECTED_RUNWAY_ENDS = 38


# ------------------------------------------------------------------------ the archive


def test_snapshot_is_present_and_hashes_match():
    """An archive whose hash is not checked is a claim, not evidence."""
    verify_snapshot()


def test_manifest_records_the_upstream_revision():
    manifest = read_manifest()
    for entry in manifest["files"].values():
        assert len(entry["upstream_sha256"]) == 64
        assert entry["upstream_bytes"] > 0
        assert entry["url"].startswith("https://")


def test_manifest_records_licence_and_retrieval_time():
    manifest = read_manifest()
    assert manifest["licence"] == "public domain"
    assert manifest["attribution"]
    assert manifest["retrieved_at"].endswith("+00:00")


def test_licence_is_archived_beside_the_data():
    """Never load a dataset the repo cannot prove it is licensed to use."""
    licence = snapshot_dir() / "LICENCE.md"
    assert licence.is_file()
    text = licence.read_text(encoding="utf-8")
    assert "public domain" in text
    assert "ourairports" in text.lower()


def test_subset_hashes_are_reproducible_from_the_bytes():
    manifest = read_manifest()
    for entry in manifest["files"].values():
        payload = (snapshot_dir() / entry["subset_file"]).read_bytes()
        assert sha256_bytes(payload) == entry["subset_sha256"]


# ------------------------------------------------------------------------- airports


def test_all_ten_airports_are_present():
    airports = load_airports()
    assert len(airports) == EXPECTED_AIRPORTS
    assert {airport.icao_code for airport in airports} == set(AIRPORT_ICAOS)


def test_airports_are_keyed_on_icao_not_iata():
    """VOBL, not BLR. Mixing the two is a recurring source of silent lookup failures."""
    for airport in load_airports():
        assert len(airport.icao_code) == 4
        assert airport.icao_code.startswith(("VO", "VI", "VA", "VE"))


def test_every_airport_carries_iata_for_display():
    for airport in load_airports():
        assert airport.iata_code and len(airport.iata_code) == 3


def test_coordinates_are_inside_india():
    for airport in load_airports():
        assert 6 < airport.latitude < 37, airport.icao_code
        assert 68 < airport.longitude < 98, airport.icao_code


def test_airports_are_labelled_real_with_a_source_ref():
    for airport in load_airports():
        assert airport.provenance_kind == "real"
        assert airport.source_ref.startswith("ourairports:airports:")


def test_bengaluru_is_the_scenario_airport():
    airport = next(a for a in load_airports() if a.icao_code == "VOBL")
    assert airport.iata_code == "BLR"
    assert airport.city == "Bengaluru"
    assert airport.timezone == "Asia/Kolkata"


def test_loading_is_deterministic():
    assert load_airports() == load_airports()
    assert load_runways() == load_runways()


# -------------------------------------------------------------------------- runways


def test_both_ends_of_every_runway_are_loaded():
    """Crosswind depends on the direction in use, so a runway is two usable orientations."""
    manifest = read_manifest()
    assert manifest["files"]["runways"]["subset_rows"] == EXPECTED_PHYSICAL_RUNWAYS
    assert len(load_runways()) == EXPECTED_RUNWAY_ENDS


def test_every_runway_has_a_usable_true_heading():
    """The crosswind function is meaningless without this, and a heading of 0 as a default
    would be indistinguishable from a real northerly runway."""
    for runway in load_runways():
        assert 0 <= runway.heading_degrees_true <= 359, runway
        assert runway.heading_source in {HEADING_TRUE, HEADING_DERIVED}


def test_headings_agree_with_their_designator():
    """A designator encodes heading to the nearest ten degrees. More than 15 degrees of
    disagreement means the row was parsed wrong, not that the runway is unusual."""
    for runway in load_runways():
        digits = "".join(ch for ch in runway.designator if ch.isdigit())
        if not digits:
            continue
        expected = (int(digits) * 10) % 360
        delta = abs((runway.heading_degrees_true - expected + 180) % 360 - 180)
        assert delta <= 15, f"{runway.airport_icao} {runway.designator}: {runway}"


def test_the_known_upstream_gap_is_labelled_not_hidden():
    """OurAirports supplies no true heading for VOBL 09L/27R, and VOBL is the demo airport.
    Those two are derived from the designator and must say so."""
    derived = [r for r in load_runways() if r.heading_source == HEADING_DERIVED]
    assert {(r.airport_icao, r.designator) for r in derived} == {
        ("VOBL", "09L"),
        ("VOBL", "27R"),
    }


def test_most_headings_are_surveyed_not_derived():
    runways = load_runways()
    surveyed = [r for r in runways if r.heading_source == HEADING_TRUE]
    assert len(surveyed) == EXPECTED_RUNWAY_ENDS - 2


def test_every_airport_has_at_least_one_surveyed_runway_end():
    """A risk score for an airport whose every heading is derived would be weaker than it
    looks. None of the ten is in that position."""
    surveyed: dict[str, int] = {}
    for runway in load_runways():
        if runway.heading_source == HEADING_TRUE:
            surveyed[runway.airport_icao] = surveyed.get(runway.airport_icao, 0) + 1
    for icao in AIRPORT_ICAOS:
        assert surveyed.get(icao, 0) >= 1, icao


def test_bengaluru_has_both_parallel_runways():
    designators = {r.designator for r in load_runways() if r.airport_icao == "VOBL"}
    assert designators == {"09L", "09R", "27L", "27R"}


def test_runways_carry_length_and_active_state():
    for runway in load_runways():
        assert runway.length_ft is None or runway.length_ft > 0
        assert isinstance(runway.is_active, bool)


def test_no_closed_runway_is_marked_active():
    assert all(r.is_active for r in load_runways()), "snapshot has no closed runways today"


# ------------------------------------------------------------ what the headings are for


@pytest.mark.parametrize("designator", ["09L", "09R", "27L", "27R"])
def test_scenario_wind_produces_a_credible_crosswind_at_bengaluru(designator: str):
    """The storm fixture is 24 kt from 250 degrees. Against roughly east-west runways that
    is a modest crosswind and a strong headwind — which is why raw wind speed alone is not
    a usable rule.
    """
    runway = next(
        r for r in load_runways() if r.airport_icao == "VOBL" and r.designator == designator
    )
    crosswind = crosswind_component_kt(
        wind_speed_kt=24, wind_direction_deg=250, runway_heading_deg=runway.heading_degrees_true
    )
    assert 5.0 < crosswind < 12.0


def test_derived_and_surveyed_parallel_headings_barely_differ():
    """The derived heading for 09L is 90 against a surveyed 92 on the parallel 09R. If that
    gap were large, the derivation would not be safe to use."""
    runways = {r.designator: r for r in load_runways() if r.airport_icao == "VOBL"}
    assert abs(runways["09L"].heading_degrees_true - runways["09R"].heading_degrees_true) <= 5
