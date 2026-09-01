"""API router assembly.

Real routers are registered before the fixture router, so if a path ever appears in both the
real implementation wins rather than the outcome depending on import order. In practice a
replaced fixture route is deleted in the same commit, so the situation should not arise.

Endpoint status after the Phase 2 increment:

| Path | Status |
| --- | --- |
| `/health/*`, `/system/mode` | real (Stream A) |
| `/incidents/{ref}` | real (Stream A) |
| `/incidents/{ref}/timeline` | real (Stream A) |
| `/incidents/{ref}/assurance` | real (Stream A route, Stream B decisions) |
| `/incidents/{ref}/actions/{id}` | real (Stream A) |
| `/incidents/{ref}/replay` | real (Stream A) |
| `/incidents/{ref}/plans`, `/plans/comparison`, `/plans/{id}/select` | real (Stream A) |
| `POST /incidents/{ref}/run` | real (Stream A) |
| `POST /assurance/{id}/decision` | real (Stream A) |
| `/incident-groups`, `/incident-groups/current`, `/incident-groups/{ref}` | real (Stream A + C) |
| `/incident-groups/{ref}/blast-radius`, `/graph`, `/replay` | real (Stream A + C) |
| `POST /incident-groups/{ref}/open`, `/run`, `/what-if` | real (Stream A + C) |
| `/incident-groups/{ref}/assurance` + `POST .../assurance/decision` | real (Stream A + B) |
| `/flights` | real (Stream A) — persisted flights, null where nothing has been assessed |
| `/demo/dataset`, `/demo/simulations` | real (Stream A) — read-only demo control |
| `POST /demo/reset` | real (Stream A) — destructive, demo envs only, typed confirmation |
| `/sources` | fixture — Stream C's providers and loaders |
| `/incidents/{ref}/policy` | real (Stream A route, Stream B engine) |
| `/reports/{id}` | fixture — the Report Generator |

Every real endpoint declares a Pydantic `response_model`. The fixture routes return `Any`,
which is why their OpenAPI schemas render as `"string"`; that pattern is not carried forward.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    assurance_router,
    demo,
    fixtures_router,
    flights,
    health,
    incident_groups,
    incidents,
    plans,
    policy,
    reasoning,
    replay,
    scenarios,
)

router = APIRouter()
router.include_router(health.router)

# Real endpoints.
router.include_router(incidents.router)
router.include_router(assurance_router.router)
router.include_router(incident_groups.router)
router.include_router(plans.router)
router.include_router(policy.router)
router.include_router(replay.router)
router.include_router(reasoning.router)
router.include_router(scenarios.router)
router.include_router(flights.router)
router.include_router(demo.router)

# Fixture-backed remainder. Each owning stream replaces its section in place, keeping the
# response shape identical so the frontend never has to change.
router.include_router(fixtures_router.router)
