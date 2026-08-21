"""The archived OurAirports snapshot is evidence, so it is checked like evidence.

Runs entirely offline against `data/snapshots/ourairports/<date>/`. CI and a demo laptop on
a dead venue network behave identically, which is the whole reason the subset is archived
rather than fetched.

The assertions that matter most are about runway headings. Crosswind is a function of wind
direction relative to runway orientation, so a missing or wrong heading produces a risk
index that is plausible and quietly wrong — and nothing downstream can detect it.
"""

from __future__ import annotations

import hashlib

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


# ------------------------------------------------- byte-level integrity of the archive
#
# The archive is evidence, so these guard the bytes rather than the parsed contents. They
# exist because of a real failure: on Windows, Git's default `core.autocrlf=true` rewrote the
# subsets to CRLF on checkout, which added a byte per line and changed
# `airports.subset.csv` from affd0426... to 25ab9fd4.... `verify_snapshot()` correctly
# refused, `make seed` failed, and it failed on that platform only — so a Linux CI run could
# never have caught it.
#
# The manifest was right and was not changed. `data/.gitattributes` stops the rewrite.


def test_manifest_hashes_match_the_committed_bytes_exactly():
    """The regression test for the reported bug, stated in the most direct possible terms.

    Recompute the SHA-256 of each subset file and compare it to what the manifest declares.
    This is what `verify_snapshot()` does; asserting it separately means the expectation is
    visible in the test suite rather than only inside a helper.
    """
    manifest = read_manifest()
    for key, entry in manifest["files"].items():
        payload = (snapshot_dir() / entry["subset_file"]).read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        assert actual == entry["subset_sha256"], (
            f"{key}: manifest says {entry['subset_sha256']}, bytes on disk hash to {actual}. "
            "If the file holds CRLF, the checkout rewrote it — restore it rather than "
            "updating the manifest."
        )


def test_manifest_records_the_byte_count_it_hashed():
    """A length is a cheap second signal. A CRLF rewrite changes it by one byte per line, so a
    mismatch here localises the problem before anyone reaches for the hashes."""
    manifest = read_manifest()
    for entry in manifest["files"].values():
        payload = (snapshot_dir() / entry["subset_file"]).read_bytes()
        assert entry["subset_bytes"] == len(payload)


def test_the_subsets_contain_no_carriage_returns():
    """The check that actually fires on the affected platform.

    A hash comparison on Windows fails with a bare mismatch and no indication why. This says
    it outright, and on Linux it still asserts the invariant the hashes depend on.
    """
    for entry in read_manifest()["files"].values():
        payload = (snapshot_dir() / entry["subset_file"]).read_bytes()
        assert b"\r" not in payload, (
            f"{entry['subset_file']} contains CR bytes, so it is not the artefact the manifest "
            "describes. Git rewrote it on checkout; confirm data/.gitattributes is present."
        )


def test_the_manifest_states_the_line_ending_its_hash_assumes():
    """A hash over bytes is only meaningful once the byte form is declared."""
    integrity = read_manifest()["integrity"]
    assert integrity["subset_line_ending"] == "LF"
    assert integrity["subset_encoding"] == "utf-8"


def test_the_snapshot_is_protected_from_end_of_line_translation():
    """The fix itself, asserted.

    Without this attribute the archive is not reproducible across platforms, and the failure
    appears as a corrupted-evidence error on a demo machine rather than as a checkout setting.
    """
    from app.config import REPO_ROOT

    attributes = REPO_ROOT / "data" / ".gitattributes"
    assert attributes.is_file(), "data/.gitattributes is missing; snapshots are unprotected"

    rules = [
        line.strip()
        for line in attributes.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert any(rule.startswith("snapshots/") and "-text" in rule for rule in rules), (
        f"no `-text` rule covering snapshots/ in data/.gitattributes: {rules}"
    )


def test_a_crlf_rewrite_is_diagnosed_rather_than_left_as_a_bare_mismatch(tmp_path):
    """The check stays hard — it must still raise — but it must say what happened.

    Verified against a copy, so the real archive is never modified by a test.
    """
    import json
    import shutil

    from data.loaders import ourairports

    staged = tmp_path / "2026-08-21"
    shutil.copytree(snapshot_dir(), staged)

    manifest = json.loads((staged / "MANIFEST.json").read_text(encoding="utf-8"))
    for entry in manifest["files"].values():
        target = staged / entry["subset_file"]
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    original_root = ourairports.SNAPSHOT_ROOT
    ourairports.SNAPSHOT_ROOT = tmp_path
    try:
        with pytest.raises(RuntimeError) as exc:
            ourairports.verify_snapshot("2026-08-21")
    finally:
        ourairports.SNAPSHOT_ROOT = original_root

    message = str(exc.value)
    assert "hash mismatch" in message
    assert "CRLF" in message
    assert "Do not update the manifest to the CRLF hash" in message


def test_the_real_archive_still_verifies_after_that(tmp_path):
    """Guards the test above: it must not have mutated the committed snapshot."""
    verify_snapshot()
