"""Gate / Resource service — STREAM D.

Simulated gate and stand reassignment. Lowest demo value of the execution services, so it
is early on the cut list.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class ResourceService:
    name = "resource"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: simulated gate reassignment")
