"""Fixture-backed endpoints for Wave 0.

Purpose: give Streams E and F the real response *shapes* on day one, so the frontend is
never blocked on backend progress.

HOW EACH STREAM REPLACES THIS
    Delete your endpoint from this module, implement it in your own router, and register
    that router in app/api/__init__.py. The response shape must stay identical — the
    frontend and the committed fixtures both depend on it.

Every payload here is served from fixtures/api/*.json and is labelled
`provenance.kind = "fixture"`. Nothing in this module reaches a database or a provider.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.errors import EntityNotFound

router = APIRouter(tags=["fixtures"])

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "api"


@lru_cache(maxsize=64)
def _load(name: str) -> Any:
    path = FIXTURE_DIR / f"{name}.json"
    if not path.is_file():
        raise EntityNotFound(
            f"fixture '{name}' not found",
            details={"expected_path": str(path)},
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- Stream C / A
@router.get("/flights", summary="Flight board [fixture]")
async def list_flights() -> Any:
    return _load("flights")


@router.get("/sources", summary="Provenance ledger [fixture]")
async def list_sources() -> Any:
    return _load("sources")


# ---------------------------------------------------------------- Stream A
@router.get("/incident-groups", summary="Cascade summaries [fixture]")
async def list_incident_groups() -> Any:
    return _load("incident_groups")


@router.get("/incident-groups/{group_id}", summary="Cascade detail [fixture]")
async def get_incident_group(group_id: str) -> Any:
    payload = _load("incident_group_detail")
    if payload.get("reference") != group_id and group_id not in {"current", payload.get("id")}:
        # Wave 0 serves a single canonical fixture; accept the alias so the UI can link.
        payload = {**payload, "requested_id": group_id}
    return payload


@router.get("/incidents/{incident_id}", summary="Incident detail [fixture]")
async def get_incident(incident_id: str) -> Any:
    payload = _load("incident_detail")
    return {**payload, "requested_id": incident_id}


@router.get("/incidents/{incident_id}/timeline", summary="Decision timeline [fixture]")
async def get_timeline(incident_id: str) -> Any:
    payload = _load("timeline")
    return {**payload, "incident_id": incident_id}


# ---------------------------------------------------------------- Stream B
@router.get("/incidents/{incident_id}/assurance", summary="Assurance evaluations [fixture]")
async def get_assurance(incident_id: str) -> Any:
    payload = _load("assurance")
    return {**payload, "incident_id": incident_id}


@router.get("/incidents/{incident_id}/policy", summary="Policy evaluation [fixture]")
async def get_policy(incident_id: str) -> Any:
    payload = _load("policy")
    return {**payload, "incident_id": incident_id}


# ---------------------------------------------------------------- Stream D
@router.get("/reports/{incident_id}", summary="Executive report [fixture]")
async def get_report(incident_id: str) -> Any:
    payload = _load("report")
    return {**payload, "incident_id": incident_id}
