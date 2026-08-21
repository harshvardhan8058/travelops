"""The complete bengaluru_storm recovery, over HTTP, against the real seeded dataset.

This is the Stage 2 vertical slice end to end: seed the committed dataset, inject the
scenario, drive the workflow through the API, approve the one action the gate holds, and
reach a terminal state — with no model involved anywhere.

Every number asserted here is computed from seeded records by Stream B's gate and Stream C's
services. None is a constant chosen to make the test pass; where a figure appears (80, 174,
420, 8 of 10) it is what the deterministic code derives, and changing the data or a rule
should change it.

The dataset is seeded once into a file-backed SQLite database for the module, because
building it is 2,083 rows and the tests only read it.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.db.base import Base
from app.db.seed import seed_demo_dataset
from app.db.session import get_session
from app.main import app
from app.models.workflow import Action, Incident, Prediction
from app.orchestrator import dispatch
from app.orchestrator.service_registry import register_stage2_services

PREFIX = "/api/v1"

#: The fixture's anchor: docs and data/fixtures/bengaluru_storm.yaml both state
#: 2026-08-20T15:36:00Z, and Stream C's seed puts it on the incident group.
EXPECTED_REFERENCE = "INC-2026-0820-VOBL-01"


@pytest.fixture(scope="module")
def storm_template(tmp_path_factory) -> Path:
    """Seed the committed dataset once, as a template to copy per test.

    Seeding is 2,083 rows; copying a SQLite file is instant. Every test then gets a pristine
    database, which matters because these tests mutate it — one that resolves the incident
    frees the flight and the next injection would open a second one. Sharing the database
    would make the suite order-dependent, and an order-dependent test that passes today is
    just a failure waiting for someone to add a case above it.
    """
    path = tmp_path_factory.mktemp("storm") / "template.sqlite"

    async def build() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        async with factory() as session:
            await seed_demo_dataset(session)
            await session.commit()
        await engine.dispose()

    asyncio.run(build())
    assert path.exists()
    return path


@pytest.fixture
def sessionmaker_for(storm_template, tmp_path):
    fresh = tmp_path / "storm.sqlite"
    shutil.copy(storm_template, fresh)
    engine = create_async_engine(f"sqlite+aiosqlite:///{fresh}")
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def storm_client(sessionmaker_for):
    """The real app over the seeded database, with the services registered."""
    dispatch.SERVICE_REGISTRY.clear()
    register_stage2_services()

    async def override():
        async with sessionmaker_for() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()
    dispatch.SERVICE_REGISTRY.clear()


@pytest.fixture
def injected(sessionmaker_for) -> str:
    """Inject the scenario exactly as `make demo` does, returning the incident reference."""
    from app.cli import _inject

    async def run() -> str:
        async with sessionmaker_for() as session:
            await _inject(session, "bengaluru_storm")
            await session.commit()
            incident = (
                (await session.execute(select(Incident).order_by(Incident.id))).scalars().first()
            )
            return incident.reference

    return asyncio.run(run())


async def _count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# ------------------------------------------------------------------------------- injection


class TestInjection:
    def test_injecting_opens_exactly_one_incident(self, sessionmaker_for, injected):
        async def check() -> int:
            async with sessionmaker_for() as session:
                return await _count(session, Incident)

        assert injected == EXPECTED_REFERENCE
        assert asyncio.run(check()) == 1

    def test_injecting_twice_still_opens_exactly_one(self, sessionmaker_for, injected):
        """The Stage 2 readiness gate, through the code path the demo actually runs."""
        from app.cli import _inject

        async def again() -> int:
            async with sessionmaker_for() as session:
                await _inject(session, "bengaluru_storm")
                await session.commit()
                return await _count(session, Incident)

        assert asyncio.run(again()) == 1

    def test_the_incident_attaches_to_the_seeded_group(self, sessionmaker_for, injected):
        async def check():
            async with sessionmaker_for() as session:
                incident = (
                    (await session.execute(select(Incident).where(Incident.reference == injected)))
                    .scalars()
                    .one()
                )
                return incident.group_id, incident.demo_dataset_id, incident.opened_at

        group_id, dataset_id, opened_at = asyncio.run(check())
        assert group_id is not None
        # Tagged, so `make demo-reset` can scope its delete and leave nothing orphaned.
        assert dataset_id == "bengaluru_storm"
        # opened_at is the scenario's own clock, not the moment the command ran.
        assert opened_at.replace(tzinfo=None).isoformat().startswith("2026-08-20T15:36")

    def test_the_primary_flight_is_derived_not_hardcoded(self, sessionmaker_for):
        """The worst-affected departure, chosen by recorded delay."""
        from app.cli import _select_primary_flight

        async def pick():
            async with sessionmaker_for() as session:
                return await _select_primary_flight(session, "VOBL")

        flight_id, flight_number, delay = asyncio.run(pick())
        assert flight_number == "6E 2134"
        assert delay == 420
        assert flight_id > 0

    def test_an_unknown_scenario_is_refused(self, sessionmaker_for):
        from app.cli import _inject
        from app.errors import EntityNotFound

        async def run():
            async with sessionmaker_for() as session:
                await _inject(session, "not_a_scenario")

        with pytest.raises(EntityNotFound):
            asyncio.run(run())


# ------------------------------------------------------------------------ the recovery run


class TestRecoveryRun:
    def test_the_run_reaches_a_terminal_state_after_one_approval(
        self, storm_client, sessionmaker_for, injected
    ):
        """open → assess → plan → assure → execute → approve → execute → resolved."""
        first = storm_client.post(f"{PREFIX}/incidents/{injected}/run").json()
        assert first["state"] == "awaiting_approval"
        assert first["is_terminal"] is False

        assurance = storm_client.get(f"{PREFIX}/incidents/{injected}/assurance").json()
        pending = [e for e in assurance["evaluations"] if e["decision"] == "needs_human"]
        assert len(pending) == 1
        assert pending[0]["action_type"] == "notify_passengers"

        approved = storm_client.post(
            f"{PREFIX}/assurance/{pending[0]['id']}/decision",
            json={"decision": "approved", "reason": "confirmed against the ops board"},
        )
        assert approved.status_code == 200

        second = storm_client.post(f"{PREFIX}/incidents/{injected}/run").json()
        assert second["state"] == "resolved"
        assert second["is_terminal"] is True

    def test_no_model_was_involved(self, storm_client, injected):
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        plan = storm_client.get(f"{PREFIX}/incidents/{injected}").json()["plan"]

        assert plan["generator"] == "fallback-playbook"
        assert plan["prompt_version"] is None
        assert plan["model_self_report"] is None

    def test_the_risk_index_is_derived_and_explainable(self, storm_client, injected):
        """An index with named factors and their observed values — not a probability."""
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        risk = storm_client.get(f"{PREFIX}/incidents/{injected}").json()["evidence"]["risk"]

        assert risk["risk_index"] == 80
        assert risk["risk_level"] == "severe"
        assert risk["rule_version"] == "delay-risk-v1"

        factors = {f["name"]: f for f in risk["factors"]}
        # The observed figures, so a reader can check the factor against the observation.
        assert factors["visibility_low_visibility_procedures"]["value"] == "800.0"
        assert factors["ceiling_low"]["value"] == "900.0"
        assert factors["wind_moderate"]["value"] == "24.0"
        # And the contribution, so 80 is explainable rather than asserted.
        assert sum(f["points"] for f in risk["factors"]) >= 80

    def test_the_risk_used_the_scenario_clock_not_the_wall_clock(self, storm_client, injected):
        """The archive holds a later clear-weather VOBL report.

        Scoring against "the newest observation now" would rate this storm at zero. The
        assessment is anchored to the incident, so it reasons from what was current when the
        disruption happened. This assertion is the regression guard for that.
        """
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        entries = storm_client.get(f"{PREFIX}/incidents/{injected}/timeline").json()["entries"]

        scored = next(e for e in entries if e["event_type"] == "HIGH_RISK_DELAY")
        assert scored["detail"]["as_of"].startswith("2026-08-20T15:36")
        assert scored["detail"]["observation_age_minutes"] == 0
        assert scored["detail"]["is_stale"] is False

    def test_the_threshold_crossing_is_recorded(self, storm_client, injected):
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        entries = storm_client.get(f"{PREFIX}/incidents/{injected}/timeline").json()["entries"]

        assert any(e["event_type"] == "HIGH_RISK_DELAY" for e in entries)

    def test_the_prediction_is_persisted_and_linked(self, storm_client, sessionmaker_for, injected):
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")

        async def check():
            async with sessionmaker_for() as session:
                incident = (
                    (await session.execute(select(Incident).where(Incident.reference == injected)))
                    .scalars()
                    .one()
                )
                return incident.prediction_id, await _count(session, Prediction)

        prediction_id, count = asyncio.run(check())
        assert prediction_id is not None
        assert count == 1

    def test_affected_passengers_come_from_booking_records(self, storm_client, injected):
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        body = storm_client.get(f"{PREFIX}/incidents/{injected}").json()

        assert body["flight"]["passengers"] == 174
        assert body["evidence"]["affected_entities"]["passengers"] == 174
        assert body["flight"]["delay_minutes"] == 420


# --------------------------------------------------------------------- the services really ran


class TestRealServiceResults:
    def _resolve(self, client, reference) -> dict:
        client.post(f"{PREFIX}/incidents/{reference}/run")
        assurance = client.get(f"{PREFIX}/incidents/{reference}/assurance").json()
        for evaluation in assurance["evaluations"]:
            if evaluation["decision"] == "needs_human":
                client.post(
                    f"{PREFIX}/assurance/{evaluation['id']}/decision",
                    json={"decision": "approved", "reason": "confirmed"},
                )
        client.post(f"{PREFIX}/incidents/{reference}/run")
        return client.get(f"{PREFIX}/incidents/{reference}").json()

    def test_every_task_executed_through_a_real_service(self, storm_client, injected):
        body = self._resolve(storm_client, injected)
        actions = {a["action_type"]: a for a in body["actions"]}

        assert set(actions) == {"check_connections", "assess_crew_impact", "notify_passengers"}
        for action in actions.values():
            assert action["status"] == "success"
            assert "SERVICE_NOT_IMPLEMENTED" not in action["reason"]

    def test_connection_results_are_computed_from_itineraries(self, storm_client, injected):
        body = self._resolve(storm_client, injected)
        reason = next(
            a["reason"] for a in body["actions"] if a["action_type"] == "check_connections"
        )
        # Derived by Stream C from booking segments, scoped to the incident's flight.
        assert "8 itineraries no longer feasible" in reason
        assert "10 connecting itineraries examined" in reason

    def test_crew_impact_names_affected_rotations(self, storm_client, injected):
        body = self._resolve(storm_client, injected)
        reason = next(
            a["reason"] for a in body["actions"] if a["action_type"] == "assess_crew_impact"
        )
        assert "crew rotations at risk" in reason

    def test_notifications_are_honest_about_real_versus_simulated(self, storm_client, injected):
        """Three real and 177 simulated is fine; implying 180 were delivered is not."""
        body = self._resolve(storm_client, injected)
        reason = next(
            a["reason"] for a in body["actions"] if a["action_type"] == "notify_passengers"
        )
        assert "0 real and 174 simulated" in reason

    def test_the_approved_action_references_its_human_decision(self, storm_client, injected):
        body = self._resolve(storm_client, injected)
        notify = next(a for a in body["actions"] if a["action_type"] == "notify_passengers")

        assert notify["human_decision_id"] is not None
        assert notify["assurance_id"] is not None

    def test_no_action_exists_without_an_authorisation(self, storm_client, injected):
        body = self._resolve(storm_client, injected)
        for action in body["actions"]:
            assert action["assurance_id"] is not None
            assert action["idempotency_key"]

    def test_the_gate_held_the_bulk_effect_on_risk_tier_alone(self, storm_client, injected):
        """`notify_passengers` must be held because of what it does, not a stale source.

        Every check passes and it is still `needs_human`, because the action is high risk and
        `high_risk_requires_human` is set. That is the gate working as designed, and it is a
        different claim from "some check failed".
        """
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        assurance = storm_client.get(f"{PREFIX}/incidents/{injected}/assurance").json()
        by_action = {e["action_type"]: e for e in assurance["evaluations"]}

        notify = by_action["notify_passengers"]
        assert notify["decision"] == "needs_human"
        assert notify["risk_tier"] == "high"
        # Every check passes, so nothing is wrong with the evidence...
        assert all(check["state"] == "PASS" for check in notify["checks"])
        # ...and `action_risk` is still named as the blocking reason, which is what tells the
        # operator this is held for what the action does rather than for a data problem.
        assert notify["blocking"] == ["action_risk"]
        risk_check = next(c for c in notify["checks"] if c["name"] == "action_risk")
        assert risk_check["state"] == "PASS"
        assert risk_check["tier"] == "high"

        for low_risk in ("check_connections", "assess_crew_impact"):
            assert by_action[low_risk]["decision"] == "execute"
            assert by_action[low_risk]["risk_tier"] == "low"

    def test_sources_are_fresh_against_the_incident_clock(self, storm_client, injected):
        """The observation the system reasoned from was current when the incident opened."""
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        assurance = storm_client.get(f"{PREFIX}/incidents/{injected}/assurance").json()

        for evaluation in assurance["evaluations"]:
            freshness = next(c for c in evaluation["checks"] if c["name"] == "sources_fresh")
            assert freshness["state"] == "PASS", freshness


# ---------------------------------------------------------------------------------- record


class TestTheRecord:
    def test_the_run_is_reconstructable_in_order(self, storm_client, injected):
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        entries = storm_client.get(f"{PREFIX}/incidents/{injected}/timeline").json()["entries"]

        types = [e["event_type"] for e in entries]
        assert types[0] == "INCIDENT_OPENED"
        for expected in (
            "HIGH_RISK_DELAY",
            "PLAN_PROPOSED",
            "ASSURANCE_EVALUATED",
            "ACTION_COMPLETED",
            "STATE_CHANGED",
        ):
            assert expected in types, f"{expected} missing"
        assert [e["id"] for e in entries] == sorted(e["id"] for e in entries)

    def test_every_entry_carries_the_same_correlation_id_within_a_request(
        self, storm_client, injected
    ):
        before = storm_client.get(f"{PREFIX}/incidents/{injected}/timeline").json()["entries"]
        highest = max((e["id"] for e in before), default=0)

        storm_client.post(
            f"{PREFIX}/incidents/{injected}/run", headers={"X-Correlation-Id": "storm-run-1"}
        )
        entries = storm_client.get(f"{PREFIX}/incidents/{injected}/timeline").json()["entries"]

        # Everything the request produced, rather than everything except one event type:
        # injection also records that the event bus was unreachable, under its own ID.
        during_run = [e for e in entries if e["id"] > highest]
        assert during_run
        assert {e["correlation_id"] for e in during_run} == {"storm-run-1"}

    def test_audit_timestamps_are_real_not_backdated(self, storm_client, injected):
        """`opened_at` is the scenario's clock; the log records when the step actually ran.

        Backdating an audit entry to make a replay look tidy would corrupt the one record
        this system asks to be trusted.
        """
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        body = storm_client.get(f"{PREFIX}/incidents/{injected}").json()
        entries = storm_client.get(f"{PREFIX}/incidents/{injected}/timeline").json()["entries"]

        assert body["opened_at"].startswith("2026-08-20T15:36")
        assert all(not e["occurred_at"].startswith("2026-08-20T15:36") for e in entries)

    def test_the_state_rail_follows_the_canonical_spine(self, storm_client, injected):
        """The rail is the state machine's shape, not a chronology.

        `assuring` and `executing` are re-entered once per task, so first-reached times are
        deliberately not monotonic along the spine. What must hold is that the spine is the
        canonical sequence and that every timestamp on it comes from one clock — the earlier
        bug was `detected` reading from `opened_at` while everything else read from the log,
        which showed as a 23-hour gap that never happened.
        """
        storm_client.post(f"{PREFIX}/incidents/{injected}/run")
        rail = storm_client.get(f"{PREFIX}/incidents/{injected}").json()["state_rail"]

        assert [entry["state"] for entry in rail][:7] == [
            "detected",
            "assessing",
            "planning",
            "assuring",
            "awaiting_approval",
            "executing",
            "resolved",
        ]
        # One clock: nothing on the rail carries the scenario's own timestamp.
        for entry in rail:
            if entry["reached_at"]:
                assert not entry["reached_at"].startswith("2026-08-20T15:36")

    def test_the_step_budget_survives_separate_requests(self, storm_client, injected):
        """Steps are recovered from the record, so /run cannot be used to reset the budget."""
        first = storm_client.post(f"{PREFIX}/incidents/{injected}/run").json()
        second = storm_client.post(f"{PREFIX}/incidents/{injected}/run").json()

        assert second["steps_taken"] >= first["steps_taken"]

    def test_demo_reset_leaves_no_orphaned_workflow_rows(
        self, storm_client, sessionmaker_for, injected
    ):
        """Twice, because the second one is what fails.

        Stream C's reset clears the rows its seed created, including `incident`. The rows a
        run produces are Stream A's, so the CLI removes those first. Asserted by counting
        orphans rather than by catching an exception: SQLite does not enforce foreign keys by
        default, so on SQLite a broken reset silently leaves dangling references instead of
        raising. Verified separately against Postgres, where it raises.
        """
        from app.cli import _demo_reset
        from app.models.workflow import DecisionLog, Plan

        storm_client.post(f"{PREFIX}/incidents/{injected}/run")

        async def reset_twice() -> tuple[int, int, int]:
            async with sessionmaker_for() as session:
                await _demo_reset(session, "bengaluru_storm")
                await session.commit()
            async with sessionmaker_for() as session:
                await _demo_reset(session, "bengaluru_storm")
                await session.commit()
            async with sessionmaker_for() as session:
                live = set((await session.execute(select(Incident.id))).scalars())
                orphan_logs = [
                    entry
                    for entry in (await session.execute(select(DecisionLog))).scalars()
                    if entry.incident_id is not None and entry.incident_id not in live
                ]
                orphan_plans = [
                    plan
                    for plan in (await session.execute(select(Plan))).scalars()
                    if plan.incident_id not in live
                ]
                return len(live), len(orphan_logs), len(orphan_plans)

        incidents, orphan_logs, orphan_plans = asyncio.run(reset_twice())
        assert incidents == 1
        assert orphan_logs == 0
        assert orphan_plans == 0

    def test_a_replayed_run_key_does_not_advance_the_workflow(
        self, storm_client, sessionmaker_for, injected
    ):
        headers = {"Idempotency-Key": "storm-run-once"}
        storm_client.post(f"{PREFIX}/incidents/{injected}/run", headers=headers)

        async def count_actions() -> int:
            async with sessionmaker_for() as session:
                return await _count(session, Action)

        before = asyncio.run(count_actions())
        replay = storm_client.post(f"{PREFIX}/incidents/{injected}/run", headers=headers).json()

        assert replay["replayed"] is True
        assert asyncio.run(count_actions()) == before
