"""Hotel service — STREAM D.

Search synthetic inventory and make a simulated reservation.

Budget cap and partner preference come from business_constraint rows, not from literals.
Capacity is deliberately insufficient in the fixture, so partial allocation and
prioritisation must both work.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class HotelService:
    name = "hotel"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: constrained search + simulated reservation")
