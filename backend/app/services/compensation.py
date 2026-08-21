"""Compensation service — STREAM D, but the law lives in STREAM B.

This service assembles the required facts and calls the policy engine. It must NEVER
compute an entitlement itself, and never infer a legal outcome from trigger_type.

It returns whatever the policy engine returns, including needs_human when a required fact
is missing. In demo or charter mode it must not present a figure as current law.
"""

from __future__ import annotations

from app.services.base import ServiceResult


class CompensationService:
    name = "compensation"

    async def execute(self, **kwargs: object) -> ServiceResult:
        raise NotImplementedError("Stream D: gather facts, delegate to app.policy.engine")
