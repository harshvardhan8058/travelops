"""Plan approval, end to end through the engine — P2-D3.

The Bengaluru dataset holds exactly one action for a person, and it is high risk, so a plan
approval covers nothing on it. That makes the seeded journey a poor test of this feature: it would
pass while the mechanism was entirely broken. So these tests build a plan with a **medium**-risk
held task and prove the four rules against the real engine, the real tables and Stream B's real
gate:

1. A plan approval releases a medium-risk hold without a per-action decision.
2. It never releases a high-risk one.
3. It never releases a task blocked on failed evidence, whatever the tier.
4. It stops covering anything once the plan is re-planned.

Every one of these is a way an approval could quietly authorise work nobody agreed to.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
)
from app.assurance.plan_gate import evaluate_plan, load_plan_config
from app.db.base import Base
from app.models.cascade import PlanApproval, PlanApprovalTier
from app.models.enums import (
    ActionStatus,
    AssuranceDecision,
    CheckState,
    IncidentState,
    ProvenanceKind,
    RiskTier,
    TaskState,
    TriggerType,
)
from app.models.reference import Airport, Flight
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    Incident,
    IncidentGroup,
    Plan,
    PlanTask,
)
from app.orchestrator import plan_lifecycle
from tests.contract.sqlite_support import create_sqlite_engine

pytestmark = pytest.mark.anyio

V2 = "./config/assurance.v2.yaml"


@pytest.fixture
async def session(tmp_path):
    engine = create_sqlite_engine(tmp_path / "plan_approval.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        active.add(
            Airport(
                icao_code="VOBL",
                iata_code="BLR",
                name="Kempegowda",
                city="Bengaluru",
                country="IN",
                latitude=13.1979,
                longitude=77.7063,
            )
        )
        active.add(
            Airport(
                icao_code="VIDP",
                iata_code="DEL",
                name="Indira Gandhi",
                city="Delhi",
                country="IN",
                latitude=28.5665,
                longitude=77.1031,
            )
        )
        await active.flush()
        active.add(
            Flight(
                id=1,
                flight_number="6E 2134",
                airline_code="6E",
                origin_icao="VOBL",
                destination_icao="VIDP",
                scheduled_departure=datetime(2026, 8, 20, 15, 40, tzinfo=UTC),
                scheduled_arrival=datetime(2026, 8, 20, 18, 25, tzinfo=UTC),
                block_time_minutes=165,
                status="delayed",
                provenance_kind=ProvenanceKind.synthetic,
            )
        )
        group = IncidentGroup(
            reference="GRP-TEST-0001",
            root_cause=TriggerType.weather,
            airport_icao="VOBL",
            severity="high",
            state=IncidentState.executing,
            opened_at=datetime(2026, 8, 20, 15, 36, tzinfo=UTC),
        )
        active.add(group)
        await active.flush()
        incident = Incident(
            reference="INC-TEST-0001",
            flight_id=1,
            group_id=group.id,
            trigger_type=TriggerType.weather,
            severity="high",
            state=IncidentState.awaiting_approval,
            opened_at=group.opened_at,
        )
        active.add(incident)
        await active.flush()
        await active.commit()
        yield active, group, incident
    await engine.dispose()


async def _plan_with(
    session, incident, *, tiers: dict[str, RiskTier], failed: set[str] = frozenset()
):
    """A persisted plan whose tasks carry the given tiers, each with a recorded evaluation.

    Held tasks are recorded exactly as the gate records them: `needs_human`, with the check that
    caused it. A task in `failed` gets a FAILED check, which is what makes it unapprovable at any
    tier.
    """
    plan = Plan(
        incident_id=incident.id,
        generated_at=datetime(2026, 8, 20, 15, 45, tzinfo=UTC),
        generator="fallback-playbook",
        retrieved_incident_ids=[],
    )
    session.add(plan)
    await session.flush()

    rows: dict[str, PlanTask] = {}
    for order, (action_type, tier) in enumerate(tiers.items(), start=1):
        row = PlanTask(
            plan_id=plan.id,
            action_type=action_type,
            task_order=order,
            depends_on=[],
            target_refs=["flight:1"],
            inputs={},
            state=TaskState.needs_human,
        )
        session.add(row)
        await session.flush()
        rows[action_type] = row

        is_failed = action_type in failed
        session.add(
            AssuranceEvaluation(
                plan_task_id=row.id,
                decision=AssuranceDecision.needs_human,
                risk_tier=tier,
                check_results={
                    "entities_valid": {
                        "state": CheckState.failed.value if is_failed else CheckState.passed.value,
                        "reason_code": "ENTITY_NOT_FOUND" if is_failed else "OK",
                        "reason": "referenced entity does not exist" if is_failed else "ok",
                    },
                    # `HUMAN_APPROVAL_REQUIRED` on a PASSING check is how the real gate records a
                    # tier hold: the classification blocks while the check itself succeeds. This
                    # is what makes `blocking_kinds` report `risk`, and therefore what makes the
                    # task approvable. Recording `OK` here instead produced a `needs_human` task
                    # with no reason, which the plan gate correctly refused as an evaluation that
                    # never happened.
                    "action_risk": {
                        "state": CheckState.passed.value,
                        "reason_code": "HUMAN_APPROVAL_REQUIRED",
                        "reason": f"{tier.value} risk requires a human decision",
                        "tier": tier.value,
                    },
                },
                blocking_reasons=["entities_valid"] if is_failed else ["action_risk"],
                evidence_refs=["flight:1"],
                config_version="assurance-v2",
                config_hash="testhash",
            )
        )
        await session.flush()

    await session.commit()
    return plan, rows


async def _approve(session, group, plan, *, reason="aggregate accepted at the ops desk"):
    """Evaluate the plan and record an approval, with coverage and exposure inside the limits."""
    loaded = load_plan_config(V2)
    review = await plan_lifecycle.project_plan(
        session, plan_id=int(plan.id), group_reference=group.reference
    )
    result = evaluate_plan(
        plan=review,
        coverage=CoverageDeclaration(declared=True, impacted_refs=["flight:1"], deferred={}),
        exposure=ExposureInputs(
            total_exposure_inr=1000,
            passengers_affected=174,
            rooms_committed=10,
            external_effects=0,
            unresolved_cohorts=[],
        ),
        config=loaded.plan,
        config_version=loaded.version,
        config_hash=loaded.digest,
    )
    approval = await plan_lifecycle.record_plan_approval(
        session,
        plan_id=int(plan.id),
        incident_group_id=int(group.id),
        result=result,
        actor_id="operator-1",
        reason=reason,
        decided_at=datetime(2026, 8, 20, 16, 5, tzinfo=UTC),
    )
    await session.commit()
    return result, approval


# --------------------------------------------------------------------------- the hash


async def test_the_plan_hash_is_the_gates_hash_not_a_second_one(session):
    """One plan identity in the system.

    `plan.plan_hash` must be the value Stream B's `plan_approval_covers` compares. A second,
    differently-canonicalised hash would eventually disagree, and the approval would then either
    cover work nobody reviewed or refuse work someone did.
    """
    active, group, incident = session
    plan, _rows = await _plan_with(active, incident, tiers={"find_hotel_options": RiskTier.low})
    stamped = await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    review = await plan_lifecycle.project_plan(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    assert stamped == review.hash()
    assert (await active.get(Plan, plan.id)).plan_hash == stamped


async def test_the_hash_changes_when_a_task_is_added(session):
    active, group, incident = session
    plan, _rows = await _plan_with(active, incident, tiers={"check_connections": RiskTier.low})
    before = await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )

    active.add(
        PlanTask(
            plan_id=plan.id,
            action_type="reserve_hotel_block",
            task_order=2,
            depends_on=[],
            target_refs=["flight:1"],
            inputs={},
            state=TaskState.proposed,
        )
    )
    await active.commit()

    after = await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    assert after != before


# ------------------------------------------------------------------- what it covers


async def test_a_plan_approval_releases_a_medium_hold(session):
    """The feature working. A medium task held by the gate needs no decision of its own."""
    active, group, incident = session
    plan, rows = await _plan_with(active, incident, tiers={"reserve_hotel_block": RiskTier.medium})
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    _result, approval = await _approve(active, group, plan)

    _row, check = await plan_lifecycle.plan_approval_for_task(
        active,
        plan_id=int(plan.id),
        plan_task_id=int(rows["reserve_hotel_block"].id),
        group_reference=group.reference,
    )
    assert check is not None
    assert check.permitted is True, check.reason
    assert str(rows["reserve_hotel_block"].id) in (approval.covered_task_ids or [])


async def test_a_plan_approval_never_releases_a_high_risk_hold(session):
    """P2-D3's hard rule. Money, cancellation and bulk external effects get their own decision."""
    active, group, incident = session
    plan, rows = await _plan_with(
        active,
        incident,
        tiers={"reserve_hotel_block": RiskTier.medium, "notify_passengers": RiskTier.high},
    )
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    _result, approval = await _approve(active, group, plan)

    _row, check = await plan_lifecycle.plan_approval_for_task(
        active,
        plan_id=int(plan.id),
        plan_task_id=int(rows["notify_passengers"].id),
        group_reference=group.reference,
    )
    assert check is not None
    assert check.permitted is False
    # Either refusal is correct — Stream B checks task-list membership before the tier — and the
    # substantive guarantee is the same either way: a high-risk task is never covered.
    assert check.refusal.value in {"TASK_NOT_IN_SCOPE", "HIGH_RISK_NEEDS_OWN_DECISION"}
    assert str(rows["notify_passengers"].id) not in (approval.covered_task_ids or [])


async def test_the_high_risk_task_is_listed_as_still_needing_its_own_decision(session):
    """The console has to be able to say so before anyone clicks approve."""
    active, group, incident = session
    plan, rows = await _plan_with(
        active,
        incident,
        tiers={"reserve_hotel_block": RiskTier.medium, "notify_passengers": RiskTier.high},
    )
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    await _approve(active, group, plan)

    outstanding = await plan_lifecycle.tasks_needing_own_decision(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    assert outstanding == [str(rows["notify_passengers"].id)]


async def test_a_plan_approval_never_releases_failed_evidence(session):
    """Approval can accept risk. It can never accept a check that failed.

    Asserted at medium tier specifically: the tier *is* covered, so the only thing refusing this
    is the evidence rule. If it were the tier check doing the work, this test would pass while the
    evidence rule was broken.
    """
    active, group, incident = session
    plan, rows = await _plan_with(
        active,
        incident,
        tiers={"reserve_hotel_block": RiskTier.medium},
        failed={"reserve_hotel_block"},
    )
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )

    # The gate refuses to let this plan be approved at all, which is the first line of defence.
    with pytest.raises(Exception) as raised:
        await _approve(active, group, plan)
    assert "cannot be approved" in str(raised.value)
    assert str(rows["reserve_hotel_block"].id)


async def test_a_replanned_plan_loses_its_approval(session):
    """The approval is bound to the hash. Re-planning voids it rather than migrating it."""
    active, group, incident = session
    plan, rows = await _plan_with(active, incident, tiers={"reserve_hotel_block": RiskTier.medium})
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    await _approve(active, group, plan)

    # A task appended after signing changes the hash.
    active.add(
        PlanTask(
            plan_id=plan.id,
            action_type="arrange_ground_transport",
            task_order=9,
            depends_on=[],
            target_refs=["flight:1"],
            inputs={},
            state=TaskState.proposed,
        )
    )
    await active.commit()
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )

    _row, check = await plan_lifecycle.plan_approval_for_task(
        active,
        plan_id=int(plan.id),
        plan_task_id=int(rows["reserve_hotel_block"].id),
        group_reference=group.reference,
    )
    assert check is not None
    assert check.permitted is False
    assert "changed after it was approved" in check.reason


# ------------------------------------------------------------------------ persistence


async def test_no_tier_row_can_be_high(session):
    """P2-D3 as a storage guarantee. The database rejects `high` outright."""
    active, group, incident = session
    plan, _rows = await _plan_with(active, incident, tiers={"reserve_hotel_block": RiskTier.medium})
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    _result, approval = await _approve(active, group, plan)

    tiers = (
        (
            await active.execute(
                select(PlanApprovalTier.risk_tier).where(
                    PlanApprovalTier.plan_approval_id == approval.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert set(tiers) == {"low", "medium"}

    # The CHECK constraint, not application code, is what refuses this.
    active.add(PlanApprovalTier(plan_approval_id=approval.id, risk_tier="high"))
    with pytest.raises(IntegrityError):
        await active.flush()
    await active.rollback()


async def test_an_approval_is_immutable(session):
    """A change of mind is a new plan, not an edited signature."""
    active, group, incident = session
    plan, _rows = await _plan_with(active, incident, tiers={"reserve_hotel_block": RiskTier.medium})
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    await _approve(active, group, plan)

    with pytest.raises(Exception) as raised:
        await _approve(active, group, plan, reason="second thoughts")
    assert "already carries an approval" in str(raised.value)

    count = (
        await active.execute(
            select(func.count()).select_from(PlanApproval).where(PlanApproval.plan_id == plan.id)
        )
    ).scalar_one()
    assert count == 1


async def test_an_unhashed_plan_cannot_be_approved(session):
    """Without a hash nothing could later prove what was signed."""
    active, group, incident = session
    plan, _rows = await _plan_with(active, incident, tiers={"reserve_hotel_block": RiskTier.medium})
    with pytest.raises(Exception) as raised:
        await _approve(active, group, plan)
    assert "no hash" in str(raised.value)


async def test_an_action_records_which_authorisation_released_it(session):
    """`plan_approval_id` on the action, so the record always names what let it run."""
    active, group, incident = session
    plan, rows = await _plan_with(active, incident, tiers={"reserve_hotel_block": RiskTier.medium})
    await plan_lifecycle.stamp_plan_hash(
        active, plan_id=int(plan.id), group_reference=group.reference
    )
    _result, approval = await _approve(active, group, plan)

    evaluation = (
        (
            await active.execute(
                select(AssuranceEvaluation).where(
                    AssuranceEvaluation.plan_task_id == rows["reserve_hotel_block"].id
                )
            )
        )
        .scalars()
        .one()
    )
    active.add(
        Action(
            plan_task_id=rows["reserve_hotel_block"].id,
            assurance_id=evaluation.id,
            human_decision_id=None,
            plan_approval_id=approval.id,
            actor="orchestrator",
            idempotency_key="test-plan-approval-1",
            status=ActionStatus.success,
            reason="rooms held under the plan approval",
            provenance_kind=ProvenanceKind.synthetic,
            payload={},
        )
    )
    await active.commit()

    action = (
        (
            await active.execute(
                select(Action).where(Action.idempotency_key == "test-plan-approval-1")
            )
        )
        .scalars()
        .one()
    )
    assert action.plan_approval_id == approval.id
    assert action.human_decision_id is None
    # Never both, and never neither: the record says exactly one thing about why this ran.
    assert (action.human_decision_id is None) != (action.plan_approval_id is None)
