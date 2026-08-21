"""API router assembly.

Real routers are registered before the fixture router, so if a path ever appears in both the
real implementation wins rather than the outcome depending on import order.

Endpoint status — the incident lifecycle surface is real; the rest is still fixture-backed
and each is waiting on the stream that owns its data:

| Path | Status |
| --- | --- |
| `/health/*`, `/system/mode` | real (Stream A) |
| `/incidents/{ref}` | real (Stream A) |
| `/incidents/{ref}/timeline` | real (Stream A) |
| `/incidents/{ref}/assurance` | real (Stream A route, Stream B decisions) |
| `POST /incidents/{ref}/run` | real (Stream A) |
| `POST /assurance/{id}/decision` | real (Stream A) |
| `/flights`, `/sources` | fixture — needs Stream C's providers and loaders |
| `/incident-groups`, `/incident-groups/{id}` | fixture — needs Stream C's cascade data |
| `/incidents/{ref}/policy` | fixture — needs Stream B's policy evaluation |
| `/reports/{id}` | fixture — needs the Report Generator |

Every real endpoint declares a Pydantic `response_model`. The fixture routes return `Any`,
which is why their OpenAPI schemas render as `"string"`; that pattern is not carried forward.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import assurance_router, fixtures_router, health, incidents

router = APIRouter()
router.include_router(health.router)

# Real endpoints.
router.include_router(incidents.router)
router.include_router(assurance_router.router)

# Fixture-backed remainder. Each owning stream replaces its section in place, keeping the
# response shape identical so the frontend never has to change.
router.include_router(fixtures_router.router)
