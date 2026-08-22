"""Operational CLI.

The Makefile targets call these subcommands. Between them they are the whole demo control
surface: seed the fixed dataset, inject the scenario, reset it, and dump the OpenAPI document.

The seed and reset internals belong to Stream C (`app/db/seed.py`); this only drives them and
owns the transaction. Injection is Stream A's, because opening an incident is workflow.

Owner: Stream A.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import configure_logging, get_logger

log = get_logger(__name__)


def cmd_openapi() -> int:
    """Print the OpenAPI document. `make openapi` writes it to docs/openapi.json."""
    from app.main import app

    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


async def _with_session(work) -> int:
    """Run one unit of work in a session, committing only on success.

    Stream C's seed helpers flush but never commit, deliberately: they run inside the
    caller's transaction so a failure part-way cannot leave a half-built dataset behind.
    Honouring that is this function's whole job.
    """
    from app.db.session import dispose_engine, get_sessionmaker

    factory = get_sessionmaker()
    try:
        async with factory() as session:
            try:
                code = await work(session)
                await session.commit()
                return code
            except Exception:
                await session.rollback()
                raise
    finally:
        await dispose_engine()


def _refuse_outside_demo() -> None:
    """Destructive helpers are refused outside development and demo."""
    from app.config import AppEnv, get_settings
    from app.errors import DemoActionForbidden

    env = get_settings().app_env
    if env not in {AppEnv.development, AppEnv.demo, AppEnv.test}:
        raise DemoActionForbidden(
            f"refusing to modify demo data with APP_ENV={env.value}",
            details={"app_env": env.value, "allowed": ["development", "demo", "test"]},
        )


# ------------------------------------------------------------------------------ seed/reset


async def _seed(session: AsyncSession) -> int:
    from app.db.seed import plan_digest, seed_demo_dataset

    _refuse_outside_demo()
    report = await seed_demo_dataset(session)
    print(report.summary())
    # The digest is what makes "byte-identical across runs" checkable rather than claimed.
    print(f"plan digest: {plan_digest()}")
    return 0


async def _clear_workflow_records(session: AsyncSession) -> dict[str, int]:
    """Remove what the orchestrator wrote for demo incidents, children first.

    Stream C's `reset_demo_dataset` clears what its seed created, which correctly includes
    `incident` but not the workflow rows a run produces — those are Stream A's output, not
    seed data, so they are not in its table order.

    Without this, a second `make demo-reset` deletes an incident that a `decision_log` row
    still references. SQLite does not enforce foreign keys by default so it appears to work
    and leaves orphans; Postgres, which is what the demo runs on, rejects the delete outright.

    The split is deliberate rather than a workaround: each stream clears what it created.
    """
    from sqlalchemy import delete

    from app.db.seed import DEMO_DATASET_ID
    from app.models.workflow import (
        Action,
        AssuranceEvaluation,
        DecisionLog,
        HotelReservation,
        HumanDecision,
        Incident,
        IncidentOutcome,
        Notification,
        Plan,
        Prediction,
    )
    from app.models.workflow import PlanTask as PlanTaskRow

    incident_ids = list(
        (
            await session.execute(
                select(Incident.id).where(Incident.demo_dataset_id == DEMO_DATASET_ID)
            )
        ).scalars()
    )
    removed: dict[str, int] = {}
    if not incident_ids:
        return removed

    plan_ids = list(
        (await session.execute(select(Plan.id).where(Plan.incident_id.in_(incident_ids)))).scalars()
    )
    task_ids = (
        list(
            (
                await session.execute(
                    select(PlanTaskRow.id).where(PlanTaskRow.plan_id.in_(plan_ids))
                )
            ).scalars()
        )
        if plan_ids
        else []
    )
    evaluation_ids = (
        list(
            (
                await session.execute(
                    select(AssuranceEvaluation.id).where(
                        AssuranceEvaluation.plan_task_id.in_(task_ids)
                    )
                )
            ).scalars()
        )
        if task_ids
        else []
    )
    action_ids = (
        list(
            (
                await session.execute(select(Action.id).where(Action.plan_task_id.in_(task_ids)))
            ).scalars()
        )
        if task_ids
        else []
    )

    # The prediction is referenced by the incident, so the link goes before the row.
    prediction_ids = list(
        (
            await session.execute(
                select(Incident.prediction_id).where(
                    Incident.id.in_(incident_ids), Incident.prediction_id.is_not(None)
                )
            )
        ).scalars()
    )
    if prediction_ids:
        await session.execute(
            Incident.__table__.update()
            .where(Incident.id.in_(incident_ids))
            .values(prediction_id=None)
        )

    # Children before parents. Anything referencing an action must go before the action.
    plan_of_deletion: list[tuple[str, Any]] = [
        ("notification", delete(Notification).where(Notification.action_id.in_(action_ids))),
        (
            "hotel_reservation",
            delete(HotelReservation).where(HotelReservation.action_id.in_(action_ids)),
        ),
        ("action", delete(Action).where(Action.id.in_(action_ids))),
        (
            "human_decision",
            delete(HumanDecision).where(HumanDecision.assurance_id.in_(evaluation_ids)),
        ),
        (
            "assurance_evaluation",
            delete(AssuranceEvaluation).where(AssuranceEvaluation.id.in_(evaluation_ids)),
        ),
        ("plan_task", delete(PlanTaskRow).where(PlanTaskRow.id.in_(task_ids))),
        ("plan", delete(Plan).where(Plan.id.in_(plan_ids))),
        (
            "incident_outcome",
            delete(IncidentOutcome).where(IncidentOutcome.incident_id.in_(incident_ids)),
        ),
        ("decision_log", delete(DecisionLog).where(DecisionLog.incident_id.in_(incident_ids))),
        ("prediction", delete(Prediction).where(Prediction.id.in_(prediction_ids))),
    ]
    for table, statement in plan_of_deletion:
        count = (await session.execute(statement)).rowcount or 0
        if count:
            removed[table] = count
    await session.flush()
    return removed


async def _reset(session: AsyncSession) -> int:
    from app.db.seed import reset_demo_dataset

    _refuse_outside_demo()
    workflow = await _clear_workflow_records(session)
    report = await reset_demo_dataset(session)
    print(report.summary())
    if workflow:
        print("  workflow records removed first:")
        for table, count in sorted(workflow.items()):
            print(f"    {table:30} {count:6}")
    return 0


def cmd_seed() -> int:
    return asyncio.run(_with_session(_seed))


def cmd_reset() -> int:
    return asyncio.run(_with_session(_reset))


# --------------------------------------------------------------------------------- inject


async def _select_primary_flight(session: AsyncSession, airport_icao: str) -> tuple[int, str, int]:
    """Choose the worst-affected departure from the disrupted airport.

    Deterministic and derived, not hardcoded: the flight with the largest recorded delay,
    tie-broken by scheduled departure then id. Phase 1 of the flow works one child flight;
    the eight-flight cascade is Phase 2 and shares the same incident group.

    Returns (flight_id, flight_number, delay_minutes).
    """
    from app.models.reference import Flight

    stmt = (
        select(Flight)
        .where(Flight.origin_icao == airport_icao, Flight.estimated_departure.is_not(None))
        .order_by(Flight.scheduled_departure, Flight.id)
    )
    candidates: list[tuple[int, int, str]] = []
    for flight in (await session.execute(stmt)).scalars():
        scheduled = _utc(flight.scheduled_departure)
        estimated = _utc(flight.estimated_departure)
        delay = max(0, int((estimated - scheduled).total_seconds() // 60))
        if delay > 0:
            candidates.append((delay, flight.id, flight.flight_number))
    if not candidates:
        from app.errors import EntityNotFound

        raise EntityNotFound(
            f"no delayed departure found at {airport_icao}; run `make seed` first",
            details={"airport_icao": airport_icao},
        )
    delay, flight_id, flight_number = max(candidates, key=lambda item: (item[0], -item[1]))
    return flight_id, flight_number, delay


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _inject(session: AsyncSession, scenario: str, *, cascade: bool = False) -> int:
    """Open the scenario's incident, idempotently.

    The scenario clock comes from the seeded `incident_group.opened_at`, which Stream C
    anchors to the fixture's `injected_at`. Reading it from the row rather than importing
    `data/` keeps `app/` free of a dependency on the generators, and means there is one
    value rather than two that can drift.

    That timestamp then flows into the incident's `opened_at`, and from there into the
    Delay Risk `as_of` — which is what makes the storm score against the observation that
    was current when it hit, rather than against a later clear-weather METAR.
    """
    from app.db.seed import DEMO_DATASET_ID, INCIDENT_GROUP_REFERENCE
    from app.errors import EntityNotFound
    from app.events.bus import get_event_bus
    from app.models.workflow import IncidentGroup
    from app.orchestrator.engine import Orchestrator
    from app.orchestrator.service_registry import register_stage2_services

    _refuse_outside_demo()

    if scenario != DEMO_DATASET_ID:
        raise EntityNotFound(
            f"unknown scenario '{scenario}'",
            details={"allowed": [DEMO_DATASET_ID]},
        )

    group = (
        (
            await session.execute(
                select(IncidentGroup).where(IncidentGroup.reference == INCIDENT_GROUP_REFERENCE)
            )
        )
        .scalars()
        .first()
    )
    if group is None:
        raise EntityNotFound(
            f"scenario group {INCIDENT_GROUP_REFERENCE} not found; run `make seed` first",
            details={"scenario": scenario},
        )

    registered = register_stage2_services()
    injected_at = _utc(group.opened_at)

    bus = None
    try:
        bus = get_event_bus()
    except Exception as exc:
        print(
            f"note: event bus unavailable ({type(exc).__name__}); the decision log is authoritative"
        )

    # The scenario time is passed as `opened_at`, not as the orchestrator's clock. It says
    # when the disruption happened; it must not backdate the audit entries that record when
    # this command actually ran.
    orchestrator = Orchestrator(session, bus=bus)

    print(f"scenario     {scenario}")
    print(f"group        {group.reference} at {group.airport_icao}")
    print(f"injected_at  {injected_at.isoformat()}")

    if cascade:
        # Membership is declared data, so the cascade opens exactly the flights
        # `incident_group_flight` names. Deriving it from `origin_icao` would return seven of
        # the eight, because UK 705 arrives into VOBL rather than departing from it.
        from app.orchestrator.group import GroupOrchestrator

        ctx_group = await GroupOrchestrator(session, orchestrator=orchestrator).open_group(
            group.id, opened_at=injected_at
        )
        print(f"members      {len(ctx_group.members)} declared flights")
        for member in ctx_group.members:
            state = member.state.value if member.state else "-"
            print(
                f"  {member.role:9} {member.flight_number:9} "
                f"{member.incident_reference or '-':28} {state}"
            )
        print(f"group state  {ctx_group.state.value} (derived from members)")
        print(f"services     {len(registered)} dispatchable: {', '.join(registered)}")
        print()
        print(f"Next: POST /api/v1/incident-groups/{group.reference}/run")
        return 0

    flight_id, flight_number, delay = await _select_primary_flight(session, group.airport_icao)
    ctx = await orchestrator.open_incident(
        flight_id,
        group.root_cause,
        severity=group.severity,
        group_id=group.id,
        demo_dataset_id=DEMO_DATASET_ID,
        evidence_refs=[f"fixture:{scenario}:weather:{group.airport_icao}"],
        opened_at=injected_at,
    )

    print(f"flight       {flight_number} (id {flight_id}), delayed {delay} min")
    print(f"incident     {ctx.incident_reference} in '{ctx.state.value}'")
    print(f"services     {len(registered)} dispatchable: {', '.join(registered)}")
    print()
    print(f"Next: POST /api/v1/incidents/{ctx.incident_reference}/run")
    return 0


def cmd_inject(scenario: str, *, cascade: bool = False) -> int:
    return asyncio.run(_with_session(lambda session: _inject(session, scenario, cascade=cascade)))


async def _demo_reset(session: AsyncSession, scenario: str, *, cascade: bool = False) -> int:
    """Reset only demo-owned records, re-seed, then re-inject."""
    from app.db.seed import seed_demo_dataset

    _refuse_outside_demo()
    # Clear the orchestrator's output before the seed clears its own rows, or the incident
    # delete inside seed_demo_dataset trips a foreign key on Postgres.
    workflow = await _clear_workflow_records(session)
    if workflow:
        print(f"removed {sum(workflow.values())} workflow records from the previous run")
    report = await seed_demo_dataset(session, reset=True)
    print(report.summary())
    # Flush so the re-seeded rows are visible to the injection in this same transaction.
    await session.flush()
    return await _inject(session, scenario, cascade=cascade)


def cmd_demo_reset(scenario: str, *, cascade: bool = False) -> int:
    return asyncio.run(
        _with_session(lambda session: _demo_reset(session, scenario, cascade=cascade))
    )


# ------------------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="travelops")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("openapi", help="print the OpenAPI document")
    sub.add_parser("seed", help="seed the fixed-seed demo dataset")
    sub.add_parser("reset", help="remove demo-owned records (development only)")

    demo_reset = sub.add_parser("demo-reset", help="reset demo-owned records and re-inject")
    demo_reset.add_argument("--scenario", default="bengaluru_storm")
    demo_reset.add_argument(
        "--cascade",
        action="store_true",
        help="open every declared member flight, not just the primary",
    )

    inject = sub.add_parser("inject", help="inject a demo scenario")
    inject.add_argument("--scenario", default="bengaluru_storm")
    inject.add_argument(
        "--cascade",
        action="store_true",
        help="open one incident per declared member flight (the network event)",
    )

    args = parser.parse_args(argv)

    if args.command != "openapi":
        from app.config import get_settings

        configure_logging(get_settings().log_level, json_output=False)

    try:
        match args.command:
            case "openapi":
                return cmd_openapi()
            case "seed":
                return cmd_seed()
            case "reset":
                return cmd_reset()
            case "demo-reset":
                return cmd_demo_reset(args.scenario, cascade=args.cascade)
            case "inject":
                return cmd_inject(args.scenario, cascade=args.cascade)
            case _:
                parser.error(f"unknown command {args.command}")
                return 2
    except Exception as exc:
        # A CLI failure states the reason and exits non-zero. `make` must not appear to pass.
        code = getattr(exc, "code", type(exc).__name__)
        print(f"error [{code}]: {exc}", file=sys.stderr)
        details = getattr(exc, "details", None)
        if details:
            print(f"details: {json.dumps(details, default=str)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
