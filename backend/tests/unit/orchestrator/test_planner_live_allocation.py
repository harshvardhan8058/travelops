"""How the eight-member cascade divides its live planner time, over a real database.

Phase 3 live kept failing on exactly three checks — `planner-agent plan present`, `planner-agent
PLAN_PROPOSED visible in replay`, and `model-authored plan exists` — while the playbook, the
explanation, the report, the approval, the execution and the resolved state all passed. All three
failing checks read the **declared primary** incident, and the primary is the one member whose
planner call is guaranteed to be the coldest of the whole run: members advance sequentially,
primary first, and the planner is only ever asked while an incident is `planning`, so the primary
gets exactly one opportunity and it is the first provider call the process makes.

That opportunity was being allocated worse than any other member's. The primary's ceiling was 40
seconds against a transport that allows 60 for a single attempt, so a slow-but-healthy first call
was cancelled before it could finish or be retried, while the warm members behind it answered
inside 20 seconds and produced the candidates the logs showed. The verifier was right and the
allocation was wrong.

These tests pin the allocation, not the numbers: the primary's allowance is reserved and can
complete a whole provider attempt, the rest share a pool charged by the time they actually use, an
exhausted pool skips a model call honestly instead of inventing a provider fault, and the
deterministic playbook survives every one of those outcomes.

They run against the SQLite session from `conftest.py` with real `incident_group_flight` rows,
because "which member is primary" is a database question and stubbing it would test the stub.

Owner: Stream A.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.agents.contract import ModelCallAudit, PlannerResponse
from app.config import (
    LLMMode,
    NotificationMode,
    PolicyMode,
    ResolvedModes,
    Settings,
    WeatherMode,
)
from app.models.cascade import IncidentGroupFlight
from app.models.enums import (
    ActionStatus,
    ActionType,
    IncidentState,
    ProvenanceKind,
    TriggerType,
)
from app.models.reference import Airport, Flight
from app.models.workflow import DecisionLog, Incident, IncidentGroup, Plan
from app.orchestrator.engine import (
    MIN_PLANNER_SLICE_SECONDS,
    Orchestrator,
    WorkflowContext,
    planner_backstop_seconds,
)
from tests.unit.orchestrator.conftest import FIXED_NOW

GROUP_REFERENCE = "GRP-2026-0820-VOBL"
DATASET = "bengaluru_storm"
PLANNER = "planner-agent"

#: The seeded cascade: one primary departure, six affected departures, one affected arrival.
MEMBERS = [
    ("6E 2134", "VOBL", "VIDP", "primary"),
    ("6E 2210", "VOBL", "VABB", "affected_departure"),
    ("6E 6402", "VOBL", "VOMM", "affected_departure"),
    ("6E 5311", "VOBL", "VOHS", "affected_departure"),
    ("6E 812", "VOBL", "VECC", "affected_departure"),
    ("6E 476", "VOBL", "VOCI", "affected_departure"),
    ("6E 933", "VOBL", "VOTP", "affected_departure"),
    ("UK 705", "VAAH", "VOBL", "affected_arrival"),
]


def _modes(llm: LLMMode = LLMMode.live) -> ResolvedModes:
    return ResolvedModes(
        llm=llm,
        weather=WeatherMode.fixture,
        notification=NotificationMode.console,
        policy=PolicyMode.charter,
        real_email_enabled=False,
        assurance_config_present=True,
        assurance_config_version="assurance-v1",
        assurance_config_hash="9f2c4b71d3e85a06",
        degradations=[],
    )


def _settings(**overrides) -> Settings:
    base = {
        "app_env": "test",
        "demo_dataset_id": DATASET,
        "planner_candidate_budget_seconds": 20.0,
        "primary_demo_planner_candidate_budget_seconds": 75.0,
        "planner_group_pool_seconds": 75.0,
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
async def cascade(session):
    """The eight declared members, their group, and one open incident each.

    Built through the models rather than the seeder so the test states its own preconditions, and
    with real `incident_group_flight` rows so role resolution is exercised for what it is: a read
    of declared data, not a naming convention.
    """
    icaos = {"VOBL", "VIDP", "VABB", "VOMM", "VOHS", "VECC", "VOCI", "VOTP", "VAAH"}
    session.add_all(
        [
            Airport(
                icao_code=icao,
                iata_code=icao[1:],
                name=f"{icao} airport",
                city=icao,
                country="IN",
                latitude=13.0,
                longitude=77.0,
                source_ref=f"fixture:{DATASET}",
            )
            for icao in sorted(icaos)
        ]
    )
    group = IncidentGroup(
        reference=GROUP_REFERENCE,
        root_cause=TriggerType.weather,
        airport_icao="VOBL",
        severity="high",
        state=IncidentState.detected,
        opened_at=FIXED_NOW,
        demo_dataset_id=DATASET,
    )
    session.add(group)
    await session.flush()

    incidents: list[tuple[Incident, str]] = []
    for index, (number, origin, destination, role) in enumerate(MEMBERS):
        flight = Flight(
            flight_number=number,
            airline_code=number.split()[0],
            origin_icao=origin,
            destination_icao=destination,
            scheduled_departure=FIXED_NOW + timedelta(minutes=4 + index * 10),
            scheduled_arrival=FIXED_NOW + timedelta(minutes=169 + index * 10),
            block_time_minutes=165,
            status="scheduled",
            is_domestic=True,
            provenance_kind=ProvenanceKind.fixture,
            source_ref=f"fixture:{DATASET}:flight",
        )
        session.add(flight)
        await session.flush()
        session.add(
            IncidentGroupFlight(
                incident_group_id=group.id,
                flight_id=flight.id,
                role=role,
                delay_minutes_at_injection=90,
                provenance_kind=ProvenanceKind.fixture,
                source_ref=f"fixture:{DATASET}:membership",
            )
        )
        incident = Incident(
            reference=f"INC-2026-0820-{origin}-{index + 1:02d}",
            group_id=group.id,
            flight_id=flight.id,
            trigger_type=TriggerType.weather,
            severity="high",
            state=IncidentState.planning,
            opened_at=FIXED_NOW,
            demo_dataset_id=DATASET,
        )
        session.add(incident)
        await session.flush()
        incidents.append((incident, role))

    await session.commit()
    return incidents


def _context(incident: Incident) -> WorkflowContext:
    return WorkflowContext(
        incident_id=incident.id,
        incident_reference=incident.reference,
        state=IncidentState.planning,
        correlation_id=f"group-{GROUP_REFERENCE}",
        flight_id=incident.flight_id,
        trigger_type="weather",
    )


class _Planner:
    """A local planner that records the allowance it was handed and honours a chosen latency.

    Deliberately not a network call: no test should spend money or depend on a provider. It sleeps
    at the same `await` boundary HTTPX would, so the orchestrator's bound is exercised for real.
    """

    def __init__(self, *, latency: float = 0.0, fail: BaseException | None = None) -> None:
        self.latency = latency
        self.fail = fail
        self.budgets: list[float | None] = []
        self.references: list[str] = []

    def install(self, monkeypatch) -> _Planner:
        planner = self

        class _Agent:
            async def propose(self, **kwargs):
                planner.budgets.append(kwargs.get("budget_seconds"))
                planner.references.append(kwargs["incident_reference"])
                await asyncio.sleep(planner.latency)
                if planner.fail is not None:
                    raise planner.fail
                reference = kwargs["incident_reference"]
                return (
                    PlannerResponse(
                        status=ActionStatus.success,
                        reason="Protect threatened connections before notifying passengers.",
                        evidence_refs=[f"incident:{reference}"],
                        tasks=[
                            {
                                "action": ActionType.check_connections,
                                "target_refs": [f"incident:{reference}"],
                                "inputs": {},
                                "depends_on": [],
                            }
                        ],
                    ),
                    ModelCallAudit(
                        generator="openrouter:openai/gpt-oss-120b",
                        prompt_version="planner.v1",
                        latency_ms=int(planner.latency * 1000),
                    ),
                )

        monkeypatch.setattr("app.agents.planner.PlannerAgent", _Agent)
        monkeypatch.setattr("app.orchestrator.dispatch.is_implemented", lambda _action: True)
        return planner


@pytest.fixture
def no_precedents(monkeypatch) -> None:
    async def _none(*_args, **_kwargs):
        return []

    monkeypatch.setattr("app.memory.retrieval.find_precedents", _none)


async def _planner_plans(session, incident_id: int) -> list[Plan]:
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(Plan).where(Plan.incident_id == incident_id, Plan.generator == PLANNER)
        )
    ).scalars()
    return list(rows)


async def _planner_journal(session, incident_id: int) -> list[DecisionLog]:
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(DecisionLog).where(
                DecisionLog.incident_id == incident_id,
                DecisionLog.event_type == "PLAN_PROPOSED",
            )
        )
    ).scalars()
    return [row for row in rows if (row.detail or {}).get("generator") == PLANNER]


# ------------------------------------------------------------------ the reserved allowance


class TestThePrimaryHoldsAReservedAllowance:
    async def test_it_is_the_declared_primary_that_gets_it_not_the_first_reference(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """`INC-...-VAAH-08` would also end in a number; role is what decides.

        The inbound member is not primary, and no amount of reference-shaped reasoning may make it
        one. This is why the allowance reads `incident_group_flight.role`.
        """
        planner = _Planner().install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())

        for incident, _role in cascade:
            await engine._propose_planner_candidate(_context(incident))

        primary_budget = planner.budgets[0]
        assert primary_budget == 75.0
        assert all(budget == 20.0 for budget in planner.budgets[1:])

    async def test_the_allowance_reaches_the_client_instead_of_wrapping_it(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """The fix in one assertion: the budget is handed down, not imposed from outside.

        A budget the client knows about can size its attempts and decline a retry that will not
        fit. A budget expressed only as an outer cancellation can do neither — it can only kill an
        attempt that was still healthy, which is what removed the primary's candidate.
        """
        planner = _Planner().install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())
        primary, _role = cascade[0]

        await engine._propose_planner_candidate(_context(primary))

        assert planner.budgets == [75.0]

    async def test_seven_members_behind_it_cannot_consume_it(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """Even after the pool is completely spent, the primary's allowance is untouched.

        Ordering makes this hard to hit in production — the primary runs first — but the guarantee
        must not depend on ordering. A single retried group run, a `max_incidents` bound or a
        future concurrent advance would otherwise silently starve the one member that matters.
        """
        _Planner().install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())
        engine._planner_pool_seconds = 0.0
        primary, _role = cascade[0]

        allowance = await engine._planner_candidate_allowance(
            await session.get(Incident, primary.id)
        )

        assert allowance.attempt is True
        assert allowance.seconds == 75.0
        assert allowance.pooled is False
        assert allowance.pool_remaining is None


# ------------------------------------------------------------------------ pooled members


class TestTheOtherMembersShareTheirTime:
    async def test_a_healthy_run_charges_only_what_it_used(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """Seven warm calls must still all produce candidates, and barely dent the pool.

        This is the reason for pooling rather than trimming: nothing is taken away from the
        non-primary members in a healthy run. They were reserving 140 seconds and spending a few.
        """
        planner = _Planner(latency=0.02).install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())

        for incident, _role in cascade:
            await engine._propose_planner_candidate(_context(incident))
        await session.commit()

        assert len(planner.references) == 8
        for incident, _role in cascade:
            assert len(await _planner_plans(session, incident.id)) == 1
        # Seven calls of ~20ms cannot have eaten a 75-second pool.
        assert engine._planner_pool_seconds > 70.0

    async def test_a_slow_member_is_charged_its_real_cost(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """Charging the nominal allowance is the accounting error being fixed.

        Debiting 20 seconds for a 0.2-second call is how seven members came to reserve 140 seconds
        that nothing spent, leaving nothing spare for the primary.
        """
        planner = _Planner(latency=0.2).install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())
        member, _role = cascade[1]

        await engine._propose_planner_candidate(_context(member))

        spent = 75.0 - (engine._planner_pool_seconds or 0.0)
        assert 0.15 < spent < 5.0, f"charged {spent:g}s for a 0.2s call"
        assert planner.budgets == [20.0]


class TestAnExhaustedPoolIsSaidPlainlyAndBoundsTheCascade:
    async def test_the_remaining_members_are_skipped_without_a_model_call(
        self, session, cascade, monkeypatch, no_precedents
    ):
        planner = _Planner(latency=0.0).install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())
        engine._planner_pool_seconds = MIN_PLANNER_SLICE_SECONDS / 2
        member, _role = cascade[1]

        await engine._propose_planner_candidate(_context(member))
        await session.commit()

        assert planner.references == [], "a model was called with no budget to call it under"
        assert await _planner_plans(session, member.id) == []

    async def test_it_records_that_nothing_was_asked_rather_than_a_provider_fault(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """The honesty requirement. No model was asked, so nothing about a model is claimed.

        `PLANNER_AGENT_UNAVAILABLE` is the right existing route — no candidate was produced — but
        the phase has to say the allowance ran out here, not that a provider misbehaved.
        """
        _Planner().install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())
        engine._planner_pool_seconds = 0.0
        member, _role = cascade[1]

        await engine._propose_planner_candidate(_context(member))
        await session.commit()

        from sqlalchemy import select

        rows = (
            await session.execute(
                select(DecisionLog).where(
                    DecisionLog.incident_id == member.id,
                    DecisionLog.event_type == "PLANNER_AGENT_UNAVAILABLE",
                )
            )
        ).scalars()
        entries = list(rows)
        assert len(entries) == 1
        detail = entries[0].detail or {}
        assert detail["llm_phase"] == "orchestrator_pool_exhausted"
        # Where in the orchestrator it happened, so the record says "we never asked" rather than
        # leaving a reader to infer it from a phase that means something else.
        assert detail["phase"] == "allocation"
        assert detail["status_code"] is None
        assert detail["finish_reason"] is None
        assert "no model call was attempted" in detail["reason"]

    async def test_the_playbook_plan_is_never_touched_by_any_of_this(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """Whatever the allocation decides, the deterministic recovery path is unaffected."""
        _Planner(latency=5.0).install(monkeypatch)
        engine = Orchestrator(
            session,
            settings=_settings(
                planner_candidate_budget_seconds=0.05,
                primary_demo_planner_candidate_budget_seconds=0.05,
            ),
            modes=_modes(),
        )
        member, _role = cascade[1]
        playbook = Plan(
            incident_id=member.id,
            generated_at=FIXED_NOW,
            generator="fallback-playbook",
            rationale="deterministic",
            retrieved_incident_ids=[],
        )
        session.add(playbook)
        await session.commit()

        await engine._propose_planner_candidate(_context(member))
        await session.commit()

        assert await _planner_plans(session, member.id) == []
        surviving = await session.get(Plan, playbook.id)
        assert surviving is not None
        assert surviving.generator == "fallback-playbook"


# --------------------------------------------------------------- the whole eight-incident run


class TestTheCompleteEightIncidentCascade:
    async def test_every_member_gets_a_durable_candidate_and_a_replayable_frame(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """Persistence proved on a real database, after a real commit.

        A staged row and a committed row are not the same claim, and the Phase 3 verifier reads the
        committed one through `/replay`. So this asserts both halves survive the transaction: the
        `planner-agent` Plan and its `PLAN_PROPOSED` journal entry, for all eight members.
        """
        _Planner(latency=0.01).install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes())

        for incident, _role in cascade:
            ctx = _context(incident)
            await engine._propose_planner_candidate(ctx)
            await session.commit()

        # Read back by primary key after expiring the identity map, so these are the committed rows
        # rather than the in-memory objects the writes happened to leave behind.
        members = [(incident.id, incident.reference) for incident, _role in cascade]
        session.expire_all()
        for incident_id, reference in members:
            plans = await _planner_plans(session, incident_id)
            assert len(plans) == 1, reference
            assert plans[0].prompt_version == "planner.v1"
            assert plans[0].selection_state == "candidate"

            frames = await _planner_journal(session, incident_id)
            assert len(frames) == 1, reference
            assert frames[0].detail["plan_id"] == plans[0].id

    async def test_the_primary_survives_latency_that_the_ordinary_cap_would_have_killed(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """The failure itself, reproduced and then fixed, at test speed.

        The allowances are scaled down so this runs in milliseconds rather than spending a real
        minute, but the shape is the observed one: a latency above the ordinary member cap and
        below the primary's reserved allowance. Under one shared ceiling the primary produces
        nothing; under a reserved allowance it produces a candidate.
        """
        settings = _settings(
            planner_candidate_budget_seconds=0.05,
            primary_demo_planner_candidate_budget_seconds=1.5,
        )
        primary, _role = cascade[0]
        member, _role = cascade[1]
        _Planner(latency=0.25).install(monkeypatch)

        engine = Orchestrator(session, settings=settings, modes=_modes())
        await engine._propose_planner_candidate(_context(primary))
        await engine._propose_planner_candidate(_context(member))
        await session.commit()

        assert len(await _planner_plans(session, primary.id)) == 1, (
            "the declared primary must get a candidate from a call this slow"
        )
        assert await _planner_plans(session, member.id) == [], (
            "an ordinary member is still bounded by the ordinary cap"
        )

    async def test_a_pathological_run_stays_inside_the_phase2_request_budget(
        self, session, cascade, monkeypatch, no_precedents
    ):
        """Eight hung planner calls must not be able to spend the caller's 300 seconds.

        Measured against the configured allowances rather than by hanging for real: the primary's
        hard backstop, plus the pool, plus the most one member can overshoot the pool by. The old
        arrangement's bound was 180s of nominal budget and it still left the primary short.
        """
        settings = _settings()
        engine = Orchestrator(session, settings=settings, modes=_modes())

        allowances = []
        for incident, _role in cascade:
            row = await session.get(Incident, incident.id)
            allowance = await engine._planner_candidate_allowance(row)
            allowances.append(allowance)
            # Charge the worst case each member could cost.
            if allowance.attempt:
                engine._charge_planner_pool(allowance, planner_backstop_seconds(allowance.seconds))

        assert allowances[0].primary_demo is True
        assert allowances[0].pooled is False
        assert all(a.pooled for a in allowances[1:])

        worst_case = planner_backstop_seconds(allowances[0].seconds) + sum(
            planner_backstop_seconds(a.seconds) for a in allowances[1:] if a.attempt
        )
        assert worst_case < 180.0, f"worst case {worst_case:g}s regressed past the old bound"
        assert worst_case < 300.0
        # The pool is what stops it, so the later members must actually run out.
        assert any(a.attempt is False for a in allowances[1:])


# ------------------------------------------------------------------- fixture / off unchanged


class TestFixtureAndOffBehaviourIsUnchanged:
    async def test_fixture_mode_is_never_pooled(self, session, cascade, monkeypatch, no_precedents):
        """Fixture replay costs no wall-clock, so rationing it would only add nondeterminism.

        Phase 3 fixture mode must keep producing a candidate for every member however many members
        there are, and must not depend on the order they ran in.
        """
        planner = _Planner().install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes(LLMMode.fixture))

        for incident, _role in cascade:
            row = await session.get(Incident, incident.id)
            allowance = await engine._planner_candidate_allowance(row)
            assert allowance.attempt is True
            assert allowance.pool_remaining is None
            assert allowance.pooled is False

        for incident, _role in cascade:
            await engine._propose_planner_candidate(_context(incident))
        await session.commit()

        assert len(planner.references) == 8
        assert engine._planner_pool_seconds is None, "the pool was consulted in fixture mode"

    async def test_off_mode_asks_for_nothing_and_allocates_nothing(
        self, session, cascade, monkeypatch, no_precedents
    ):
        planner = _Planner().install(monkeypatch)
        engine = Orchestrator(session, settings=_settings(), modes=_modes(LLMMode.off))

        for incident, _role in cascade:
            await engine._propose_planner_candidate(_context(incident))
        await session.commit()

        assert planner.references == []
        assert engine._planner_pool_seconds is None
        for incident, _role in cascade:
            assert await _planner_plans(session, incident.id) == []
