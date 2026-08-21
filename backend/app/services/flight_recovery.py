"""Flight Recovery service — STREAM D.

Propose and record simulated reaccommodation onto alternative flights.

No real booking, no PSS or GDS call. The provider interface exists so a real integration
could replace the simulator without touching this service.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class FlightRecoveryService:
    name = "flight_recovery"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: simulated reaccommodation")
