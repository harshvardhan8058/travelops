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
from app.models.enums import DIRECT_PAIRING_MECHANISMS, PairingMechanism

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
    # Phase 2, additive. Kept in the same frozen-shape test as everything else so a future
    # addition is a deliberate edit here rather than a drift the console discovers at runtime.
    "rollup_status",
    "graph",
    "blast_radius",
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


def test_mechanism_legend_covers_exactly_the_direct_enum():
    """The legend documents what the projected cascade can actually show. `downstream_flight`
    is deliberately absent: the fixture is the unexpanded direct set."""
    legend = _committed()["mechanism_legend"]
    assert set(legend) == {mechanism.value for mechanism in DIRECT_PAIRING_MECHANISMS}
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
    # ASCII arrow: 'Inter' and 'JetBrains Mono' are webfonts, and on a machine without them the
    # fallback renders U+2192 as a tofu box. A box where an arrow should be reads as a rendering
    # fault and undermines every figure beside it, so display strings stay ASCII.
    assert inbound["route"] == "AMD -> BLR"


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


def test_all_four_direct_mechanisms_appear_as_edge_labels():
    body = _committed()
    present = {pairing["mechanism"] for pairing in body["crew_pairings"]}
    assert present == {mechanism.value for mechanism in DIRECT_PAIRING_MECHANISMS}


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


# ------------------------------------------------------ Phase 2: graph and blast radius
#
# The Phase 2 keys are additive. These tests assert both halves of that: the new blocks are
# present and correct, AND every key the console already read is still there. A fixture change
# that silently dropped a key Stream D depends on would break the console with no failing test
# to point at it.


def test_the_phase_one_keys_all_survive():
    """Additive means additive. This is the guard against a helpful refactor."""
    body = _committed()
    for key in (
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
    ):
        assert key in body, key


def test_the_rollups_are_unchanged_by_the_phase_two_additions():
    """The five headline numbers are the demo. Adding a graph must not move any of them."""
    rollups = _committed()["rollups"]
    assert rollups["flights_affected"] == 8
    assert rollups["passengers_affected"] == 604
    assert rollups["connections_at_risk"] == 22
    assert rollups["candidate_hotels"] == 11
    assert rollups["crew_pairings_affected"] == 9


def test_the_graph_has_one_node_per_declared_flight_and_per_rotation():
    body = _committed()
    graph = body["graph"]
    flight_nodes = [n for n in graph["nodes"] if n["kind"] == "flight"]
    pairing_nodes = [n for n in graph["nodes"] if n["kind"] == "pairing"]
    event_nodes = [n for n in graph["nodes"] if n["kind"] == "event"]

    assert len(event_nodes) == 1
    assert len(flight_nodes) == len(body["flights"]) == 8
    assert len(pairing_nodes) == len(body["crew_pairings"]) == 9


def test_every_graph_node_ref_is_kind_colon_id():
    """The same addressing as `evidence_refs`, because there is no node table and a node is the
    row it names. A bare integer id would make the two vocabularies diverge."""
    for node in _committed()["graph"]["nodes"]:
        kind, _, identifier = node["ref"].partition(":")
        assert kind == node["kind"]
        assert identifier


def test_every_graph_edge_connects_two_declared_nodes():
    """A dangling edge is the classic graph-rendering bug and produces a floating box on
    screen with no explanation attached to it."""
    graph = _committed()["graph"]
    refs = {node["ref"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source_ref"] in refs, edge
        assert edge["target_ref"] in refs, edge


def test_one_root_cause_edge_per_flight_and_one_crew_edge_per_rotation():
    graph = _committed()["graph"]
    assert graph["edge_counts_by_kind"] == {"root_cause": 8, "crew": 9}
    assert sum(graph["edge_counts_by_kind"].values()) == len(graph["edges"])


def test_every_crew_edge_carries_a_mechanism_and_root_cause_edges_do_not():
    """The mechanism is the edge label. A crew edge without one is an arrow with no reason."""
    for edge in _committed()["graph"]["edges"]:
        if edge["edge_kind"] == "crew":
            assert edge["mechanism"]
            assert edge["detail"]
        if edge["edge_kind"] == "root_cause":
            assert edge["mechanism"] is None


def test_exactly_one_flight_node_is_primary_and_exactly_one_is_an_arrival():
    """The membership decision, visible in the fixture. UK 705 arrives into VOBL; a
    departure-origin query would have produced seven departures and no arrival at all."""
    flight_nodes = [n for n in _committed()["graph"]["nodes"] if n["kind"] == "flight"]
    roles = [node["role"] for node in flight_nodes]
    assert roles.count("primary") == 1
    assert roles.count("affected_arrival") == 1
    assert roles.count("affected_departure") == 6


def test_the_fixture_marks_its_edge_provenance_as_a_fixture():
    """A fixture edge must not be mistakable for recorded evidence. The live projection carries
    a real `action:` or `prediction:` reference; this says plainly that it does not."""
    graph = _committed()["graph"]
    assert all(edge["derived_from"] == "fixture" for edge in graph["edges"])
    assert "not a row id" in graph["note"]


def test_blast_radius_repeats_the_rollups_and_calculates_nothing():
    """Composition only. Every dimension must be findable in `rollups`."""
    body = _committed()
    rollups = body["rollups"]
    values = {d["key"]: d["value"] for d in body["blast_radius"]["dimensions"]}
    assert values == {
        "flights": rollups["flights_affected"],
        "passengers": rollups["passengers_affected"],
        "connections": rollups["connections_at_risk"],
        "crew_pairings": rollups["crew_pairings_affected"],
        "candidate_hotels": rollups["candidate_hotels"],
    }


def test_blast_radius_states_completeness_and_never_confidence():
    """Completeness is countable; confidence would be a probability nothing here is calibrated
    to produce. One uncheckable figure discredits the checkable ones beside it."""
    radius = _committed()["blast_radius"]
    assert radius["basis"] == "composed_from_recorded_findings"
    assert radius["completeness"]["is_complete"] is True
    assert radius["completeness"]["ratio"] == "8/8"
    # Checked over keys rather than the whole document: the prose is allowed to explain *why*
    # there is no confidence value, and a substring match over it would forbid saying so.
    for dimension in radius["dimensions"]:
        assert "confidence" not in dimension
        assert "probability" not in dimension
    assert "confidence" not in radius["completeness"]


def test_every_blast_radius_dimension_names_what_measured_it():
    for dimension in _committed()["blast_radius"]["dimensions"]:
        assert dimension["measured_by"]
        assert dimension["unit"]
        assert dimension["note"]


def test_the_group_list_rollups_stay_typeable_as_number_or_string():
    """Stream D types `rollups` as `Record<string, number | string>`.

    Phase 2 needed to say whether a rollup is complete, and a boolean inside `rollups` would
    have broken that type for a cosmetic gain. `rollup_status` is a sibling instead, which also
    reads better: completeness is a property of the computation, not one of the figures.
    """
    groups = json.loads(
        (REPO_ROOT / "fixtures" / "api" / "incident_groups.json").read_text(encoding="utf-8")
    )["groups"]
    assert groups
    for group in groups:
        for key, value in group["rollups"].items():
            assert isinstance(value, (int, str)) and not isinstance(value, bool), (key, value)

        status = group["rollup_status"]
        assert isinstance(status["is_complete"], bool)
        assert status["computed_at"]
        assert "render as partial" in status["note"]


def test_the_fixture_declares_every_field_the_real_endpoint_returns():
    """The fixture and `GET /incident-groups/{id}` must be the same contract.

    The console can run against either — fixtures mode is how the UI is built and demoed without a
    database. If the two shapes drift, flipping `VITE_USE_FIXTURES` changes the contract silently,
    and the failure shows up as a blank panel in whichever mode nobody was looking at.

    Compared as a set of field names rather than by validating the fixture against the response
    model: the fixture legitimately carries `generated_by` and `note`, which the API does not, and a
    strict validation would force those out of the file for no benefit.
    """
    from app.schemas.groups import GroupDetailResponse

    declared = set(GroupDetailResponse.model_fields)
    present = set(_committed())
    missing = declared - present
    assert not missing, f"fixture is missing fields the API returns: {sorted(missing)}"


def test_the_fixture_rollup_status_matches_the_api_shape():
    from app.schemas.groups import RollupStatus

    assert set(_committed()["rollup_status"]) == set(RollupStatus.model_fields)


def test_the_fixture_graph_matches_the_api_shape():
    """Node and edge field names, so a renderer written against one works against the other.

    Edges are the exception that proves the rule: the API flattens two nullable provenance columns
    into one `derived_from` string, and the fixture does the same, because a renderer only needs to
    link to the evidence. The database keeps both columns and the CHECK that exactly one is set.
    """
    from app.schemas.groups import CascadeGraphOut, GraphEdgeOut, GraphNodeOut

    graph = _committed()["graph"]
    assert set(CascadeGraphOut.model_fields) - set(graph) <= {"snapshot_hash"}
    for node in graph["nodes"]:
        assert set(node) == set(GraphNodeOut.model_fields)
    for edge in graph["edges"]:
        assert set(GraphEdgeOut.model_fields) - set(edge) == set()


def test_the_fixture_blast_radius_matches_the_api_shape():
    from app.schemas.groups import BlastRadiusDimensionOut, BlastRadiusOut

    radius = _committed()["blast_radius"]
    assert set(BlastRadiusOut.model_fields) - set(radius) <= {"group_reference"}
    for dimension in radius["dimensions"]:
        assert set(dimension) == set(BlastRadiusDimensionOut.model_fields)
