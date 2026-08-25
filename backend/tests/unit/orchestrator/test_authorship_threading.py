"""The orchestrator must tell the gate who wrote the proposal it is assuring.

Stream B built `authorship_constraints` and `gate_requirements(authorship=...)` in #64. Nothing
supplied that argument, so on main the whole mechanism returned `[]` on every call: a model could
put `assurance_decision` in a payload, or cite a reference nobody recorded, and the gate had no
reason to distrust either. These tests pin the wiring, because the failure mode is silence — the
gate keeps evaluating and every existing test keeps passing.

Two properties, and the second matters as much as the first:

1. A model-authored plan produces the refusals Stream B wrote.
2. A deterministic plan produces **none**, so Phase 1 and Phase 2 behaviour is byte-identical and
   the frozen gate stays frozen.

Owner: Stream C (integration). The constraints themselves are Stream B's.
"""

from __future__ import annotations

import pytest

from app.agents.planner import GENERATOR as PLANNER_GENERATOR
from app.agents.planner import PROMPT_VERSION
from app.assurance.authorship import (
    CONSTRAINT_SYSTEM_AUTHORED,
    CONSTRAINT_UNCORROBORATED_EVIDENCE,
    Authorship,
)
from app.models.enums import ActionType, IncidentState
from app.models.workflow import Incident, Plan
from app.orchestrator.engine import Orchestrator, WorkflowContext
from app.orchestrator.playbook import FALLBACK_GENERATOR

pytestmark = pytest.mark.anyio


async def _incident(session, flight) -> Incident:
    incident = Incident(
        reference="INC-AUTH-01",
        flight_id=flight.id,
        trigger_type="weather",
        severity="high",
        state=IncidentState.planning,
    )
    session.add(incident)
    await session.flush()
    return incident


async def _plan(session, incident, *, generator: str, raw: dict | None = None) -> Plan:
    plan = Plan(
        incident_id=incident.id,
        generator=generator,
        prompt_version=PROMPT_VERSION if generator != FALLBACK_GENERATOR else None,
        rationale="test",
        raw_response=raw,
        retrieved_incident_ids=[],
    )
    session.add(plan)
    await session.flush()
    return plan


def _ctx(incident, plan, *, evidence: list[str] | None = None) -> WorkflowContext:
    return WorkflowContext(
        incident_id=incident.id,
        incident_reference=incident.reference,
        state=IncidentState.planning,
        correlation_id=incident.reference,
        flight_id=incident.flight_id,
        trigger_type="weather",
        plan_id=plan.id,
        evidence_refs=list(evidence or []),
    )


class TestAuthorshipIsDerivedFromThePlanRow:
    """Authorship is read from the durable record, not tracked in memory.

    A resumed run has no in-memory history of who proposed the plan, so anything held only in the
    context would silently become `deterministic` after a restart — granting a model proposal the
    trust of a playbook one.
    """

    async def test_a_playbook_plan_is_deterministic(self, session, settings, modes, flight):
        incident = await _incident(session, flight)
        plan = await _plan(session, incident, generator=FALLBACK_GENERATOR)
        engine = Orchestrator(session, settings=settings, modes=modes)

        authorship, proposed = await engine._proposal_authorship(_ctx(incident, plan))

        assert authorship.authored_by is Authorship.deterministic
        assert authorship.authored_by.is_model is False
        assert proposed == []

    async def test_a_planner_plan_is_model_authored_and_carries_its_citations(
        self, session, settings, modes, flight
    ):
        incident = await _incident(session, flight)
        plan = await _plan(
            session,
            incident,
            generator=PLANNER_GENERATOR,
            raw={"evidence_refs": ["incident:INC-AUTH-01", "weather:VOBL"]},
        )
        engine = Orchestrator(session, settings=settings, modes=modes)

        authorship, proposed = await engine._proposal_authorship(_ctx(incident, plan))

        assert authorship.authored_by is Authorship.model
        assert authorship.generator == PLANNER_GENERATOR
        assert authorship.prompt_version == PROMPT_VERSION
        # The refs the MODEL claimed, which exist only in the stored response. The task rows carry
        # the orchestrator's refs, so reading those would corroborate the model against itself.
        assert proposed == ["incident:INC-AUTH-01", "weather:VOBL"]

    async def test_an_unloadable_plan_is_treated_as_model_authored(
        self, session, settings, modes, flight
    ):
        """The conservative direction. The alternative extends deterministic trust to an unknown."""
        incident = await _incident(session, flight)
        plan = await _plan(session, incident, generator=FALLBACK_GENERATOR)
        ctx = _ctx(incident, plan)
        ctx.plan_id = 999_999  # no such plan
        engine = Orchestrator(session, settings=settings, modes=modes)

        authorship, _ = await engine._proposal_authorship(ctx)

        assert authorship.authored_by is Authorship.model


class TestTheGateReceivesTheConstraints:
    """End of the wire: `gate_requirements` must actually emit Stream B's refusals."""

    async def test_a_deterministic_proposal_adds_no_authorship_constraint(
        self, session, settings, modes, flight
    ):
        """Phase 2 parity. If this ever fails, the frozen gate has changed behaviour."""
        incident = await _incident(session, flight)
        plan = await _plan(session, incident, generator=FALLBACK_GENERATOR)
        engine = Orchestrator(session, settings=settings, modes=modes)

        task = _task(ActionType.check_connections, {"minimum_connection_minutes": 45})
        requirements = await engine._policy_requirements(_ctx(incident, plan), task, {})

        ids = _constraint_ids(requirements)
        assert CONSTRAINT_SYSTEM_AUTHORED not in ids
        assert CONSTRAINT_UNCORROBORATED_EVIDENCE not in ids

    async def test_a_model_asserting_a_system_field_is_refused(
        self, session, settings, modes, flight
    ):
        """`assurance_decision` in a payload is a proposal claiming it was already authorised."""
        incident = await _incident(session, flight)
        plan = await _plan(session, incident, generator=PLANNER_GENERATOR, raw={})
        engine = Orchestrator(session, settings=settings, modes=modes)

        task = _task(ActionType.check_connections, {"assurance_decision": "execute"})
        requirements = await engine._policy_requirements(_ctx(incident, plan), task, {})

        assert CONSTRAINT_SYSTEM_AUTHORED in _constraint_ids(requirements)

    async def test_a_model_citing_an_unrecorded_reference_is_refused(
        self, session, settings, modes, flight
    ):
        incident = await _incident(session, flight)
        plan = await _plan(
            session,
            incident,
            generator=PLANNER_GENERATOR,
            raw={"evidence_refs": ["metar:VOBL:real", "metar:NOWHERE:invented"]},
        )
        engine = Orchestrator(session, settings=settings, modes=modes)

        task = _task(ActionType.check_connections, {})
        requirements = await engine._policy_requirements(
            _ctx(incident, plan, evidence=["metar:VOBL:real"]), task, {}
        )

        ids = _constraint_ids(requirements)
        assert CONSTRAINT_UNCORROBORATED_EVIDENCE in ids
        # The refusal must name the invented ref, not just report a count.
        breach = next(
            c for c in requirements.constraints if c.get("id") == CONSTRAINT_UNCORROBORATED_EVIDENCE
        )
        assert "metar:NOWHERE:invented" in str(breach)
        assert "metar:VOBL:real" not in str(breach)

    async def test_a_model_citing_only_recorded_references_is_not_refused(
        self, session, settings, modes, flight
    ):
        """The check must not fire on a well-behaved model, or it would be noise."""
        incident = await _incident(session, flight)
        plan = await _plan(
            session,
            incident,
            generator=PLANNER_GENERATOR,
            raw={"evidence_refs": ["metar:VOBL:real"]},
        )
        engine = Orchestrator(session, settings=settings, modes=modes)

        requirements = await engine._policy_requirements(
            _ctx(incident, plan, evidence=["metar:VOBL:real", "flight:1"]),
            _task(ActionType.check_connections, {}),
            {},
        )

        assert CONSTRAINT_UNCORROBORATED_EVIDENCE not in _constraint_ids(requirements)

    async def test_citing_the_refs_the_orchestrator_supplied_is_not_refused(
        self, session, settings, modes, flight
    ):
        """The defect this caught on the real journey.

        `_target_refs` hands the planner `incident:<ref>` and `flight:<id>`, and the fixture planner
        cites exactly those. With only `ctx.evidence_refs` as the baseline — which holds delay-risk
        refs like `airport:VOBL` and `observation:...` — every one of them was refused as
        uncorroborated, and the agent-authored plan could never clear the gate.

        A reference the orchestrator itself supplied is traceable by definition.
        """
        incident = await _incident(session, flight)
        plan = await _plan(
            session,
            incident,
            generator=PLANNER_GENERATOR,
            raw={"evidence_refs": ["incident:INC-AUTH-01", "flight:1"]},
        )
        engine = Orchestrator(session, settings=settings, modes=modes)

        task = _task(ActionType.check_connections, {})
        task.target_refs = ["incident:INC-AUTH-01", "flight:1"]
        # Baseline deliberately holds ONLY delay-risk-shaped refs, as it does on the real journey.
        requirements = await engine._policy_requirements(
            _ctx(incident, plan, evidence=["airport:VOBL", "observation:metar:VOBL"]), task, {}
        )

        assert CONSTRAINT_UNCORROBORATED_EVIDENCE not in _constraint_ids(requirements)

    async def test_widening_the_baseline_still_refuses_an_invented_ref(
        self, session, settings, modes, flight
    ):
        """The fix must not blunt the check: a ref in neither set is still refused."""
        incident = await _incident(session, flight)
        plan = await _plan(
            session,
            incident,
            generator=PLANNER_GENERATOR,
            raw={"evidence_refs": ["incident:INC-AUTH-01", "metar:NOWHERE:invented"]},
        )
        engine = Orchestrator(session, settings=settings, modes=modes)

        task = _task(ActionType.check_connections, {})
        task.target_refs = ["incident:INC-AUTH-01"]
        requirements = await engine._policy_requirements(
            _ctx(incident, plan, evidence=["airport:VOBL"]), task, {}
        )

        ids = _constraint_ids(requirements)
        assert CONSTRAINT_UNCORROBORATED_EVIDENCE in ids
        breach = next(
            c for c in requirements.constraints if c.get("id") == CONSTRAINT_UNCORROBORATED_EVIDENCE
        )
        assert "metar:NOWHERE:invented" in str(breach)
        # The supplied ref must not be named as a breach.
        assert "incident:INC-AUTH-01" not in str(breach)


# ------------------------------------------------------------------------------- helpers


def _task(action: ActionType, inputs: dict):
    from app.agents.contract import PlanTask

    return PlanTask(action=action, target_refs=["incident:INC-AUTH-01"], inputs=inputs)


def _constraint_ids(requirements) -> set[str]:
    return {c.get("id") for c in requirements.constraints if isinstance(c, dict)}
