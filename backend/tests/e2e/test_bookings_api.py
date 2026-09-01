"""`GET /bookings/{pnr}` must agree with the database and the flight board's own derivations.

This is the endpoint the passenger view reads for "what flight was I on" — `PassengerImpact`
carries a priority ranking keyed on `pnr` but no flight facts at all, so a passenger screen with
nothing else to read from would have had to invent a flight number or omit the trip. Both are
wrong; this endpoint is the honest alternative.

The tests pin three things: a booking's segments come back in `segment_order`, each segment's
`delay_minutes` agrees with `_flight_delay_minutes` — the same function `GET /flights` and
`POST /scenarios` use, so the three can never disagree about how delayed a flight is — and a PNR
that does not exist answers 404 with a `resolution` an operator or a passenger screen can show,
not a bare "not found".

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.db.base import Base
from app.db.scenario_queries import _flight_delay_minutes
from app.db.seed import seed_demo_dataset
from app.db.session import get_session
from app.main import app
from app.models.enums import TriggerType
from app.models.reference import Booking, BookingSegment, Flight
from app.models.workflow import Incident

PREFIX = "/api/v1"


@pytest.fixture
async def booking_engine() -> AsyncIterator:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def booking_sessions(booking_engine):
    factory = async_sessionmaker(bind=booking_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        await seed_demo_dataset(session)
        await session.commit()
    return factory


@pytest.fixture
def booking_client(booking_sessions) -> AsyncIterator[TestClient]:
    """The real app over the seeded dataset. Only the session dependency is overridden."""

    async def override() -> AsyncIterator:
        async with booking_sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.pop(get_session, None)


async def _a_multi_segment_pnr(sessions) -> str:
    """The seeded dataset carries at least one connecting itinerary; find one rather than
    hardcoding a reference the generator is free to change."""
    async with sessions() as session:
        rows = (
            await session.execute(
                select(BookingSegment.booking_id, func.count())
                .group_by(BookingSegment.booking_id)
                .having(func.count() > 1)
            )
        ).all()
        assert rows, "expected at least one multi-segment booking in the seeded dataset"
        booking_id = rows[0][0]
        pnr = (
            await session.execute(select(Booking.pnr).where(Booking.id == booking_id))
        ).scalar_one()
    return str(pnr)


async def _any_pnr(sessions) -> str:
    async with sessions() as session:
        pnr = (await session.execute(select(Booking.pnr).limit(1))).scalar_one()
    return str(pnr)


class TestALookupThatExists:
    async def test_segments_come_back_ordered(self, booking_client, booking_sessions):
        pnr = await _a_multi_segment_pnr(booking_sessions)

        response = booking_client.get(f"{PREFIX}/bookings/{pnr}")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["pnr"] == pnr
        assert len(body["segments"]) >= 2
        orders = [segment["segment_order"] for segment in body["segments"]]
        assert orders == sorted(orders), "segments must be in segment_order, not insertion order"

    async def test_delay_minutes_agrees_with_the_shared_derivation(
        self, booking_client, booking_sessions
    ):
        """The figure a passenger sees must be the same figure the operator board and the
        scenario validator would derive for the same flight — never a second formula."""
        pnr = await _a_multi_segment_pnr(booking_sessions)

        response = booking_client.get(f"{PREFIX}/bookings/{pnr}")
        body = response.json()

        async with booking_sessions() as session:
            flights_by_id = {
                row.id: row
                for row in (
                    await session.execute(
                        select(Flight).where(
                            Flight.id.in_([s["flight_id"] for s in body["segments"]])
                        )
                    )
                )
                .scalars()
                .all()
            }

        for segment in body["segments"]:
            derived = _flight_delay_minutes(flights_by_id[segment["flight_id"]])
            assert segment["delay_minutes"] == derived, (
                f"flight {segment['flight_number']}: booking={segment['delay_minutes']} "
                f"derived={derived}"
            )

    async def test_a_pnr_is_looked_up_case_insensitively(self, booking_client, booking_sessions):
        """Passengers do not reliably type references in the case they were issued."""
        pnr = await _any_pnr(booking_sessions)

        response = booking_client.get(f"{PREFIX}/bookings/{pnr.lower()}")
        assert response.status_code == 200, response.text
        assert response.json()["pnr"] == pnr

    async def test_the_passenger_reference_matches_the_impacts_contract(
        self, booking_client, booking_sessions
    ):
        """`PassengerImpact.passenger_reference` and this endpoint's `passenger_reference` must
        name the same passenger record, since the passenger screen correlates the two by it."""
        pnr = await _any_pnr(booking_sessions)

        async with booking_sessions() as session:
            booking = (
                await session.execute(select(Booking).where(Booking.pnr == pnr))
            ).scalar_one()
            from app.models.reference import Passenger

            passenger = await session.get(Passenger, booking.passenger_id)

        response = booking_client.get(f"{PREFIX}/bookings/{pnr}")
        assert response.json()["passenger_reference"] == passenger.reference

    async def test_an_incident_reference_appears_only_on_the_affected_segment(
        self, booking_client, booking_sessions
    ):
        pnr = await _a_multi_segment_pnr(booking_sessions)

        before = booking_client.get(f"{PREFIX}/bookings/{pnr}").json()
        first_flight_id = before["segments"][0]["flight_id"]

        async with booking_sessions() as session:
            session.add(
                Incident(
                    reference="INC-BOOKING-01",
                    flight_id=first_flight_id,
                    trigger_type=TriggerType.weather,
                    severity="high",
                    opened_at=datetime.now(tz=UTC),
                    demo_dataset_id="bengaluru_storm",
                )
            )
            await session.commit()

        after = booking_client.get(f"{PREFIX}/bookings/{pnr}").json()
        by_flight = {s["flight_id"]: s["incident_reference"] for s in after["segments"]}
        assert by_flight[first_flight_id] == "INC-BOOKING-01"
        for segment in after["segments"]:
            if segment["flight_id"] != first_flight_id:
                assert segment["incident_reference"] is None


class TestALookupThatDoesNotExist:
    async def test_an_unknown_pnr_answers_404_with_a_resolution(self, booking_client):
        response = booking_client.get(f"{PREFIX}/bookings/ZZZZZZ")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["details"]["resolution"], "a passenger screen needs something to say"

    async def test_the_404_names_the_pnr_that_was_looked_up(self, booking_client):
        response = booking_client.get(f"{PREFIX}/bookings/NOTREAL")
        assert response.json()["error"]["details"]["pnr"] == "NOTREAL"
