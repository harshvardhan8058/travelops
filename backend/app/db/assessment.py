"""Run Delay Risk against stored evidence and record the `prediction` row.

Delay Risk is deliberately **not** an `ActionType`. It is not something the gate authorises
and dispatches — it is the assessment that decides whether an incident should exist at all,
and it runs before `Orchestrator.open_incident(prediction_id=...)`. Registering it as an
action would put the trigger for the workflow inside the workflow.

So this module is the seam. Stream A's injection endpoint calls:

    prediction_id, result = await record_delay_risk_prediction(
        session,
        airport_icao="VOBL",
        flight_id=flight.id,
        as_of=scenario_clock,
    )
    ctx = await orchestrator.open_incident(
        flight_id, "weather", prediction_id=prediction_id, group_id=group.id
    )

## `as_of` is not optional

The archived AWC observations carry their own true timestamps, which are later than the
scenario's. Asking for "the latest observation" therefore returns the clear-weather archive
and scores the storm **0/100 instead of 80/100**. The caller must pass the clock of the moment
being assessed. There is no default, on purpose: a silent default here is the difference
between a severe incident and no incident at all.

Owner: Stream C.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scenario_queries import load_delay_risk_inputs
from app.models.enums import ActionStatus
from app.models.workflow import Prediction
from app.services.base import ServiceResult
from app.services.delay_risk import DelayRiskService


@dataclass(slots=True)
class PredictionRecord:
    """The persisted assessment, plus the service result it came from."""

    prediction_id: int | None
    result: ServiceResult

    @property
    def risk_index(self) -> int | None:
        value = self.result.payload.get("risk_index")
        return int(value) if value is not None else None

    @property
    def risk_level(self) -> str | None:
        value = self.result.payload.get("risk_level")
        return str(value) if value is not None else None

    @property
    def event_recommended(self) -> bool:
        return bool(self.result.payload.get("event_recommended", False))


async def record_delay_risk_prediction(
    session: AsyncSession,
    *,
    airport_icao: str,
    flight_id: int,
    as_of: datetime,
    event_threshold: int | None = None,
) -> PredictionRecord:
    """Score the airport as of `as_of` and persist the result as a `prediction` row.

    Returns `prediction_id=None` when the service refused — a missing or incomplete
    observation yields `needs_human` and **no row**, because an unscored incident must not
    acquire a prediction record implying it was assessed.
    """
    weather, runways, ruleset = await load_delay_risk_inputs(session, airport_icao, as_of=as_of)

    result = await DelayRiskService().execute(
        weather=weather,
        runways=runways,
        ruleset=ruleset,
        event_threshold=event_threshold,
    )

    if result.status is not ActionStatus.success:
        return PredictionRecord(prediction_id=None, result=result)

    payload = result.payload
    prediction = Prediction(
        flight_id=flight_id,
        airport_icao=airport_icao,
        predicted_at=as_of,
        risk_index=int(payload["risk_index"]),
        risk_level=payload["risk_level"],
        # Both versions are recorded: the rule family and the exact hashed numbers, so a
        # stored prediction can be replayed against the ruleset that produced it.
        rule_version=payload["rule_version"],
        factors=payload["factors"],
        evidence_refs=[
            *result.evidence_refs,
            f"ruleset_hash:{payload['ruleset_hash']}",
        ],
    )
    session.add(prediction)
    await session.flush()

    return PredictionRecord(prediction_id=prediction.id, result=result)
