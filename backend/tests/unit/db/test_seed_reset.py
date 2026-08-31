"""Runtime rows owned by the demo dataset are removed with their declared scope.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.seed import DEMO_DATASET_ID, reset_demo_dataset, seed_demo_dataset
from app.models.cascade import IncidentGroupFlight
from app.models.enums import IncidentState, ProvenanceKind, TriggerType
from app.models.workflow import DecisionLog, IncidentGroup


async def test_reset_removes_runtime_group_membership_and_group_audit(session):
    await seed_demo_dataset(session)
    await session.commit()

    reference = "SCN-RESET-OWNERSHIP"
    group = IncidentGroup(
        reference=reference,
        root_cause=TriggerType.weather,
        airport_icao="VOBL",
        severity="high",
        state=IncidentState.detected,
        opened_at=datetime(2026, 8, 20, 15, 40, tzinfo=UTC),
        demo_dataset_id=DEMO_DATASET_ID,
    )
    session.add(group)
    await session.flush()
    session.add_all(
        [
            IncidentGroupFlight(
                incident_group_id=group.id,
                flight_id=1,
                role="primary",
                delay_minutes_at_injection=420,
                provenance_kind=ProvenanceKind.simulated,
                source_ref=f"scenario-builder:{reference}",
            ),
            DecisionLog(
                incident_id=None,
                stage="scenario",
                actor="human",
                event_type="SCENARIO_CREATED",
                summary=f"Operator created scenario {reference}",
                detail={"scenario_reference": reference},
                correlation_id=reference,
            ),
        ]
    )
    await session.commit()

    await reset_demo_dataset(session)
    await session.commit()

    group_count = await session.scalar(
        select(func.count()).select_from(IncidentGroup).where(IncidentGroup.reference == reference)
    )
    membership_count = await session.scalar(
        select(func.count())
        .select_from(IncidentGroupFlight)
        .where(IncidentGroupFlight.incident_group_id == group.id)
    )
    audit_count = await session.scalar(
        select(func.count()).select_from(DecisionLog).where(DecisionLog.correlation_id == reference)
    )

    assert group_count == 0
    assert membership_count == 0
    assert audit_count == 0
