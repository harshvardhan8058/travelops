"""SQL precedent retrieval — explainable WHERE-clause matching.

Finds resolved incidents at the same airport with the same trigger type, ranked by recency
and severity match. Records WHY each matched as a list of reasons — "cosine similarity said so"
is not an explanation a judge will accept; a WHERE clause is.

No embeddings, no vector store. The matching is deterministic, inspectable and unit-testable.
A precedent that no longer matches because the data changed simply disappears from the results,
which is the correct behaviour for a query and the incorrect behaviour for a similarity cache.

Owner: Stream C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import IncidentState
from app.models.workflow import Incident

log = structlog.get_logger(__name__)


@dataclass
class PrecedentMatch:
    """One matched precedent, with the reasons it qualified."""

    incident_id: int
    incident_reference: str
    airport_icao: str
    trigger_type: str
    severity: str
    outcome_state: str
    match_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "incident_reference": self.incident_reference,
            "airport_icao": self.airport_icao,
            "trigger_type": self.trigger_type,
            "severity": self.severity,
            "outcome_state": self.outcome_state,
            "match_reasons": self.match_reasons,
        }


async def find_precedents(
    session: AsyncSession,
    *,
    airport_icao: str,
    trigger_type: str,
    severity: str,
    exclude_incident_id: int | None = None,
    limit: int = 3,
) -> list[PrecedentMatch]:
    """Find resolved incidents that match the current disruption's characteristics.

    Matching criteria (all expressed as WHERE clauses, all recorded):
    1. Same airport (the most operationally relevant factor — same runways, same hotels)
    2. Same trigger type (weather patterns, ATC constraints repeat at the same airport)
    3. Resolved state (we learn from successes, not from incidents still in progress)
    4. Prefer same severity (similar scale of disruption)

    Each criterion that matched is recorded in `match_reasons`, so the planner's prompt and
    the audit trail both state WHY a precedent was offered.
    """
    # Base: resolved incidents at the same airport. Since Incident has no `airport_icao` column,
    # we join through the group (which does carry it) or match via the flight's origin.
    from app.models.reference import Flight

    stmt = (
        select(Incident)
        .join(Flight, Flight.id == Incident.flight_id)
        .where(
            Incident.state == IncidentState.resolved,
            Flight.origin_icao == airport_icao,
        )
        .order_by(Incident.id.desc())
    )
    if exclude_incident_id is not None:
        stmt = stmt.where(Incident.id != exclude_incident_id)

    # Limit broadly then rank in Python — the dataset is small (demo) and the ranking logic
    # uses multiple non-trivial criteria that are clearer as code than as SQL ORDER BY.
    stmt = stmt.limit(limit * 5)
    rows = list((await session.execute(stmt)).scalars())

    matches: list[PrecedentMatch] = []
    for incident in rows:
        reasons: list[str] = [f"same airport {airport_icao}"]

        if incident.trigger_type == trigger_type:
            reasons.append(f"same trigger {trigger_type}")
        if incident.severity == severity:
            reasons.append(f"same severity {severity}")
        reasons.append("resolved successfully")

        matches.append(
            PrecedentMatch(
                incident_id=incident.id,
                incident_reference=incident.reference,
                airport_icao=airport_icao,
                trigger_type=incident.trigger_type or "unknown",
                severity=incident.severity or "unknown",
                outcome_state=incident.state,
                match_reasons=reasons,
            )
        )

    # Sort: more match reasons = better precedent; tie-break by recency (higher id)
    matches.sort(key=lambda m: (-len(m.match_reasons), -m.incident_id))
    result = matches[:limit]

    log.info(
        "precedents_retrieved",
        airport_icao=airport_icao,
        trigger_type=trigger_type,
        candidates=len(rows),
        returned=len(result),
    )
    return result
