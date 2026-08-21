"""Connection service — STREAM D.

Identify itineraries whose onward segment is no longer feasible after a delay.

Compare the revised arrival of the delayed segment against the scheduled departure of the
next segment on the same booking, allowing the minimum connection time. Return the exact
booking and segment references so the count is traceable rather than asserted.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class ConnectionService:
    name = "connection"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: implement at-risk connection detection")
