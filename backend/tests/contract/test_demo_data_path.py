"""The demo data path: what the documented command needs, and one trap that would pass review.

`make seed` → `make demo` is the path a reviewer watches. This file pins the dataset
invariants that path depends on, so they cannot drift while attention is elsewhere. It
asserts nothing about how many incidents `app.cli inject` opens today — that is expected to
change from one to the full cascade — only about the data the cascade must be built from.

## The trap

`_select_primary_flight` scopes candidates with `origin_icao == airport_icao`. That is right
for picking a primary departure, and wrong as a way to enumerate the cascade, because **UK 705
is an arrival into VOBL, not a departure from it**. A loop over that same query opens seven
incidents, not eight.

Seven still produces **nine** pairings. The count a reviewer checks stays correct while the
`onward_duty` mechanism disappears, PAIR-E1 is relabelled `operating`, and the headline
arithmetic silently becomes "7 flights → 9 rotations". A wrong number gets noticed; a right
number arrived at the wrong way does not. Hence the assertions below.

Owner: Stream C.
"""

from __future__ import annotations

import pytest
from data.generators.cascade_spec import BENGALURU_STORM
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.base import Base
from app.db.scenario_queries import cascade_rollup
from app.db.seed import (
    DEMO_DATASET_ID,
    INCIDENT_GROUP_REFERENCE,
    build_seed_plan,
    plan_digest,
    seed_demo_dataset,
)
from tests.contract.sqlite_support import create_sqlite_engine

ROOT_AIRPORT = "VOBL"

EXPECTED_AFFECTED_FLIGHTS = 8
EXPECTED_PASSENGERS = 604
EXPECTED_HOTELS = 11
EXPECTED_PAIRINGS = 9

#: The eight the scenario fixture names, with the delay each carries.
EXPECTED_DELAYS = {
    "6E 2134": 420,
    "6E 811": 110,
    "AI 503": 65,
    "6E 455": 95,
    "UK 812": 140,
    "AI 611": 55,
    "6E 297": 80,
    "UK 705": 70,
}


@pytest.fixture(scope="module")
def plan() -> dict:
    return build_seed_plan()


def _delayed(plan: dict) -> list[dict]:
    return [row for row in plan["flight"] if row["status"] == "delayed"]


# ------------------------------------------------------------------ what seed must provide


def test_the_seed_is_reproducible():
    """`make seed` twice must give the same dataset, or the demo shifts between runs."""
    assert plan_digest(build_seed_plan()) == plan_digest(build_seed_plan())


def test_the_dataset_carries_all_eight_affected_flights(plan):
    delayed = _delayed(plan)
    assert len(delayed) == EXPECTED_AFFECTED_FLIGHTS
    assert {row["flight_number"] for row in delayed} == set(EXPECTED_DELAYS)


def test_each_affected_flight_carries_the_fixture_delay(plan):
    """The delay is read from `estimated_departure`, so it has to be present and exact."""
    for row in _delayed(plan):
        scheduled = row["scheduled_departure"]
        estimated = row["estimated_departure"]
        assert estimated is not None, row["flight_number"]
        minutes = int((estimated - scheduled).total_seconds() // 60)
        assert minutes == EXPECTED_DELAYS[row["flight_number"]], row["flight_number"]


def test_the_storm_group_is_seeded_with_the_scenario_clock(plan):
    """`inject` reads the clock off this row, and the Delay Risk `as_of` follows from it."""
    group = plan["incident_group"][0]
    assert group["reference"] == INCIDENT_GROUP_REFERENCE
    assert group["demo_dataset_id"] == DEMO_DATASET_ID
    assert group["airport_icao"] == ROOT_AIRPORT
    assert group["opened_at"] == BENGALURU_STORM.injected_at.astimezone(group["opened_at"].tzinfo)


def test_the_hotels_and_passengers_behind_the_headline_are_present(plan):
    assert len([h for h in plan["hotel"] if h["airport_icao"] == ROOT_AIRPORT]) == (EXPECTED_HOTELS)
    assert sum(f.passengers for f in BENGALURU_STORM.affected_flights) == EXPECTED_PASSENGERS


# --------------------------------------------------------------------------- the trap


def test_one_affected_flight_is_an_arrival_not_a_departure(plan):
    """UK 705 is `VAAH → VOBL`. Anything enumerating the cascade by departure misses it."""
    delayed = _delayed(plan)
    departures = [row for row in delayed if row["origin_icao"] == ROOT_AIRPORT]
    arrivals = [row for row in delayed if row["destination_icao"] == ROOT_AIRPORT]

    assert len(departures) == 7
    assert len(arrivals) == 1
    assert arrivals[0]["flight_number"] == "UK 705"
    assert len(departures) + len(arrivals) == EXPECTED_AFFECTED_FLIGHTS


def test_a_departures_only_cascade_still_reports_nine_but_loses_a_mechanism():
    """The reason the assertion above matters.

    Scoping the cascade to VOBL departures drops UK 705 and yields seven flights. The pairing
    count stays at nine, so nothing looks wrong — but `onward_duty` vanishes from the graph,
    PAIR-E1 is relabelled `operating`, and "eight flights, nine rotations" is no longer what
    the data says. A wrong count gets caught; a right count reached the wrong way does not.
    """
    from app.services.crew_impact import attribute_pairing_impacts

    def assess(flight_ids: set[int]) -> tuple[int, dict[str, int], str]:
        impacts = [
            impact
            for impact in attribute_pairing_impacts(
                affected_flights=[
                    f for f in BENGALURU_STORM.affected_flights if f.flight_id in flight_ids
                ],
                pairings=list(BENGALURU_STORM.pairings),
                flights=BENGALURU_STORM.flights_by_id,
            )
            if impact.is_at_risk
        ]
        counts: dict[str, int] = {}
        for impact in impacts:
            counts[impact.mechanism.value] = counts.get(impact.mechanism.value, 0) + 1
        e1 = next(i for i in impacts if i.pairing_reference == "PAIR-E1")
        return len(impacts), counts, e1.mechanism.value

    all_eight = {f.flight_id for f in BENGALURU_STORM.affected_flights}
    inbound_id = next(
        f.flight_id for f in BENGALURU_STORM.affected_flights if f.flight_number == "UK 705"
    )

    total_eight, mechanisms_eight, e1_eight = assess(all_eight)
    total_seven, mechanisms_seven, e1_seven = assess(all_eight - {inbound_id})

    # The correct cascade: nine rotations and all four mechanisms.
    assert total_eight == EXPECTED_PAIRINGS
    assert len(mechanisms_eight) == 4
    assert e1_eight == "onward_duty"

    # The trap: the same nine, quietly missing a mechanism.
    assert total_seven == EXPECTED_PAIRINGS
    assert len(mechanisms_seven) == 3
    assert "onward_duty" not in mechanisms_seven
    assert e1_seven == "operating"


# ------------------------------------------------------- the rollup never fabricates


@pytest.fixture
async def session(tmp_path):
    engine = create_sqlite_engine(tmp_path / "demo_path.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        await seed_demo_dataset(active)
        await active.commit()
        yield active
    await engine.dispose()


async def test_a_seeded_but_unrun_dataset_rolls_up_to_zero(session):
    """Immediately after `make seed`, before anything has run.

    The dataset-level facts are already true because they are counted from rows. The workflow
    figures are zero because no service has reported anything, and `is_complete` says so. A
    rollup that filled these in from the fixture would put numbers on screen that no run
    produced.
    """
    from sqlalchemy import select

    from app.models.workflow import IncidentGroup

    group = (
        (
            await session.execute(
                select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
            )
        )
        .scalars()
        .one()
    )
    rollup = await cascade_rollup(session, group_id=group.id)

    # No incidents opened yet, so nothing is attributed to the group.
    assert rollup.incidents_in_group == 0
    assert rollup.flights_affected == 0
    assert rollup.connections_at_risk == 0
    assert rollup.crew_pairings_affected == 0
    assert rollup.pairings == []
    assert rollup.is_complete is False

    # Airport-level facts do not depend on a run.
    assert rollup.candidate_hotels == EXPECTED_HOTELS
    assert rollup.group_reference == INCIDENT_GROUP_REFERENCE
