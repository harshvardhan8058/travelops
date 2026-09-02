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

from app.config import get_modes, get_settings, provider_transport
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
    # Which endpoint `live` would actually talk to, resolved by the same function the LLM client
    # uses. Published because `llm_mode` alone cannot answer "live against what?" — and a console
    # that cannot answer it is how a top bar reading LIVE ended up beside a provenance row naming
    # a different provider entirely. The key is never published, only whether one is present.
    transport = provider_transport(settings)
    payload["llm_provider"] = transport.provider.value
    payload["llm_model"] = transport.model
    payload["llm_provider_configured"] = bool(transport.api_key)
    payload["policy_pack"] = _policy_pack_payload(settings)
    payload["data_seed"] = settings.data_seed
    payload["limits"] = {
        "max_workflow_steps": settings.max_workflow_steps,
        "action_timeout_seconds": settings.action_timeout_seconds,
    }
    return payload


def _policy_pack_payload(settings: Any) -> dict[str, Any]:
    """Pack identity and label read from the loaded pack, never composed from the mode.

    This used to derive the label from the requested `POLICY_MODE` with a string switch, which is
    the same defect in three different ways. The switch had drifted out of step with the packs it
    described (it uppercased the charter's `MoCA Passenger Charter · Feb 2019 · pending CAR
    verification`, and carried demo text the fixture pack no longer used), and in the remaining
    branch it *composed* the word "VERIFIED" from a requested mode — a legal standing asserted by
    configuration rather than read from a reviewed pack.

    The pack is the authority for its own label, so the shell now reads the same `LoadedPack` that
    `GET /incidents/{id}/policy` reports. One derivation, so the chip and the citation card cannot
    disagree about the instrument a figure is cited from.

    Identity comes from the loaded pack too, not from `POLICY_PACK_ID`. In demo mode those differ:
    `active_pack_coordinates` resolves demo to the fictional fixture regardless of the configured
    id, so reporting the configured id beside the fixture's label would have put one pack's name
    next to another pack's standing.

    Imported inside the function, as `app/api/policy.py` does, to keep the reasoning/policy layer
    off the import path of the liveness probe.
    """
    from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
    from app.policy.entitlements import active_pack_coordinates, load_active_pack

    pack_id, pack_version = active_pack_coordinates(settings)
    try:
        pack = load_active_pack(settings)
    except (PolicyPackUnavailable, PackNotVerifiedEligible) as exc:
        # The shell must still render, so this reports the coordinates it was asked for and an
        # empty label rather than failing. Empty, not a stand-in string: the console already
        # renders a blank label as "policy pack unknown", and inventing a label here for a pack
        # that would not load is precisely what this function was changed to stop doing.
        log.warning(
            "policy_pack_label_unavailable",
            pack_id=pack_id,
            pack_version=pack_version,
            policy_mode=settings.policy_mode.value,
            reason_code=getattr(exc, "code", "UNKNOWN"),
            detail=str(exc)[:200],
        )
        return {"id": pack_id, "version": pack_version, "ui_label": ""}

    return {
        "id": pack.pack_id,
        "version": pack.version,
        # Rendered verbatim by the UI badge. Never upgraded, recased or composed by hand.
        "ui_label": pack.ui_label,
    }
