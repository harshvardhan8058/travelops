"""Hotel search and allocation against the committed dataset.

The numbers here are not chosen — they fall out of the seeded inventory and the seeded rate cap.
174 stranded passengers at two per room need 87 rooms; the six properties inside the ₹6,000 cap
hold 71. The 16-room gap is the point of the scenario, and these tests exist so nobody
accidentally closes it by relaxing a cap, rounding differently, or mutating a counter.
"""

from __future__ import annotations

from app.db.seed import build_seed_plan
from app.services.hotel import (
    HotelAllocationService,
    HotelOption,
    HotelSearchService,
    allocate_rooms,
    load_constraints,
    rank_options,
    rooms_required,
)

#: Passengers on 6E 2134, the primary flight. The one number taken from the scenario.
PRIMARY_FLIGHT_PASSENGERS = 174


def _plan():
    return build_seed_plan()


def _options(plan=None) -> list[HotelOption]:
    plan = plan or _plan()
    return [
        HotelOption(
            hotel_id=row["id"],
            name=row["name"],
            airport_icao=row["airport_icao"],
            rate_inr=row["rate_inr"],
            is_partner=row["is_partner"],
            distance_km=float(row["distance_km"]),
            total_rooms=row["total_rooms"],
        )
        for row in plan["hotel"]
    ]


def _constraints(plan=None):
    return load_constraints((plan or _plan())["business_constraint"])


# -------------------------------------------------------------------------- constraints


def test_every_constraint_comes_from_a_seeded_row_not_a_default():
    """If any of these fell back to a literal, the demo's caps would be invisible to a
    reviewer reading the database."""
    constraints = _constraints()
    assert constraints.used_defaults == []
    assert constraints.max_rate_inr == 6000
    assert constraints.passengers_per_room == 2
    assert constraints.prefer_partner is True


def test_a_result_says_when_it_fell_back_to_a_default():
    """A default is legitimate; a silent default is not."""
    constraints = load_constraints([])
    assert set(constraints.used_defaults) == {
        "max_rate_inr",
        "prefer_partner",
        "passengers_per_room",
    }


# ------------------------------------------------------------------------------ rounding


def test_rooms_always_round_up():
    """Rounding down would leave someone in the terminal to satisfy an arithmetic
    convenience."""
    constraints = _constraints()
    assert rooms_required(passengers=174, constraints=constraints) == 87
    assert rooms_required(passengers=175, constraints=constraints) == 88
    assert rooms_required(passengers=1, constraints=constraints) == 1
    assert rooms_required(passengers=0, constraints=constraints) == 0


# ------------------------------------------------------------------------------- ranking


def test_the_rate_cap_excludes_the_five_expensive_properties():
    ranked = rank_options(_options(), constraints=_constraints())
    assert [option.hotel_id for option in ranked] == [1, 3, 6, 2, 4, 5]
    assert all(option.rate_inr <= 6000 for option in ranked)


def test_partner_properties_come_first_then_distance():
    ranked = rank_options(_options(), constraints=_constraints())
    partner_positions = [i for i, o in enumerate(ranked) if o.is_partner]
    assert partner_positions == [0, 1, 2]
    partner_distances = [o.distance_km for o in ranked if o.is_partner]
    assert partner_distances == sorted(partner_distances)


def test_turning_off_the_partner_preference_reorders_by_distance_alone():
    """Proves the preference is a real, data-driven lever rather than decoration."""
    constraints = _constraints().model_copy(update={"prefer_partner": False})
    ranked = rank_options(_options(), constraints=constraints)
    assert [o.hotel_id for o in ranked] == [1, 2, 3, 4, 5, 6]


def test_a_property_with_no_rooms_left_is_not_offered():
    """Availability is derived from held rooms, so a fully held property drops out of the
    ranking without anyone editing a counter."""
    options = [
        o.model_copy(update={"rooms_held": o.total_rooms}) if o.hotel_id == 1 else o
        for o in _options()
    ]
    assert 1 not in {o.hotel_id for o in rank_options(options, constraints=_constraints())}


def test_ranking_is_a_total_order_so_two_runs_cannot_disagree():
    options = _options()
    assert [o.hotel_id for o in rank_options(options, constraints=_constraints())] == [
        o.hotel_id for o in rank_options(list(reversed(options)), constraints=_constraints())
    ]


# ---------------------------------------------------------------------------- allocation


def test_the_seeded_scenario_is_sixteen_rooms_short():
    """The load-bearing assertion. A recovery tool that always succeeds teaches nobody
    anything, so the shortfall is designed in and pinned here."""
    result = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=_constraints()
    )
    assert result.rooms_required == 87
    assert result.rooms_allocated == 71
    assert result.shortfall_rooms == 16
    assert result.is_complete is False


def test_the_shortfall_is_stated_in_rooms_and_as_a_decision():
    """A number alone is not actionable. The note has to name what a person must now decide."""
    result = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=_constraints()
    )
    note = result.shortfall_note
    assert "71 of 87" in note
    assert "16 rooms short" in note
    assert "raise the cap" in note


def test_allocation_never_exceeds_a_property_available_rooms():
    result = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=_constraints()
    )
    by_id = {o.hotel_id: o for o in _options()}
    for allocation in result.allocations:
        assert allocation.rooms <= by_id[allocation.hotel_id].available_rooms


def test_allocation_never_breaches_the_rate_cap_to_close_the_gap():
    """The tempting bug: 16 rooms short, and hotel 7 has 40 rooms at ₹6,400. Spending over the
    cap to make the number go green would be the service quietly overriding a hard constraint."""
    result = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=_constraints()
    )
    assert all(a.rate_inr <= 6000 for a in result.allocations)
    assert set(result.excluded_by_rate_cap) == {7, 8, 9, 10, 11}


def test_cost_is_rooms_times_the_recorded_rate():
    result = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=_constraints()
    )
    assert result.total_cost_inr == sum(a.rooms * a.rate_inr * a.nights for a in result.allocations)
    assert result.total_cost_inr == 276600


def test_raising_the_cap_closes_the_gap_which_is_why_it_is_a_decision():
    """The counterfactual an operator is being asked to make. Also proves the shortfall is a
    genuine consequence of the cap rather than an artefact of the allocator."""
    constraints = _constraints().model_copy(update={"max_rate_inr": 9999})
    result = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=constraints
    )
    assert result.is_complete is True
    assert result.rooms_allocated == 87


def test_allocation_is_deterministic():
    first = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS, options=_options(), constraints=_constraints()
    )
    second = allocate_rooms(
        passengers=PRIMARY_FLIGHT_PASSENGERS,
        options=list(reversed(_options())),
        constraints=_constraints(),
    )
    assert first.model_dump() == second.model_dump()


# ------------------------------------------------------------------------------ services


async def test_search_commits_nothing_and_says_so():
    result = await HotelSearchService().execute(
        hotel_options=[o.model_dump() for o in _options()],
        passengers=PRIMARY_FLIGHT_PASSENGERS,
        business_constraints=_plan()["business_constraint"],
    )
    assert result.status.value == "success"
    assert result.payload["rooms_required"] == 87
    assert result.payload["eligible_capacity_rooms"] == 71
    assert result.payload["capacity_is_sufficient"] is False
    assert "Nothing is held" in result.payload["scope_note"]


async def test_search_ranks_without_reporting_a_shortfall_as_a_failure():
    """A search that escalates would take the decision away from the person looking at it."""
    result = await HotelSearchService().execute(
        hotel_options=[o.model_dump() for o in _options()],
        passengers=PRIMARY_FLIGHT_PASSENGERS,
        business_constraints=_plan()["business_constraint"],
    )
    assert result.status.value == "success"
    assert [o["rank"] for o in result.payload["options"]] == [1, 2, 3, 4, 5, 6]


async def test_allocation_escalates_with_the_partial_result_intact():
    """`needs_human`, not failure. The 71 rooms secured are real and must stand — failing would
    discard a good partial result and send everyone back to the terminal."""
    result = await HotelAllocationService().execute(
        hotel_options=[o.model_dump() for o in _options()],
        passengers=PRIMARY_FLIGHT_PASSENGERS,
        business_constraints=_plan()["business_constraint"],
    )
    assert result.status.value == "needs_human"
    assert result.payload["rooms_allocated"] == 71
    assert result.payload["shortfall_rooms"] == 16
    assert result.payload["passengers_unaccommodated"] == 32
    assert len(result.payload["allocations"]) == 6
    assert result.evidence_refs == [f"hotel:{n}" for n in (1, 2, 3, 4, 5, 6)]


async def test_allocation_succeeds_when_capacity_is_sufficient():
    result = await HotelAllocationService().execute(
        hotel_options=[o.model_dump() for o in _options()],
        passengers=40,
        business_constraints=_plan()["business_constraint"],
    )
    assert result.status.value == "success"
    assert result.payload["rooms_required"] == 20
    assert result.payload["shortfall_rooms"] == 0


async def test_both_services_refuse_without_inputs():
    for service in (HotelSearchService(), HotelAllocationService()):
        result = await service.execute()
        assert result.status.value == "needs_human"
