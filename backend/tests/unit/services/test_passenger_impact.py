"""Passenger priority: the ranking that decides who gets a room when there are not enough.

Every test here guards against the same failure — a number that looks authoritative but has
nothing behind it, or a ranking that came from an engineering opinion rather than declared
policy.
"""

from __future__ import annotations

import pytest

from app.models.enums import PriorityBand
from app.services.passenger_impact import (
    BUSINESS_CONSTRAINT_KEY,
    BUSINESS_CONSTRAINT_SERVICE,
    DEFAULT_RULESET,
    PassengerCohortFacts,
    PassengerImpactService,
    assess_passenger_impact,
    load_ruleset,
    ruleset_hash,
    score_passenger,
)


def facts(**overrides) -> PassengerCohortFacts:
    base = {
        "passenger_id": 1,
        "passenger_reference": "PAX-00001",
        "booking_id": 1,
        "pnr": "AAA111",
    }
    return PassengerCohortFacts(**{**base, **overrides})


# ------------------------------------------------------------------------------ scoring


def test_a_passenger_with_no_recorded_exposure_scores_zero():
    """The base case must be zero, not a floor. A nonzero default would make everyone look
    somewhat affected and flatten the ranking that the allocation depends on."""
    priority = score_passenger(facts())
    assert priority.priority_index == 0
    assert priority.priority_band is PriorityBand.routine
    assert priority.factors == []


def test_every_point_is_attributable_to_a_named_factor():
    """The core requirement. A score without its factors is a number nobody can argue with,
    which in an operations room is worse than no score at all."""
    priority = score_passenger(facts(connection_broken=True, has_special_needs=True))
    assert priority.priority_index == sum(item["weight"] for item in priority.factors)
    assert {item["factor"] for item in priority.factors} == {
        "broken_connection",
        "special_needs_recorded",
    }
    for item in priority.factors:
        assert item["source"]


def test_each_factor_names_the_field_it_came_from():
    """`source` has to point at a real attribute, so "where does this come from" is answerable
    without reading the scoring function."""
    priority = score_passenger(
        facts(
            connection_broken=True,
            no_onward_option_today=True,
            has_special_needs=True,
            contact_missing=True,
            stranded_mid_itinerary=True,
        )
    )
    for item in priority.factors:
        if item["factor"] == "recorded_tier":
            continue
        assert hasattr(PassengerCohortFacts, "model_fields")
        assert item["source"] in PassengerCohortFacts.model_fields


def test_the_index_is_clamped_to_one_hundred():
    """The DB CHECK enforces 0..100. Saturating rather than overflowing is the intent: a
    passenger who is unreachable, needs assistance and is stranded overnight *is* the top."""
    priority = score_passenger(
        facts(
            tier="platinum",
            connection_broken=True,
            no_onward_option_today=True,
            has_special_needs=True,
            contact_missing=True,
            stranded_mid_itinerary=True,
        )
    )
    assert priority.priority_index == 100
    assert priority.priority_band is PriorityBand.critical


def test_a_zero_weighted_factor_is_omitted_not_recorded_as_plus_zero():
    """Recording `+0` would read as a factor that was considered and mattered, then did
    nothing. Omission is the honest rendering of 'policy gives this no weight'."""
    ruleset = {**DEFAULT_RULESET, "factors": {**DEFAULT_RULESET["factors"], "broken_connection": 0}}
    priority = score_passenger(facts(connection_broken=True), ruleset=ruleset)
    assert priority.factors == []
    assert priority.priority_index == 0


def test_unreachable_contact_outweighs_loyalty_tier():
    """A deliberate property of the seeded policy. Somebody who cannot be told anything has to
    be found in person; a tier is a commercial relationship. If a future weighting inverted
    this, it should be a decision someone made, not a silent drift."""
    unreachable = score_passenger(facts(contact_missing=True))
    platinum = score_passenger(facts(tier="platinum"))
    assert unreachable.priority_index > platinum.priority_index


def test_an_unknown_tier_contributes_nothing_rather_than_erroring():
    """Data this service does not recognise must not crash a recovery run, and must not be
    guessed at either."""
    assert score_passenger(facts(tier="obsidian")).priority_index == 0


# ------------------------------------------------------------------------------- bands


@pytest.mark.parametrize(
    ("index_facts", "expected"),
    [
        ({}, PriorityBand.routine),
        ({"journey_incomplete_only": True}, PriorityBand.routine),
        ({"connection_broken": True}, PriorityBand.elevated),
        ({"connection_broken": True, "no_onward_option_today": True}, PriorityBand.high),
        (
            {
                "connection_broken": True,
                "no_onward_option_today": True,
                "has_special_needs": True,
            },
            PriorityBand.critical,
        ),
    ],
)
def test_bands_follow_the_declared_thresholds(index_facts, expected):
    index_facts.pop("journey_incomplete_only", None)
    assert score_passenger(facts(**index_facts)).priority_band is expected


# ------------------------------------------------------------------------- determinism


def test_ordering_does_not_depend_on_input_order():
    """This ordering decides who gets one of 87 rooms. If it depended on how a query happened
    to sort, two identical runs could accommodate different people."""
    people = [
        facts(passenger_id=3, passenger_reference="PAX-3", booking_id=3, pnr="C"),
        facts(
            passenger_id=1,
            passenger_reference="PAX-1",
            booking_id=1,
            pnr="A",
            connection_broken=True,
        ),
        facts(
            passenger_id=2,
            passenger_reference="PAX-2",
            booking_id=2,
            pnr="B",
            connection_broken=True,
        ),
    ]
    forward = assess_passenger_impact(cohort_facts=people)
    backward = assess_passenger_impact(cohort_facts=list(reversed(people)))
    assert [p.passenger_id for p in forward.priorities] == [
        p.passenger_id for p in backward.priorities
    ]
    # Equal scores break by passenger id, ascending.
    assert [p.passenger_id for p in forward.priorities] == [1, 2, 3]


def test_the_ruleset_hash_changes_when_a_weight_changes():
    """Stamped onto every row, so an ordering can always be tied to the policy behind it."""
    altered = {
        **DEFAULT_RULESET,
        "factors": {**DEFAULT_RULESET["factors"], "broken_connection": 31},
    }
    assert ruleset_hash(altered) != ruleset_hash(DEFAULT_RULESET)


def test_the_ruleset_hash_is_stable_across_key_order():
    reordered = {key: DEFAULT_RULESET[key] for key in reversed(list(DEFAULT_RULESET))}
    assert ruleset_hash(reordered) == ruleset_hash(DEFAULT_RULESET)


# --------------------------------------------------------------------------- the policy


def test_the_ruleset_is_read_from_business_constraint_not_from_this_module():
    """The whole reason the weights live in data: an operator can change the ranking and the
    change is inspectable. A hard-coded ruleset would be an engineering opinion presented as
    arithmetic."""
    declared = {
        "version": "priority-ruleset-vtest",
        "factors": {"broken_connection": 90},
        "tier_weights": {},
        "bands": {"critical": 70, "high": 45, "elevated": 20, "routine": 0},
    }
    rows = [
        {
            "service": BUSINESS_CONSTRAINT_SERVICE,
            "constraint_key": BUSINESS_CONSTRAINT_KEY,
            "constraint_value": declared,
        }
    ]
    assert load_ruleset(rows) == declared
    priority = score_passenger(facts(connection_broken=True), ruleset=load_ruleset(rows))
    assert priority.priority_index == 90


def test_a_malformed_constraint_row_falls_back_rather_than_half_applying():
    """A ruleset with no factors would score everyone zero and silently disable the ranking."""
    rows = [
        {
            "service": BUSINESS_CONSTRAINT_SERVICE,
            "constraint_key": BUSINESS_CONSTRAINT_KEY,
            "constraint_value": {"version": "broken"},
        }
    ]
    assert load_ruleset(rows) == DEFAULT_RULESET


# ------------------------------------------------------------------------------ cohorts


def test_cohorts_partition_the_assessed_passengers_exactly():
    people = [
        facts(
            passenger_id=n,
            passenger_reference=f"PAX-{n}",
            booking_id=n,
            pnr=f"P{n}",
            connection_broken=n % 2 == 0,
            no_onward_option_today=n % 3 == 0,
        )
        for n in range(1, 13)
    ]
    assessment = assess_passenger_impact(cohort_facts=people)
    assert sum(c.passenger_count for c in assessment.cohorts) == len(people)
    assert assessment.passengers_assessed == len(people)
    all_bookings = [b for cohort in assessment.cohorts for b in cohort.booking_ids]
    assert sorted(all_bookings) == sorted(p.booking_id for p in people)


def test_cohorts_declare_their_basis_in_the_type():
    """`basis` is a Literal, so a cohort cannot claim to be anything but persisted records
    without a change that forces the conversation."""
    assessment = assess_passenger_impact(cohort_facts=[facts(connection_broken=True)])
    assert all(cohort.basis == "persisted_records" for cohort in assessment.cohorts)


def test_needing_accommodation_is_exactly_the_overnight_exposed_set():
    """The set a hotel allocation draws from, in the order it draws them."""
    people = [
        facts(
            passenger_id=1,
            passenger_reference="A",
            booking_id=1,
            pnr="A",
            no_onward_option_today=True,
        ),
        facts(
            passenger_id=2, passenger_reference="B", booking_id=2, pnr="B", connection_broken=True
        ),
        facts(
            passenger_id=3,
            passenger_reference="C",
            booking_id=3,
            pnr="C",
            no_onward_option_today=True,
            has_special_needs=True,
        ),
    ]
    assessment = assess_passenger_impact(cohort_facts=people)
    assert [p.passenger_id for p in assessment.needing_accommodation] == [3, 1]


# ------------------------------------------------------------------------------ service


async def test_the_service_refuses_rather_than_ranking_nobody():
    """An empty priority list would read as 'every passenger is equally fine'."""
    result = await PassengerImpactService().execute()
    assert result.status.value == "needs_human"
    assert "cohort facts" in result.reason


async def test_the_service_payload_carries_the_ruleset_hash_and_its_scope_limits():
    result = await PassengerImpactService().execute(
        cohort_facts=[facts(connection_broken=True, no_onward_option_today=True)]
    )
    assert result.status.value == "success"
    assert result.payload["ruleset_hash"] == ruleset_hash(DEFAULT_RULESET)
    assert result.payload["needing_accommodation"] == 1
    note = result.payload["scope_note"]
    for refused in ("seat availability", "party", "sub-categories"):
        assert refused in note
    assert result.evidence_refs == ["booking:1", "passenger:1"]
