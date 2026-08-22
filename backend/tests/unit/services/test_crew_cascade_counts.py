"""The nine crew pairings must be structurally derivable, not asserted.

This file was written BEFORE the generator that satisfies it, per
`data/generators/README.md`. The count of nine is the single most scrutinised number in the
demo, so every property that makes it defensible is a hard assertion here.

The structural identity being proved:

    8 affected flights
      -> 7 rotations carry them (PAIR-E1 spans two: it arrives on UK 705 and is
         rostered to operate UK 812)
      +  1 second rotation on an already-covered flight (cabin crew on 6E 2134)
      +  1 rotation reached only by positioning (deadheading on 6E 811)
      =  9

No delay threshold appears anywhere in the derivation. A threshold would be reverse
engineered from the answer, which is exactly the accusation the nine has to survive.

Scope boundary: nothing here asserts duty-time legality. Feasibility is turnaround
arithmetic against `pairing_leg.min_connection_minutes` and nothing more.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

import pytest
from data.generators.cascade_spec import BENGALURU_STORM

from app.models.enums import DIRECT_PAIRING_MECHANISMS, PairingLegRole, PairingMechanism
from app.services.crew_impact import attribute_pairing_impacts, expand_crew_cascade

EXPECTED_PAIRING_COUNT = 9
EXPECTED_AFFECTED_FLIGHTS = 8


@pytest.fixture
def impacts():
    return attribute_pairing_impacts(
        affected_flights=BENGALURU_STORM.affected_flights,
        pairings=BENGALURU_STORM.pairings,
        flights=BENGALURU_STORM.flights_by_id,
    )


@pytest.fixture
def at_risk(impacts):
    return [impact for impact in impacts if impact.is_at_risk]


# --------------------------------------------------------------------------- the count


def test_exactly_nine_pairings_are_at_risk(at_risk):
    assert len(at_risk) == EXPECTED_PAIRING_COUNT, [i.pairing_reference for i in at_risk]


def test_pairings_are_distinct(at_risk):
    references = [impact.pairing_reference for impact in at_risk]
    assert len(set(references)) == len(references)


def test_scenario_declares_eight_affected_flights():
    assert len(BENGALURU_STORM.affected_flights) == EXPECTED_AFFECTED_FLIGHTS


# --------------------------------------------------------------------- the mechanisms


def test_each_pairing_carries_exactly_one_mechanism(at_risk):
    for impact in at_risk:
        assert isinstance(impact.mechanism, PairingMechanism)


def test_all_four_direct_mechanisms_appear(at_risk):
    """Each direct mechanism is an edge label in the cascade graph. A missing one is a story
    the reviewer cannot read off the screen.

    Compared against DIRECT_PAIRING_MECHANISMS rather than the whole enum: `downstream_flight`
    is reachable only through bounded expansion at depth >= 2, and asserting it here would make
    the default, unexpanded call look incomplete."""
    assert {impact.mechanism for impact in at_risk} == set(DIRECT_PAIRING_MECHANISMS)


def test_the_default_call_never_labels_a_pairing_downstream(at_risk):
    """Guards the boundary from the other side. Without expansion explicitly requested, no
    depth-2 label may appear, and every impact sits at depth 1."""
    assert PairingMechanism.downstream_flight not in {i.mechanism for i in at_risk}
    assert {i.depth for i in at_risk} == {1}


def test_mechanism_distribution_is_the_documented_identity(at_risk):
    counts = Counter(impact.mechanism for impact in at_risk)
    assert counts == {
        PairingMechanism.operating: 6,
        PairingMechanism.onward_duty: 1,
        PairingMechanism.second_pairing: 1,
        PairingMechanism.positioning: 1,
    }


def test_seven_rotations_carry_the_flights_and_two_are_extras(at_risk):
    """7 + 2 = 9. This is the arithmetic a judge is invited to check."""
    carriers = [
        i
        for i in at_risk
        if i.mechanism in {PairingMechanism.operating, PairingMechanism.onward_duty}
    ]
    extras = [
        i
        for i in at_risk
        if i.mechanism in {PairingMechanism.second_pairing, PairingMechanism.positioning}
    ]
    assert len(carriers) == 7
    assert len(extras) == 2
    assert len(carriers) + len(extras) == EXPECTED_PAIRING_COUNT


# ------------------------------------------------------------------------- coverage


def test_every_affected_flight_is_represented(at_risk):
    """The failure mode this file exists to prevent.

    A cascade that reports crew for only five of eight delayed flights invites the
    question 'where is the crew for the other three?', and the only honest answer would be
    that the roster is incomplete.
    """
    covered: set[int] = set()
    for impact in at_risk:
        covered.update(impact.covered_flight_ids)

    expected = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}
    assert covered == expected, f"missing: {sorted(expected - covered)}"


def test_the_seven_carriers_alone_cover_all_eight_flights(at_risk):
    """The two extras are genuinely additional, not doing coverage work."""
    carriers = [
        i
        for i in at_risk
        if i.mechanism in {PairingMechanism.operating, PairingMechanism.onward_duty}
    ]
    covered: set[int] = set()
    for impact in carriers:
        covered.update(impact.covered_flight_ids)
    assert covered == {flight.flight_id for flight in BENGALURU_STORM.affected_flights}


def test_one_rotation_spans_two_affected_flights(at_risk):
    """This is the -1 that turns 8 primary rotations into 7."""
    spanning = [i for i in at_risk if len(i.covered_flight_ids) > 1]
    assert len(spanning) == 1
    assert spanning[0].pairing_reference == "PAIR-E1"
    assert spanning[0].mechanism is PairingMechanism.onward_duty


# ------------------------------------------------------------------- referential truth


def test_no_unrelated_pairing_is_included(impacts):
    """Every reported pairing must actually hold a leg on an affected flight."""
    affected_ids = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}
    pairings = {p.reference: p for p in BENGALURU_STORM.pairings}

    for impact in impacts:
        legs = pairings[impact.pairing_reference].legs
        assert any(leg.flight_id in affected_ids for leg in legs)


def test_affected_leg_belongs_to_its_pairing(at_risk):
    pairings = {p.reference: p for p in BENGALURU_STORM.pairings}

    for impact in at_risk:
        pairing = pairings[impact.pairing_reference]
        leg_ids = {leg.leg_id for leg in pairing.legs}
        assert impact.affected_leg_id in leg_ids
        assert impact.pairing_leg_count == len(pairing.legs)
        assert 1 <= impact.affected_leg_order <= impact.pairing_leg_count


def test_source_flight_is_an_affected_flight(at_risk):
    affected_ids = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}
    for impact in at_risk:
        assert impact.source_flight_id in affected_ids


def test_background_roster_never_touches_the_affected_flights():
    """Only the nine deliberate pairings may hold a leg on the storm set, otherwise the
    count stops being controlled by the scenario."""
    affected_ids = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}
    touching = {
        pairing.reference
        for pairing in BENGALURU_STORM.pairings
        if any(leg.flight_id in affected_ids for leg in pairing.legs)
    }
    assert len(touching) == EXPECTED_PAIRING_COUNT


def test_every_pairing_starts_and_ends_at_its_base():
    """A pairing that does not return to base is not a pairing, and a reviewer who checks
    the roster will notice before we do."""
    for pairing in BENGALURU_STORM.pairings:
        legs = sorted(pairing.legs, key=lambda leg: leg.leg_order)
        first = BENGALURU_STORM.flights_by_id[legs[0].flight_id]
        last = BENGALURU_STORM.flights_by_id[legs[-1].flight_id]
        assert first.origin_icao == pairing.base_icao, pairing.reference
        assert last.destination_icao == pairing.base_icao, pairing.reference


def test_pairing_legs_are_geographically_continuous():
    for pairing in BENGALURU_STORM.pairings:
        legs = sorted(pairing.legs, key=lambda leg: leg.leg_order)
        for previous, current in pairwise(legs):
            arrives = BENGALURU_STORM.flights_by_id[previous.flight_id].destination_icao
            departs = BENGALURU_STORM.flights_by_id[current.flight_id].origin_icao
            assert arrives == departs, f"{pairing.reference} leg {current.leg_order}"


def test_pairing_legs_are_chronological_as_scheduled():
    for pairing in BENGALURU_STORM.pairings:
        legs = sorted(pairing.legs, key=lambda leg: leg.leg_order)
        for previous, current in pairwise(legs):
            earlier = BENGALURU_STORM.flights_by_id[previous.flight_id]
            later = BENGALURU_STORM.flights_by_id[current.flight_id]
            assert earlier.scheduled_arrival <= later.scheduled_departure


def test_every_pairing_as_scheduled_is_feasible_before_the_disruption():
    """The disruption must be what breaks the roster. If a pairing were already infeasible
    as published, the cascade would be an artefact of a bad schedule."""
    for pairing in BENGALURU_STORM.pairings:
        legs = sorted(pairing.legs, key=lambda leg: leg.leg_order)
        for previous, current in pairwise(legs):
            earlier = BENGALURU_STORM.flights_by_id[previous.flight_id]
            later = BENGALURU_STORM.flights_by_id[current.flight_id]
            gap = later.scheduled_departure - earlier.scheduled_arrival
            turnaround = gap.total_seconds() / 60
            assert turnaround >= previous.min_connection_minutes, (
                f"{pairing.reference} leg {current.leg_order} is infeasible as scheduled"
            )


def test_exactly_one_positioning_leg_sits_on_an_affected_flight():
    affected_ids = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}
    positioning = [
        (pairing.reference, leg.leg_order)
        for pairing in BENGALURU_STORM.pairings
        for leg in pairing.legs
        if leg.role is PairingLegRole.positioning and leg.flight_id in affected_ids
    ]
    assert positioning == [("PAIR-B2", 2)]


# -------------------------------------------------------------------------- determinism


def test_attribution_is_reproducible(impacts):
    again = attribute_pairing_impacts(
        affected_flights=BENGALURU_STORM.affected_flights,
        pairings=BENGALURU_STORM.pairings,
        flights=BENGALURU_STORM.flights_by_id,
    )
    assert [i.model_dump(mode="json") for i in impacts] == [
        i.model_dump(mode="json") for i in again
    ]


def test_attribution_is_independent_of_input_ordering():
    """Identical input yields identical output, including when the caller hands us rows in
    a different order — which a database will."""
    baseline = attribute_pairing_impacts(
        affected_flights=BENGALURU_STORM.affected_flights,
        pairings=BENGALURU_STORM.pairings,
        flights=BENGALURU_STORM.flights_by_id,
    )
    shuffled = attribute_pairing_impacts(
        affected_flights=list(reversed(BENGALURU_STORM.affected_flights)),
        pairings=list(reversed(BENGALURU_STORM.pairings)),
        flights=BENGALURU_STORM.flights_by_id,
    )
    assert [i.model_dump(mode="json") for i in baseline] == [
        i.model_dump(mode="json") for i in shuffled
    ]


def test_details_are_populated_and_factual(at_risk):
    """`detail` is rendered as the edge tooltip. It must name the flights and times it
    rests on rather than restate the conclusion."""
    for impact in at_risk:
        assert impact.detail
        assert impact.source_flight_number in impact.detail


# ----------------------------------------------------------------- declared sub-targets


def test_at_risk_connection_breakdown_sums_to_the_target():
    """22 is a sum of per-flight counts, not a headline."""
    assert sum(BENGALURU_STORM.at_risk_connections_by_flight.values()) == 22


def test_connection_breakdown_only_references_affected_flights():
    affected_ids = {flight.flight_id for flight in BENGALURU_STORM.affected_flights}
    assert set(BENGALURU_STORM.at_risk_connections_by_flight) <= affected_ids


def test_passenger_counts_sum_to_the_fixture_target():
    assert sum(flight.passengers for flight in BENGALURU_STORM.affected_flights) == 604


# ------------------------------------------------------- bounded expansion (Phase 2, C2-6)
#
# The expansion exists to answer "and then what?" without putting the nine at risk. These
# tests are written so that any change making expansion move the headline count fails loudly.


def _cascade(depth: int):
    return expand_crew_cascade(
        affected_flights=BENGALURU_STORM.affected_flights,
        pairings=BENGALURU_STORM.pairings,
        flights=BENGALURU_STORM.flights_by_id,
        max_expansion_depth=depth,
    )


def test_depth_one_is_exactly_the_phase_one_answer(at_risk):
    """The default is direct-only, byte for byte identical to `attribute_pairing_impacts`."""
    cascade = _cascade(1)
    assert cascade.downstream == []
    assert cascade.max_depth_reached == 1
    assert cascade.expansion_truncated is False
    assert cascade.newly_at_risk_flight_ids == []
    assert [i.model_dump() for i in cascade.direct_at_risk] == [i.model_dump() for i in at_risk]


@pytest.mark.parametrize("depth", [1, 2, 3, 5])
def test_the_direct_count_is_nine_at_every_expansion_depth(depth):
    """The load-bearing assertion of this whole feature.

    Nine is the number a reviewer is invited to verify by hand. Expansion may add rows to
    `downstream`; if it can also change `direct`, the figure on screen depends on a config
    value nobody in the room can see, and the demo's most checkable claim becomes unfalsifiable.
    """
    cascade = _cascade(depth)
    assert len(cascade.direct_at_risk) == EXPECTED_PAIRING_COUNT
    assert {i.depth for i in cascade.direct} == {1}


def test_expansion_never_relabels_a_direct_rotation():
    """`downstream_flight` is reachable only at depth >= 2, and only for rotations that were
    not already attributed. A pairing counted directly must never reappear downstream."""
    cascade = _cascade(3)
    direct_ids = {i.pairing_id for i in cascade.direct}
    downstream_ids = {i.pairing_id for i in cascade.downstream}
    assert direct_ids.isdisjoint(downstream_ids)
    assert all(i.mechanism is PairingMechanism.downstream_flight for i in cascade.downstream)
    assert all(i.depth >= 2 for i in cascade.downstream)
    assert PairingMechanism.downstream_flight not in {i.mechanism for i in cascade.direct}


def test_expansion_terminates_and_reports_truncation_rather_than_hiding_it():
    """A bounded walk that quietly stops is an incomplete answer presented as a complete one.

    Whatever the bound, the walk must terminate, and `expansion_truncated` must be the honest
    statement of whether a frontier was still open when it did.
    """
    for depth in (2, 3, 4, 8):
        cascade = _cascade(depth)
        assert cascade.max_depth_reached <= depth
        assert isinstance(cascade.expansion_truncated, bool)
        # A cascade that exhausted its frontier before the bound cannot claim truncation.
        if cascade.max_depth_reached < depth:
            assert cascade.expansion_truncated is False


def test_expansion_is_deterministic():
    """Same roster, same answer — including ordering, since the payload is hashed downstream."""
    first, second = _cascade(3), _cascade(3)
    assert [i.model_dump() for i in first.downstream] == [i.model_dump() for i in second.downstream]


def test_downstream_flights_were_not_themselves_disrupted():
    """The point of the mechanism: these flights lost crew, they were never delayed.

    If a newly-at-risk flight were already in the affected set, the row would be double
    counting a direct impact under a second label.
    """
    cascade = _cascade(3)
    affected = {f.flight_id for f in BENGALURU_STORM.affected_flights}
    assert set(cascade.newly_at_risk_flight_ids).isdisjoint(affected)
    for impact in cascade.downstream:
        assert impact.source_flight_id not in affected
