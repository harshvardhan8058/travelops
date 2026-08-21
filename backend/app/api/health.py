"""Health and system-mode endpoints.

/health/live   - process alive, no dependency checks
/health/ready  - dependency status; 503 when a hard dependency is down
/system/mode   - effective modes and provenance posture. Never contains secrets.

Owner: Stream A.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.config import get_modes, get_settings
from app.observability.logging import get_logger

router = APIRouter(tags=["system"])
log = get_logger(__name__)


@router.get("/health/live", summary="Liveness")
async def live() -> dict[str, str]:
    return {"status": "alive"}


async def _check_database() -> dict[str, Any]:
    try:
        from app.db.session import get_sessionmaker

        factory = get_sessionmaker()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "up"}
    except Exception as exc:
        return {"status": "down", "detail": type(exc).__name__}


async def _check_redis() -> dict[str, Any]:
    try:
        from redis.asyncio import from_url

        client = from_url(get_settings().redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return {"status": "up"}
    except Exception as exc:
        return {"status": "down", "detail": type(exc).__name__}


@router.get("/health/ready", summary="Readiness with dependency status")
async def ready(response: Response) -> dict[str, Any]:
    database = await _check_database()
    redis = await _check_redis()
    modes = get_modes()

    hard_dependencies_up = database["status"] == "up" and redis["status"] == "up"
    ready_state = hard_dependencies_up and modes.workflow_executable

    if not ready_state:
        response.status_code = 503

    return {
        "status": "ready" if ready_state else "not_ready",
        "dependencies": {"database": database, "redis": redis},
        "assurance": {
            "config_present": modes.assurance_config_present,
            "config_version": modes.assurance_config_version,
            "workflow_executable": modes.workflow_executable,
        },
        "degradations": modes.degradations,
    }


@router.get("/system/mode", summary="Effective runtime modes")
async def system_mode() -> dict[str, Any]:
    settings = get_settings()
    modes = get_modes()
    payload = modes.to_dict()
    payload["app_env"] = settings.app_env.value
    payload["policy_pack"] = {
        "id": settings.policy_pack_id,
        "version": settings.policy_pack_version,
        # Rendered verbatim by the UI badge. Never upgraded by hand.
        "ui_label": _policy_ui_label(modes.policy.value, settings),
    }
    payload["data_seed"] = settings.data_seed
    payload["limits"] = {
        "max_workflow_steps": settings.max_workflow_steps,
        "action_timeout_seconds": settings.action_timeout_seconds,
    }
    return payload


def _policy_ui_label(mode: str, settings: Any) -> str:
    if mode == "demo":
        return "DEMO FIXTURE · fictional policy · no legal claim"
    if mode == "charter":
        return "MoCA PASSENGER CHARTER · FEB 2019 · PENDING CAR VERIFICATION"
    return f"VERIFIED · {settings.policy_pack_id} {settings.policy_pack_version}"
