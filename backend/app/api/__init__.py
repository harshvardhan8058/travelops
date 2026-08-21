"""API router assembly.

Wave 0 ships health and system-mode as working endpoints, and fixture-backed stubs for
the rest so Streams E and F can build against real shapes immediately.

Router ownership:
    health, system   Stream A
    flights          Stream C (data) + A (routing)
    incidents        Stream A
    assurance        Stream B
    policy           Stream B
    cascade          Stream C
    reports          Stream D
    sources          Stream C
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import fixtures_router, health

router = APIRouter()
router.include_router(health.router)

# Fixture-backed endpoints. Each owning stream replaces its section in place, keeping the
# response shape identical so the frontend never has to change.
router.include_router(fixtures_router.router)
