"""Transport service — STREAM D.

Arrange simulated ground transfers against synthetic vendor capacity.

May be folded into the Hotel service as a cost line if scope is cut — see the cut list in
docs/14-hackathon-plan.md.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class TransportService:
    name = "transport"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: vendor capacity allocation")
