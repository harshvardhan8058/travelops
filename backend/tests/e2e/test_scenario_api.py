"""Scenario lifecycle through the real FastAPI boundary over SQLite.

The PostgreSQL counterpart proves database-specific behavior; these tests keep request validation,
typed errors, audit attribution, idempotency, and workflow handoff fast in the default suite.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.cascade import IncidentGroupFlight
from app.models.workflow import DecisionLog, Incident, IncidentGroup

PREFIX = "/api/v1"


def _scenario_payload(**overrides):
    payload = {
        "root_cause": "weather",
        "airport_icao": "VOBL",
        "severity": "high",
        "effective_at": "2026-08-20T15:40:00Z",
        "actor_id": "operator-1",
        "members": [{"flight_id": 1, "role": "primary", "delay_minutes": 420}],
    }
    payload.update(overrides)
    return payload


async def test_create_persists_declared_membership_and_audit(client, seeded):
    response = client.post(
        f"{PREFIX}/scenarios",
        json=_scenario_payload(),
        headers={"Idempotency-Key": "create-one"},
    )

    assert response.status_code == 201
    body = response.json()
    reference = body["scenario_reference"]
    assert reference.startswith("SCN-20260820-")
    assert body["state"] == "detected"
    assert body["members"] == [
        {
            "flight_id": 1,
            "flight_number": "6E 2134",
            "role": "primary",
            "delay_minutes": 420,
        }
    ]
    assert body["created_by"] == "operator-1"
    assert body["provenance"] == {
        "kind": "simulated",
        "provider": "scenario-builder",
        "source_ref": f"scenario-builder:{reference}",
    }
    assert response.headers["X-Correlation-Id"]

    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        group = (
            await session.execute(select(IncidentGroup).where(IncidentGroup.reference == reference))
        ).scalar_one()
        member = (
            await session.execute(
                select(IncidentGroupFlight).where(IncidentGroupFlight.incident_group_id == group.id)
            )
        ).scalar_one()
        audit = (
            await session.execute(
                select(DecisionLog).where(
                    DecisionLog.event_type == "SCENARIO_CREATED",
                    DecisionLog.correlation_id == reference,
                )
            )
        ).scalar_one()

    assert group.demo_dataset_id == "bengaluru_storm"
    assert member.provenance_kind == "simulated"
    assert member.source_ref == f"scenario-builder:{reference}"
    assert audit.actor == "human"
    assert audit.detail["actor_id"] == "operator-1"
    assert audit.detail["idempotency_digest"] != "create-one"


async def test_create_idempotency_replays_without_a_second_group(client, seeded):
    headers = {"Idempotency-Key": "stable-create-key"}
    first = client.post(f"{PREFIX}/scenarios", json=_scenario_payload(), headers=headers)
    second = client.post(f"{PREFIX}/scenarios", json=_scenario_payload(), headers=headers)

    assert first.status_code == second.status_code == 201
    assert second.json()["scenario_reference"] == first.json()["scenario_reference"]
    assert second.json()["replayed"] is True

    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(IncidentGroup)
            .where(IncidentGroup.reference.like("SCN-%"))
        )
    assert count == 1


def test_create_idempotency_key_is_bound_to_the_request(client):
    headers = {"Idempotency-Key": "request-bound-key"}
    first = client.post(f"{PREFIX}/scenarios", json=_scenario_payload(), headers=headers)
    changed = client.post(
        f"{PREFIX}/scenarios",
        json=_scenario_payload(severity="low"),
        headers=headers,
    )

    assert first.status_code == 201
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert changed.json()["error"]["details"] == {"idempotency_key_reused": True}


def test_declared_delay_must_match_the_recorded_flight_state(client):
    response = client.post(
        f"{PREFIX}/scenarios",
        json=_scenario_payload(members=[{"flight_id": 1, "role": "primary", "delay_minutes": 30}]),
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["members"] == [
        {
            "flight_id": 1,
            "flight_number": "6E 2134",
            "declared_delay_minutes": 30,
            "recorded_delay_minutes": 420,
        }
    ]


async def test_invalid_database_references_write_nothing(client, seeded):
    response = client.post(
        f"{PREFIX}/scenarios",
        json=_scenario_payload(
            members=[{"flight_id": 9999, "role": "primary", "delay_minutes": 5}]
        ),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ENTITY_NOT_FOUND"
    assert response.json()["error"]["details"] == {"flight_ids": [9999]}

    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(IncidentGroup)
            .where(IncidentGroup.reference.like("SCN-%"))
        )
    assert count == 0


def test_validation_errors_use_the_typed_envelope(client):
    response = client.post(
        f"{PREFIX}/scenarios",
        json=_scenario_payload(
            effective_at="2026-08-20T15:40:00",
            members=[{"flight_id": 1, "role": "affected_departure", "delay_minutes": 5}],
        ),
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["correlation_id"]
    assert error["details"]["errors"]


def test_member_role_must_match_the_root_airport(client):
    response = client.post(
        f"{PREFIX}/scenarios",
        json=_scenario_payload(airport_icao="VIDP"),
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["airport_icao"] == "VIDP"
    assert error["details"]["members"][0]["expected_airport_icao"] == "VOBL"


async def test_start_opens_existing_workflow_once_and_records_human_attribution(client, seeded):
    created = client.post(f"{PREFIX}/scenarios", json=_scenario_payload()).json()
    reference = created["scenario_reference"]

    first = client.post(
        f"{PREFIX}/scenarios/{reference}/start",
        json={"actor_id": "operator-2"},
        headers={"Idempotency-Key": "start-one"},
    )
    second = client.post(
        f"{PREFIX}/scenarios/{reference}/start",
        json={"actor_id": "operator-2"},
        headers={"Idempotency-Key": "a-different-key"},
    )

    assert first.status_code == second.status_code == 200
    body = first.json()
    assert body["scenario_reference"] == reference
    assert len(body["opened_incident_ids"]) == 1
    assert body["members"][0]["incident_reference"].startswith("INC-2026-0820-VOBL-")
    assert body["members"][0]["state"] == "detected"
    assert body["started_by"] == "operator-2"
    assert second.json()["replayed"] is True
    assert second.json()["opened_incident_ids"] == body["opened_incident_ids"]

    incident_response = client.get(f"{PREFIX}/incidents/{body['members'][0]['incident_reference']}")
    assert incident_response.status_code == 200
    assert incident_response.json()["state"] == "detected"

    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        incident_count = await session.scalar(
            select(func.count())
            .select_from(Incident)
            .join(IncidentGroup, Incident.group_id == IncidentGroup.id)
            .where(IncidentGroup.reference == reference)
        )
        start_entries = (
            (
                await session.execute(
                    select(DecisionLog).where(
                        DecisionLog.event_type == "SCENARIO_STARTED",
                        DecisionLog.correlation_id == reference,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert incident_count == 1
    assert len(start_entries) == 1
    assert start_entries[0].actor == "human"
    assert start_entries[0].detail["actor_id"] == "operator-2"


async def test_start_rejects_an_active_incident_owned_by_another_workflow(client, seeded):
    created = client.post(f"{PREFIX}/scenarios", json=_scenario_payload()).json()
    reference = created["scenario_reference"]

    factory = async_sessionmaker(bind=seeded, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        session.add(
            Incident(
                reference="INC-EXISTING-WORKFLOW",
                group_id=None,
                flight_id=1,
                trigger_type="weather",
                severity="high",
                state="detected",
                opened_at=datetime(2026, 8, 20, 15, 40, tzinfo=UTC),
                demo_dataset_id="bengaluru_storm",
            )
        )
        await session.commit()

    response = client.post(f"{PREFIX}/scenarios/{reference}/start", json={"actor_id": "operator-2"})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "INVALID_STATE_TRANSITION"
    assert error["details"]["conflicts"][0]["incident_reference"] == ("INC-EXISTING-WORKFLOW")

    async with factory() as session:
        group_id = await session.scalar(
            select(IncidentGroup.id).where(IncidentGroup.reference == reference)
        )
        scenario_incidents = await session.scalar(
            select(func.count()).select_from(Incident).where(Incident.group_id == group_id)
        )
        started = await session.scalar(
            select(func.count())
            .select_from(DecisionLog)
            .where(
                DecisionLog.event_type == "SCENARIO_STARTED",
                DecisionLog.correlation_id == reference,
            )
        )
    assert scenario_incidents == 0
    assert started == 0


def test_unknown_scenario_cannot_start(client):
    response = client.post(
        f"{PREFIX}/scenarios/SCN-NOT-FOUND/start", json={"actor_id": "operator-1"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["details"] == {"scenario_reference": "SCN-NOT-FOUND"}


def test_openapi_contains_only_the_two_new_typed_operations(client):
    spec = client.get("/openapi.json").json()
    create = spec["paths"][f"{PREFIX}/scenarios"]["post"]
    start = spec["paths"][f"{PREFIX}/scenarios/{{scenario_reference}}/start"]["post"]

    assert create["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ScenarioCreateResponse"
    }
    assert start["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ScenarioStartResponse"
    }
    assert create["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ScenarioCreateRequest"
    }
    assert start["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ScenarioStartRequest"
    }
    assert {parameter["name"] for parameter in create["parameters"]} == {"Idempotency-Key"}
    assert {parameter["name"] for parameter in start["parameters"]} == {
        "scenario_reference",
        "Idempotency-Key",
    }
