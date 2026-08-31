"""The verified figures must stay tied to the dataset that produces them.

`scripts/verify_phase2.py` asserts four numbers — 8 flights, 604 passengers, 22 at-risk
connections, 9 rotations. They are literals in that script on purpose: it is stdlib-only so it can
run on the host, inside the API container, or against a remote deployment, and importing the
generators would tie it to one of those.

The cost of literals is that they can go stale silently, and a stale expected figure is a nasty
failure: the verifier reports a wrong number and the natural reading is that the application
regressed. So this file is the link that stops it. Each literal is checked against the value
`data/generators/cascade_spec.BENGALURU_STORM` derives, and the connections figure is additionally
checked against what the real connection walk finds over a freshly seeded database — so a change to
the dataset fails here, naming the new figure, rather than surfacing later as an apparent
application defect.

Owner: Stream A (the verifier contract) / Stream C (the dataset it is derived from).
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator

import pytest
from data.generators.cascade_spec import BENGALURU_STORM as SPEC
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.config import REPO_ROOT
from app.db.base import Base
from app.db.scenario_queries import load_business_constraints, load_connection_inputs
from app.db.seed import seed_demo_dataset
from app.services.connection import (
    DEFAULT_MINIMUM_CONNECTION_MINUTES,
    ConnectionService,
    find_at_risk_connections,
)

VERIFIER = REPO_ROOT / "scripts" / "verify_phase2.py"


@pytest.fixture(scope="module")
def verifier():
    """Import the verifier for its constants.

    It guards its entry point with `if __name__ == "__main__"`, so importing it performs no HTTP
    and runs no checks. Reading the module is better than re-typing the numbers here, which would
    just move the drift one file along.
    """
    spec = importlib.util.spec_from_file_location("verify_phase2", VERIFIER)
    assert spec and spec.loader, f"cannot load {VERIFIER}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def seeded_sessions() -> AsyncIterator:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        await seed_demo_dataset(session)
        await session.commit()
    yield factory
    await engine.dispose()


class TestTheVerifierAgreesWithTheDataset:
    def test_the_expected_flight_count_is_the_declared_membership(self, verifier):
        assert len(SPEC.affected) == verifier.EXPECTED_FLIGHTS

    def test_the_expected_passenger_count_is_the_sum_of_the_per_flight_counts(self, verifier):
        assert SPEC.passengers_affected == verifier.EXPECTED_PASSENGERS

    def test_the_expected_connection_count_is_the_sum_of_the_per_flight_at_risk_targets(
        self, verifier
    ):
        """This is the one that was questioned, so it is spelled out.

        `at_risk_connections` per flight is 8, 5, 3, 2, 2, 0, 0, 2 — and the generator realises
        each as a tight onward segment. 22 is not a fixture-era number; it is this sum.
        """
        derived = sum(SPEC.at_risk_connections_by_flight.values())

        assert derived == 22, f"the dataset now describes {derived} at-risk connections"
        assert derived == verifier.EXPECTED_CONNECTIONS, (
            f"verify_phase2.py expects {verifier.EXPECTED_CONNECTIONS} but the dataset derives "
            f"{derived}; update the verifier constant and the demo script together"
        )

    def test_the_expected_pairing_count_is_the_declared_rotation_count(self, verifier):
        assert len(SPEC.pairings) == verifier.EXPECTED_PAIRINGS


class TestTheSeededDataReallyProducesTheFigure:
    async def test_the_connection_walk_finds_exactly_the_expected_count(
        self, seeded_sessions, verifier
    ):
        """Not the spec's intent — what the real service finds in the real rows.

        A generator can intend 22 at-risk connections and produce rows that yield a different
        number; only running the walk proves the two agree.
        """
        async with seeded_sessions() as session:
            affected = set(SPEC.at_risk_connections_by_flight)
            itineraries, flights = await load_connection_inputs(session, affected)
            assessment = find_at_risk_connections(
                itineraries=itineraries,
                flights=flights,
                minimum_connection_minutes=DEFAULT_MINIMUM_CONNECTION_MINUTES,
                affected_flight_ids=affected,
            )

        assert assessment.count == verifier.EXPECTED_CONNECTIONS
        # Distinct bookings, because the rollup unions rather than sums.
        assert len({item.booking_id for item in assessment.at_risk}) == assessment.count

    async def test_every_flight_contributes_exactly_what_the_spec_declares(self, seeded_sessions):
        """Per flight, so a shortfall names the flight instead of only the total.

        A total that happens to match while two flights are wrong in opposite directions would
        otherwise pass.
        """
        expected = SPEC.at_risk_connections_by_flight
        wrong: list[str] = []
        async with seeded_sessions() as session:
            for flight_id, want in sorted(expected.items()):
                itineraries, flights = await load_connection_inputs(session, {flight_id})
                got = find_at_risk_connections(
                    itineraries=itineraries,
                    flights=flights,
                    minimum_connection_minutes=DEFAULT_MINIMUM_CONNECTION_MINUTES,
                    affected_flight_ids={flight_id},
                ).count
                if got != want:
                    wrong.append(f"flight {flight_id}: expected {want}, walk found {got}")

        assert not wrong, "; ".join(wrong)

    async def test_the_service_reports_the_same_count_it_walked(self, seeded_sessions, verifier):
        """Through `ConnectionService`, because that payload is what the rollup unions."""
        async with seeded_sessions() as session:
            affected = set(SPEC.at_risk_connections_by_flight)
            itineraries, flights = await load_connection_inputs(session, affected)
            constraints = await load_business_constraints(session)
            result = await ConnectionService().execute(
                itineraries=itineraries,
                flights=flights,
                affected_flight_ids=affected,
                business_constraints=constraints,
            )

        assert result.payload["at_risk_count"] == verifier.EXPECTED_CONNECTIONS
        assert len(result.payload["at_risk"]) == verifier.EXPECTED_CONNECTIONS
        booking_ids = {item["booking_id"] for item in result.payload["at_risk"]}
        assert len(booking_ids) == verifier.EXPECTED_CONNECTIONS

    async def test_the_minimum_connection_time_is_the_one_the_figure_assumes(self, seeded_sessions):
        """22 depends on a 45-minute minimum against a 60-minute published gap.

        If the seeded business constraint ever moved past 60, every tight connection would break
        as published and the figure would change for a reason unrelated to the disruption.
        """
        async with seeded_sessions() as session:
            constraints = await load_business_constraints(session)

        from app.services.connection import _minimum_connection_minutes

        minimum = _minimum_connection_minutes(constraints)
        assert minimum == DEFAULT_MINIMUM_CONNECTION_MINUTES == 45
        assert minimum < 60, "the tight onward gap is 60 minutes and must be feasible as published"
