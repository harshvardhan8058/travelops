"""Crew Impact service — STREAM D.

Walk pairing legs FORWARD from the affected flights and report which pairings are at risk,
each with the mechanism that put it at risk:

    operating       crew are working the affected flight
    onward_duty     a later leg of the same pairing is now infeasible
    second_pairing  cockpit and cabin sit on different pairings
    positioning     crew were deadheading to operate another flight

The mechanism becomes the edge label in the cascade graph, which is what lets a reviewer
read why nine rotations are affected by eight flights instead of trusting a headline.

SCOPE BOUNDARY: coordination and display only. This service must NEVER validate duty-time
legality or generate a legal replacement roster.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class CrewImpactService:
    name = "crew_impact"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: recursive walk over pairing_leg")
