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
# `/flights` moved to `app/api/flights.py` and now reads persisted state. It was the board the
# Scenario Builder resolves flight ids against, so a fixture here could not agree with the
# validation `POST /scenarios` performs against the real `flight` table — and on the committed
# dataset it did not: one offered flight did not exist and another published a delay the database
# contradicted. `fixtures/api/flights.json` is retained because the console's own offline mode
# (`VITE_USE_FIXTURES=true`) serves it statically, and that mode cannot author scenarios at all.


# `/sources` moved to `app/api/sources.py` and is now derived from the running process. The
# fixture it used to serve was the reason the console's provenance claims disagreed with each
# other: it named a reasoning provider the configuration did not select, published a mode
# unrelated to `LLM_MODE`, and marked never-called sources `real`. `fixtures/api/sources.json` is
# retained for the console's own offline mode (`VITE_USE_FIXTURES=true`), where it describes that
# mode's posture honestly and nothing can drift because nothing is running.


# ---------------------------------------------------------------- Stream A
