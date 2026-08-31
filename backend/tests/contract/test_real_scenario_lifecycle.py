"""Scenario lifecycle against migrated PostgreSQL and the real FastAPI application.

These tests never create schema. ``TRAVELOPS_TEST_DATABASE_URL`` must point at a database already
migrated to head, matching the rest of the real-database contract suite.

Owner: Stream A.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select

from app.api.scenarios import _advisory_key
from app.db.seed import reset_demo_dataset
from app.models.cascade import IncidentGroupFlight
from app.models.workflow import DecisionLog, Incident, IncidentGroup
from tests.contract.conftest import clear_workflow
from tests.contract.postgres_support import requires_postgres

pytestmark = [pytest.mark.anyio, requires_postgres]
PREFIX = "/api/v1"


def _payload():
    return {
        "root_cause": "weather",
        "airport_icao": "VOBL",
        "severity": "high",
        "effective_at": "2026-08-20T15:40:00+05:30",
        "actor_id": "postgres-operator",
        "members": [{"flight_id": 1, "role": "primary", "delay_minutes": 420}],
    }


@pytest.fixture
async def authored_scenario_cleanup(sessionmaker_for, seeded) -> AsyncIterator[None]:
    """Remove authored scenario rows before the shared seed fixture removes reference data."""
    yield
    async with sessionmaker_for() as session:
        groups = (
            (
                await session.execute(
                    select(IncidentGroup.id).where(IncidentGroup.reference.like("SCN-%"))
                )
            )
            .scalars()
            .all()
        )
        if groups:
            await clear_workflow(session)
            await session.execute(delete(Incident).where(Incident.group_id.in_(groups)))
            await session.execute(
                delete(IncidentGroupFlight).where(IncidentGroupFlight.incident_group_id.in_(groups))
            )
            await session.execute(delete(IncidentGroup).where(IncidentGroup.id.in_(groups)))
        await session.commit()


async def test_create_persists_timezone_membership_provenance_and_idempotency(
    client, sessionmaker_for, authored_scenario_cleanup
):
    headers = {"Idempotency-Key": "postgres-create"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _index: client.post(f"{PREFIX}/scenarios", json=_payload(), headers=headers),
                range(2),
            )
        )

    assert all(response.status_code == 201 for response in responses)
    references = {response.json()["scenario_reference"] for response in responses}
    assert len(references) == 1
    assert sorted(response.json()["replayed"] for response in responses) == [False, True]
    reference = references.pop()

    async with sessionmaker_for() as session:
        group = (
            await session.execute(select(IncidentGroup).where(IncidentGroup.reference == reference))
        ).scalar_one()
        members = (
            (
                await session.execute(
                    select(IncidentGroupFlight).where(
                        IncidentGroupFlight.incident_group_id == group.id
                    )
                )
            )
            .scalars()
            .all()
        )
        audits = (
            (
                await session.execute(
                    select(DecisionLog).where(
                        DecisionLog.event_type == "SCENARIO_CREATED",
                        DecisionLog.correlation_id == reference,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert group.opened_at.tzinfo is not None
    assert group.opened_at.isoformat() == "2026-08-20T10:10:00+00:00"
    assert group.demo_dataset_id == "bengaluru_storm"
    assert len(members) == 1
    assert members[0].provenance_kind == "simulated"
    assert members[0].source_ref == f"scenario-builder:{reference}"
    assert len(audits) == 1
    assert audits[0].actor == "human"
    assert audits[0].detail["actor_id"] == "postgres-operator"


async def test_start_uses_canonical_workflow_and_cannot_open_twice(
    client, sessionmaker_for, authored_scenario_cleanup
):
    created = client.post(f"{PREFIX}/scenarios", json=_payload()).json()
    reference = created["scenario_reference"]

    first = client.post(
        f"{PREFIX}/scenarios/{reference}/start",
        json={"actor_id": "postgres-operator"},
        headers={"Idempotency-Key": "postgres-start"},
    )
    second = client.post(
        f"{PREFIX}/scenarios/{reference}/start",
        json={"actor_id": "postgres-operator"},
        headers={"Idempotency-Key": "postgres-start-retry"},
    )

    assert first.status_code == second.status_code == 200
    body = first.json()
    assert len(body["opened_incident_ids"]) == 1
    assert body["members"][0]["incident_reference"].startswith("INC-2026-0820-VOBL-")
    assert second.json()["replayed"] is True

    async with sessionmaker_for() as session:
        group_id = await session.scalar(
            select(IncidentGroup.id).where(IncidentGroup.reference == reference)
        )
        incident_count = await session.scalar(
            select(func.count()).select_from(Incident).where(Incident.group_id == group_id)
        )
        incident = (
            await session.execute(select(Incident).where(Incident.group_id == group_id))
        ).scalar_one()
        requested_count = await session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(
                DecisionLog.event_type == "SCENARIO_START_REQUESTED",
                DecisionLog.correlation_id == reference,
            )
        )
        started_count = await session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(
                DecisionLog.event_type == "SCENARIO_STARTED",
                DecisionLog.correlation_id == reference,
            )
        )

    assert incident_count == 1
    assert incident.reference == body["members"][0]["incident_reference"]
    assert incident.state == "detected"
    assert requested_count == 1
    assert started_count == 1

    existing_workflow = client.post(f"{PREFIX}/incidents/{incident.reference}/run")
    assert existing_workflow.status_code == 200
    assert existing_workflow.json()["incident_reference"] == incident.reference
    assert existing_workflow.json()["state"] in {"awaiting_approval", "resolved", "blocked"}


async def test_demo_reset_removes_authored_scenario_membership_incident_and_audit(
    client, sessionmaker_for, authored_scenario_cleanup
):
    created = client.post(f"{PREFIX}/scenarios", json=_payload()).json()
    reference = created["scenario_reference"]
    started = client.post(
        f"{PREFIX}/scenarios/{reference}/start",
        json={"actor_id": "postgres-operator"},
    )
    assert started.status_code == 200

    async with sessionmaker_for() as session:
        await reset_demo_dataset(session)
        await session.commit()

    async with sessionmaker_for() as session:
        group_count = await session.scalar(
            select(func.count())
            .select_from(IncidentGroup)
            .where(IncidentGroup.reference == reference)
        )
        member_count = await session.scalar(
            select(func.count())
            .select_from(IncidentGroupFlight)
            .join(IncidentGroup, IncidentGroupFlight.incident_group_id == IncidentGroup.id)
            .where(IncidentGroup.reference == reference)
        )
        incident_count = await session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.demo_dataset_id == "bengaluru_storm")
        )
        audit_count = await session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(DecisionLog.correlation_id == reference)
        )

    assert group_count == 0
    assert member_count == 0
    assert incident_count == 0
    assert audit_count == 0


async def test_concurrent_overlapping_scenario_starts_leave_only_the_winner(
    client, engine, sessionmaker_for, authored_scenario_cleanup
):
    references = [
        client.post(f"{PREFIX}/scenarios", json=_payload()).json()["scenario_reference"]
        for _index in range(2)
    ]

    gate = Barrier(3)

    def start(reference: str):
        gate.wait(timeout=5)
        return client.post(
            f"{PREFIX}/scenarios/{reference}/start",
            json={"actor_id": "postgres-operator"},
        )

    flight_lock = _advisory_key("scenario-flight:1")
    async with engine.connect() as blocker:
        await blocker.execute(select(func.pg_advisory_lock(flight_lock)))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start, reference) for reference in references]
            gate.wait(timeout=5)
            await asyncio.sleep(0.5)
            both_waited_for_the_flight_claim = all(not future.done() for future in futures)
            await blocker.execute(select(func.pg_advisory_unlock(flight_lock)))
            responses = [future.result(timeout=30) for future in futures]

    assert both_waited_for_the_flight_claim
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner_index = next(
        index for index, response in enumerate(responses) if response.status_code == 200
    )
    loser_index = 1 - winner_index
    winner_reference = references[winner_index]
    loser_reference = references[loser_index]
    assert responses[loser_index].json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    async with sessionmaker_for() as session:
        groups = {
            reference: group_id
            for group_id, reference in (
                await session.execute(
                    select(IncidentGroup.id, IncidentGroup.reference).where(
                        IncidentGroup.reference.in_(references)
                    )
                )
            ).all()
        }
        winner_incidents = await session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.group_id == groups[winner_reference])
        )
        loser_incidents = await session.scalar(
            select(func.count())
            .select_from(Incident)
            .where(Incident.group_id == groups[loser_reference])
        )
        loser_requests = await session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(
                DecisionLog.event_type == "SCENARIO_START_REQUESTED",
                DecisionLog.correlation_id == loser_reference,
            )
        )
        suppressed_opens = await session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(DecisionLog.event_type == "INCIDENT_OPEN_SUPPRESSED")
        )

    assert winner_incidents == 1
    assert loser_incidents == 0
    assert loser_requests == 0
    assert suppressed_opens == 0
