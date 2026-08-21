"""Communication service — STREAM D.

Render approved templates and dispatch through the notification provider.

Real sends go ONLY to the configured allowlist. Every other recipient produces a
notification row with delivery_mode=simulated. Three real emails and 177 simulated is
honest; implying all 180 were delivered is not.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class CommunicationService:
    name = "communication"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: template render + provider dispatch")
