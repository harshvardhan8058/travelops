"""Seed the committed bengaluru_storm dataset.

Stream A's CLI imports two functions from here; it does not know how the dataset is built:

    from app.db.seed import seed_demo_dataset, reset_demo_dataset

    async def cmd_seed() -> int:
        async with get_sessionmaker()() as session:
            report = await seed_demo_dataset(session)
            print(report.summary())
            return 0

    async def cmd_reset() -> int:
        async with get_sessionmaker()() as session:
            report = await reset_demo_dataset(session)
            print(report.summary())
            return 0

## One dataset, not two

Every row comes from `data/loaders` (real, archived) or `data/generators` (deterministic,
seeded). Nothing is written out by hand here. The build is split in two on purpose:

* `build_seed_plan()` is **pure** — it returns ordered rows per table and touches no database,
  so determinism is testable without Postgres.
* `seed_demo_dataset()` inserts that plan.

`plan_digest()` hashes the plan, which is how "byte-identical for seed 20260807" becomes a
checkable claim rather than an aspiration.

## Explicit primary keys

The seed assigns its own ids rather than letting sequences do it. Evidence references like
`flight:1` and `pairing_leg:23` then mean the same thing in every environment, which is what
makes a recorded decision replayable. Postgres sequences are advanced afterwards so later
inserts do not collide.

Owner: Stream C.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crew import (
    CrewMember,
    CrewPairingAssignment,
    Pairing,
    PairingImpact,
    PairingLeg,
)
from app.models.enums import IncidentState, ProvenanceKind, TriggerType
from app.models.policy import (
    BusinessConstraint,
    EntitlementEvaluation,
    PolicyApplicability,
)
from app.models.reference import (
    Airport,
    Booking,
    BookingSegment,
    Flight,
    Hotel,
    Passenger,
    Runway,
    WeatherObservation,
)
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    DecisionLog,
    HotelReservation,
    HumanDecision,
    Incident,
    IncidentGroup,
    IncidentOutcome,
    Notification,
    Plan,
    PlanTask,
    Prediction,
)

#: Tags the rows this module owns. `reset` never touches anything outside the dataset.
DEMO_DATASET_ID = "bengaluru_storm"

INCIDENT_GROUP_REFERENCE = "GRP-2026-0820-VOBL"

#: Insert order. Parents before children; reset walks it in reverse.
TABLE_ORDER: tuple[str, ...] = (
    "airport",
    "runway",
    "weather_observation",
    "flight",
    "hotel",
    "business_constraint",
    "crew_member",
    "pairing",
    "pairing_leg",
    "crew_pairing_assignment",
    "passenger",
    "booking",
    "booking_segment",
    "incident_group",
)


@dataclass(slots=True)
class SeedReport:
    dataset_id: str
    digest: str
    counts: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"seeded dataset '{self.dataset_id}' digest={self.digest}",
            *(f"  {table:26} {count:>6}" for table, count in self.counts.items()),
            f"  {'TOTAL':26} {sum(self.counts.values()):>6}",
        ]
        if self.deleted:
            removed = sum(self.deleted.values())
            lines.insert(1, f"  removed {removed} pre-existing demo rows first")
        return "\n".join(lines)


@dataclass(slots=True)
class ResetReport:
    dataset_id: str
    deleted: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        total = sum(self.deleted.values())
        lines = [f"reset dataset '{self.dataset_id}': {total} rows removed"]
        lines += [f"  {table:26} {count:>6}" for table, count in self.deleted.items() if count]
        return "\n".join(lines)


# --------------------------------------------------------------------------- the plan


def build_seed_plan() -> dict[str, list[dict[str, Any]]]:
    """Every row, in insert order. Pure: no database, no clock, no network.

    Imports are local because `data/` is not part of the application wheel — it is reachable
    via the repository root, which `app.config.REPO_ROOT` defines.
    """
    import sys

    from app.config import REPO_ROOT

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from data.generators.cascade_spec import BENGALURU_STORM
    from data.generators.scenario_dataset import build_scenario_dataset
    from data.loaders.ourairports import load_airports, load_runways, verify_snapshot

    from app.providers.weather.fixture import load_snapshot
    from app.providers.weather.live import reading_from_metar, readings_from_taf

    # An archive whose hash is not checked is a claim, not evidence. Fail the seed rather
    # than load reference data that may have been edited in place.
    verify_snapshot()

    scenario = BENGALURU_STORM
    dataset = build_scenario_dataset(scenario)

    plan: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLE_ORDER}

    # ---------------------------------------------------------------- reference (real)
    for airport in load_airports():
        plan["airport"].append(
            {
                "icao_code": airport.icao_code,
                "iata_code": airport.iata_code,
                "name": airport.name,
                "city": airport.city,
                "country": airport.country,
                "latitude": airport.latitude,
                "longitude": airport.longitude,
                "timezone": airport.timezone,
                "source_ref": airport.source_ref,
            }
        )

    for index, runway in enumerate(load_runways(), start=1):
        plan["runway"].append(
            {
                "id": index,
                "airport_icao": runway.airport_icao,
                "designator": runway.designator,
                "heading_degrees_true": runway.heading_degrees_true,
                "heading_source": runway.heading_source,
                "length_ft": runway.length_ft,
                "is_active": runway.is_active,
            }
        )

    # ---------------------------------------------------------------- weather
    snapshot = load_snapshot()
    snapshot_clock = datetime.fromisoformat(
        str(snapshot["source"]["retrieved_at"]).replace("Z", "+00:00")
    )
    observation_id = 0

    # Archived observations. Labelled `fixture`: the bytes came from a real source, but a
    # replay is not an observation of current conditions.
    for row in sorted(snapshot["metar"], key=lambda item: item["icaoId"]):
        reading = reading_from_metar(
            row,
            now=snapshot_clock,
            provenance_kind=ProvenanceKind.fixture,
            provider="awc-fixture",
        )
        observation_id += 1
        plan["weather_observation"].append(_weather_row(observation_id, reading, is_forecast=False))

    # TAF periods, separated by `is_forecast` so a forecast can never be scored as though it
    # were an observation.
    for row in sorted(snapshot["taf"], key=lambda item: item["icaoId"]):
        for reading in readings_from_taf(
            row,
            now=snapshot_clock,
            provenance_kind=ProvenanceKind.fixture,
            provider="awc-fixture",
        ):
            observation_id += 1
            plan["weather_observation"].append(
                _weather_row(observation_id, reading, is_forecast=True)
            )

    # The injected scenario observation: the one the demo actually scores.
    injected = snapshot["injected"]
    observed_at = datetime.fromisoformat(str(injected["observed_at"]).replace("Z", "+00:00"))
    observation_id += 1
    plan["weather_observation"].append(
        {
            "id": observation_id,
            "airport_icao": injected["airport_icao"],
            "observed_at": observed_at,
            "is_forecast": False,
            "wind_speed_kt": injected["wind_speed_kt"],
            "wind_direction_deg": injected["wind_direction_deg"],
            "visibility_m": injected["visibility_m"],
            "ceiling_ft": injected["ceiling_ft"],
            "precipitation": injected["precipitation"],
            "raw_metar": injected["raw_metar"],
            "provenance_kind": ProvenanceKind.fixture,
            "provenance_provider": "awc-fixture",
            "source_ref": f"fixture:{injected['scenario']}:metar:{injected['airport_icao']}",
        }
    )

    # ---------------------------------------------------------------- flights (synthetic)
    all_flights = [
        *scenario.affected_flights,
        *scenario.support_flights,
        *dataset.onward_flights,
    ]
    for flight in sorted(all_flights, key=lambda item: item.flight_id):
        block = int((flight.scheduled_arrival - flight.scheduled_departure).total_seconds() // 60)
        plan["flight"].append(
            {
                "id": flight.flight_id,
                "flight_number": flight.flight_number,
                "airline_code": flight.flight_number.split()[0],
                "origin_icao": flight.origin_icao,
                "destination_icao": flight.destination_icao,
                "scheduled_departure": flight.scheduled_departure,
                "scheduled_arrival": flight.scheduled_arrival,
                "estimated_departure": (flight.revised_departure if flight.delay_minutes else None),
                "block_time_minutes": block,
                "status": "delayed" if flight.delay_minutes else "scheduled",
                "is_domestic": True,
                "gate": None,
                # Schedules stay `synthetic` until the AIKosh file is archived with its
                # licence and a loader contract test passes.
                "provenance_kind": ProvenanceKind.synthetic,
                "source_ref": f"generator:cascade_spec:{scenario.scenario_key}",
            }
        )

    # ---------------------------------------------------------------- hotels
    for index, hotel in enumerate(dataset.hotels, start=1):
        plan["hotel"].append(
            {
                "id": index,
                "name": hotel.name,
                "airport_icao": hotel.airport_icao,
                "rate_inr": hotel.rate_inr,
                "is_partner": hotel.is_partner,
                "distance_km": hotel.distance_km,
                "total_rooms": hotel.total_rooms,
                "available_rooms": hotel.available_rooms,
                "provenance_kind": ProvenanceKind.synthetic,
            }
        )

    # ---------------------------------------------------------------- constraints
    for index, constraint in enumerate(dataset.constraints, start=1):
        plan["business_constraint"].append(
            {
                "id": index,
                "service": constraint.service,
                "constraint_key": constraint.constraint_key,
                "constraint_value": constraint.constraint_value,
                "is_hard": constraint.is_hard,
                "version": constraint.version,
                "description": constraint.description,
            }
        )

    # ---------------------------------------------------------------- crew
    pairing_ids = {pairing.reference: pairing.pairing_id for pairing in scenario.pairings}
    crew_ids: dict[str, int] = {}
    for index, (crew_reference, _pairing_reference) in enumerate(
        scenario.crew_assignments, start=1
    ):
        crew_ids[crew_reference] = index

    for crew_reference, pairing_reference in scenario.crew_assignments:
        pairing = next(p for p in scenario.pairings if p.reference == pairing_reference)
        # PAIR-A2 is the cabin complement on 6E 2134; everything else carries cockpit crew.
        role = "cabin" if pairing_reference == "PAIR-A2" else "cockpit"
        plan["crew_member"].append(
            {
                "id": crew_ids[crew_reference],
                "reference": crew_reference,
                "full_name": f"Crew {crew_reference.split('-')[1]}",
                "role": role,
                "base_icao": pairing.base_icao,
                # Present for display only. No code path treats this as a legality decision.
                "duty_hours_limit": 13,
                "provenance_kind": ProvenanceKind.synthetic,
            }
        )

    for pairing in sorted(scenario.pairings, key=lambda item: item.reference):
        legs = pairing.ordered_legs
        plan["pairing"].append(
            {
                "id": pairing.pairing_id,
                "reference": pairing.reference,
                "base_icao": pairing.base_icao,
                "starts_at": _flight_by_id(all_flights, legs[0].flight_id).scheduled_departure,
                "ends_at": _flight_by_id(all_flights, legs[-1].flight_id).scheduled_arrival,
            }
        )
        for leg in legs:
            plan["pairing_leg"].append(
                {
                    "id": leg.leg_id,
                    "pairing_id": pairing.pairing_id,
                    "flight_id": leg.flight_id,
                    "leg_order": leg.leg_order,
                    "role": leg.role,
                    "min_connection_minutes": leg.min_connection_minutes,
                }
            )

    for index, (crew_reference, pairing_reference) in enumerate(scenario.crew_assignments, start=1):
        plan["crew_pairing_assignment"].append(
            {
                "id": index,
                "crew_member_id": crew_ids[crew_reference],
                "pairing_id": pairing_ids[pairing_reference],
            }
        )

    # ---------------------------------------------------------------- passengers
    passenger_ids: dict[str, int] = {}
    for index, passenger in enumerate(dataset.passengers, start=1):
        passenger_ids[passenger.reference] = index
        plan["passenger"].append(
            {
                "id": index,
                "reference": passenger.reference,
                "full_name": passenger.full_name,
                "email": passenger.email,
                "phone": passenger.phone,
                "tier": passenger.tier,
                "has_special_needs": passenger.has_special_needs,
                "provenance_kind": ProvenanceKind.synthetic,
            }
        )

    booking_ids: dict[str, int] = {}
    for index, booking in enumerate(dataset.bookings, start=1):
        booking_ids[booking.pnr] = index
        plan["booking"].append(
            {
                "id": index,
                "pnr": booking.pnr,
                "passenger_id": passenger_ids[booking.passenger_reference],
                "cabin": booking.cabin,
                "one_way_basic_fare_inr": booking.one_way_basic_fare_inr,
                "airline_fuel_charge_inr": booking.airline_fuel_charge_inr,
                "payment_method": booking.payment_method,
                "contact_info_provided_at_booking": True,
            }
        )

    for segment in dataset.segments:
        plan["booking_segment"].append(
            {
                "id": segment.segment_id,
                "booking_id": booking_ids[segment.booking_pnr],
                "flight_id": segment.flight_id,
                "segment_order": segment.segment_order,
                "checked_in_on_time": True,
            }
        )

    # ---------------------------------------------------------------- the cascade group
    #
    # The group exists so the reference is stable and Stream A's inject can attach incidents
    # to it. Rollups are NOT stored: every count in the API is derived from the rows above,
    # which is what keeps "8 flights, 604 passengers, 9 rotations" checkable.
    plan["incident_group"].append(
        {
            "id": 1,
            "reference": INCIDENT_GROUP_REFERENCE,
            "root_cause": TriggerType.weather,
            "airport_icao": scenario.root_airport_icao,
            "severity": "high",
            "state": IncidentState.detected,
            "opened_at": scenario.injected_at.astimezone(UTC),
            "demo_dataset_id": DEMO_DATASET_ID,
        }
    )

    return plan


def _weather_row(row_id: int, reading: Any, *, is_forecast: bool) -> dict[str, Any]:
    return {
        "id": row_id,
        "airport_icao": reading.airport_icao,
        "observed_at": reading.observed_at,
        "is_forecast": is_forecast,
        "wind_speed_kt": reading.wind_speed_kt,
        "wind_direction_deg": reading.wind_direction_deg,
        "visibility_m": reading.visibility_m,
        "ceiling_ft": reading.ceiling_ft,
        "precipitation": reading.precipitation,
        "raw_metar": reading.raw_metar,
        "provenance_kind": reading.provenance.kind,
        "provenance_provider": reading.provenance.provider,
        "source_ref": reading.provenance.source_ref,
    }


def _flight_by_id(flights: list[Any], flight_id: int) -> Any:
    return next(flight for flight in flights if flight.flight_id == flight_id)


def plan_digest(plan: dict[str, list[dict[str, Any]]] | None = None) -> str:
    """SHA-256 over the whole plan.

    This is what makes "`make seed` produces a byte-identical dataset for seed 20260807" a
    claim anyone can check: run it twice, compare the digest.
    """
    resolved = plan if plan is not None else build_seed_plan()
    payload = json.dumps(resolved, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# --------------------------------------------------------------------------- persistence

_MODEL_BY_TABLE = {
    "airport": Airport,
    "runway": Runway,
    "weather_observation": WeatherObservation,
    "flight": Flight,
    "hotel": Hotel,
    "business_constraint": BusinessConstraint,
    "crew_member": CrewMember,
    "pairing": Pairing,
    "pairing_leg": PairingLeg,
    "crew_pairing_assignment": CrewPairingAssignment,
    "passenger": Passenger,
    "booking": Booking,
    "booking_segment": BookingSegment,
    "incident_group": IncidentGroup,
}


async def _delete_workflow_records(session: AsyncSession, report: ResetReport) -> None:
    """Remove everything the workflow appended for the demo incidents, child-first.

    A run leaves behind decision-log entries, a plan, plan tasks, assurance evaluations and
    actions, all pointing at the demo incidents. Deleting the incidents while those exist
    raises `ForeignKeyViolationError` on Postgres — and passes silently on SQLite, which does
    not enforce foreign keys unless asked. `make reset` after a demo run would have failed on
    the demo machine and nowhere else, so the order below is explicit and tested against both
    engines.

    Scoped to the demo incidents throughout: an operator's own incidents are untouched.
    """
    incident_ids = (
        (
            await session.execute(
                select(Incident.id).where(Incident.demo_dataset_id == DEMO_DATASET_ID)
            )
        )
        .scalars()
        .all()
    )
    group_ids = (
        (
            await session.execute(
                select(IncidentGroup.id).where(IncidentGroup.demo_dataset_id == DEMO_DATASET_ID)
            )
        )
        .scalars()
        .all()
    )

    if group_ids:
        result = await session.execute(
            delete(PairingImpact).where(PairingImpact.incident_group_id.in_(group_ids))
        )
        report.deleted["pairing_impact"] = result.rowcount or 0

    if not incident_ids:
        return

    plan_ids = (
        (await session.execute(select(Plan.id).where(Plan.incident_id.in_(incident_ids))))
        .scalars()
        .all()
    )
    task_ids = (
        (
            (await session.execute(select(PlanTask.id).where(PlanTask.plan_id.in_(plan_ids))))
            .scalars()
            .all()
        )
        if plan_ids
        else []
    )
    evaluation_ids = (
        (
            (
                await session.execute(
                    select(AssuranceEvaluation.id).where(
                        AssuranceEvaluation.plan_task_id.in_(task_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if task_ids
        else []
    )
    action_ids = (
        (
            (await session.execute(select(Action.id).where(Action.plan_task_id.in_(task_ids))))
            .scalars()
            .all()
        )
        if task_ids
        else []
    )

    # Deepest children first. Each step names what would otherwise hold a reference.
    steps: list[tuple[str, Any]] = []
    if action_ids:
        steps += [
            ("notification", delete(Notification).where(Notification.action_id.in_(action_ids))),
            (
                "hotel_reservation",
                delete(HotelReservation).where(HotelReservation.action_id.in_(action_ids)),
            ),
        ]
    if task_ids:
        steps.append(("action", delete(Action).where(Action.plan_task_id.in_(task_ids))))
    if evaluation_ids:
        steps.append(
            (
                "human_decision",
                delete(HumanDecision).where(HumanDecision.assurance_id.in_(evaluation_ids)),
            )
        )
    if task_ids:
        steps.append(
            (
                "assurance_evaluation",
                delete(AssuranceEvaluation).where(AssuranceEvaluation.plan_task_id.in_(task_ids)),
            )
        )
    if plan_ids:
        steps.append(("plan_task", delete(PlanTask).where(PlanTask.plan_id.in_(plan_ids))))
    steps += [
        ("plan", delete(Plan).where(Plan.incident_id.in_(incident_ids))),
        # Stream B's records also hang off the incident.
        (
            "entitlement_evaluation",
            delete(EntitlementEvaluation).where(
                EntitlementEvaluation.incident_id.in_(incident_ids)
            ),
        ),
        (
            "policy_applicability",
            delete(PolicyApplicability).where(PolicyApplicability.incident_id.in_(incident_ids)),
        ),
        (
            "incident_outcome",
            delete(IncidentOutcome).where(IncidentOutcome.incident_id.in_(incident_ids)),
        ),
        ("decision_log", delete(DecisionLog).where(DecisionLog.incident_id.in_(incident_ids))),
        ("incident", delete(Incident).where(Incident.id.in_(incident_ids))),
    ]

    for table, statement in steps:
        result = await session.execute(statement)
        report.deleted[table] = (report.deleted.get(table) or 0) + (result.rowcount or 0)

    # Predictions are referenced BY incidents, so they go once the incidents are gone.
    flight_ids = [row["id"] for row in build_seed_plan()["flight"]]
    result = await session.execute(delete(Prediction).where(Prediction.flight_id.in_(flight_ids)))
    report.deleted["prediction"] = result.rowcount or 0


async def reset_demo_dataset(session: AsyncSession) -> ResetReport:
    """Remove the demo dataset and nothing else.

    Scoped deliberately rather than truncating: incidents and groups by `demo_dataset_id`,
    and the seeded reference and synthetic rows by the exact keys the generator produces.
    A `TRUNCATE` here would take an operator's own data with it.
    """
    report = ResetReport(dataset_id=DEMO_DATASET_ID)
    plan = build_seed_plan()

    # Everything the workflow appended, child-first, before the rows it points at.
    await _delete_workflow_records(session, report)
    await session.flush()

    for table in reversed(TABLE_ORDER):
        model = _MODEL_BY_TABLE[table]
        rows = plan[table]
        if not rows:
            continue

        if table == "incident_group":
            statement = delete(model).where(model.demo_dataset_id == DEMO_DATASET_ID)
        elif table == "airport":
            statement = delete(model).where(model.icao_code.in_([row["icao_code"] for row in rows]))
        else:
            statement = delete(model).where(model.id.in_([row["id"] for row in rows]))

        result = await session.execute(statement)
        report.deleted[table] = result.rowcount or 0

    await session.flush()
    return report


async def seed_demo_dataset(session: AsyncSession, *, reset: bool = True) -> SeedReport:
    """Insert the committed dataset. Idempotent when `reset` is left on.

    Runs inside the caller's transaction so a partial dataset cannot be committed.
    """
    plan = build_seed_plan()
    report = SeedReport(dataset_id=DEMO_DATASET_ID, digest=plan_digest(plan))

    if reset:
        reset_report = await reset_demo_dataset(session)
        report.deleted = reset_report.deleted

    for table in TABLE_ORDER:
        rows = plan[table]
        if not rows:
            continue
        await session.execute(_MODEL_BY_TABLE[table].__table__.insert(), rows)
        report.counts[table] = len(rows)

    await session.flush()
    await _advance_sequences(session, plan)
    return report


async def _advance_sequences(session: AsyncSession, plan: dict[str, list[dict[str, Any]]]) -> None:
    """Move Postgres sequences past the explicit ids the seed assigned.

    Without this the next runtime insert reuses id 1 and fails on the primary key. Silently
    skipped on other dialects, which have no sequences to advance.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return

    for table, rows in plan.items():
        if not rows or "id" not in rows[0]:
            continue
        highest = max(int(row["id"]) for row in rows)
        await session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence(:table, 'id'), :value, true) "
                "WHERE pg_get_serial_sequence(:table, 'id') IS NOT NULL"
            ),
            {"table": table, "value": highest},
        )


async def dataset_counts(session: AsyncSession) -> dict[str, int]:
    """Row counts read back from the database, for verifying a seed actually landed."""
    counts: dict[str, int] = {}
    for table in TABLE_ORDER:
        model = _MODEL_BY_TABLE[table]
        result = await session.execute(select(func.count()).select_from(model))
        counts[table] = int(result.scalar_one())
    return counts
