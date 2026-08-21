"""Analytics / Learning service — STREAM D.

Aggregate metrics from recorded rows only: gate decisions by check, human approve/reject
rates, action outcomes, notification real-vs-simulated counts.

Record incident outcomes so SQL precedent retrieval has signal. Never invent a metric the
records cannot support, and never describe gate outcomes as ground truth.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class AnalyticsLearningService:
    name = "analytics_learning"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: record-derived aggregates only")
