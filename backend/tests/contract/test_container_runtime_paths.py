"""Guard the container runtime paths.

These assertions exist because of a real failure. `fixtures/api/` lives at the repository
root, but the Docker build contexts are `./backend` and `./frontend`, so it is never copied
into either image. Two components resolve it to `/fixtures/api` at runtime:

  * backend/app/api/fixtures_router.py  -> every fixture endpoint 404s without the mount
  * frontend/scripts/sync-fixtures.mjs -> the predev hook dies, `npm run dev` never starts,
    and :5173 refuses the connection with nothing obviously wrong

Both symptoms are confusing and neither is caught by a unit test or a type check, so the
mount is asserted here instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"

#: The path both components resolve to inside their container.
CONTAINER_FIXTURE_PATH = "/fixtures"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _volumes(compose: dict, service: str) -> list[str]:
    return compose["services"][service].get("volumes", [])


@pytest.mark.parametrize("service", ["api", "web"])
def test_repo_root_fixtures_are_mounted(compose: dict, service: str):
    """Both containers must see repo-root fixtures/ at /fixtures."""
    mounts = _volumes(compose, service)
    assert any(m.startswith("./fixtures:") and CONTAINER_FIXTURE_PATH in m for m in mounts), (
        f"service '{service}' does not mount ./fixtures at {CONTAINER_FIXTURE_PATH}. "
        "The build context cannot reach repo-root fixtures/, so it must be a volume."
    )


def test_api_fixture_dir_still_resolves_to_the_mounted_path():
    """If FIXTURE_DIR is refactored, the compose mount must move with it."""
    source = (REPO_ROOT / "backend/app/api/fixtures_router.py").read_text(encoding="utf-8")
    match = re.search(r"FIXTURE_DIR\s*=\s*Path\(__file__\)\.resolve\(\)\.parents\[(\d+)\]", source)
    assert match, "FIXTURE_DIR definition changed; re-check the compose mount"

    # /app/app/api/fixtures_router.py -> parents[3] is /
    parents_index = int(match.group(1))
    assert parents_index == 3, (
        f"FIXTURE_DIR now uses parents[{parents_index}]; inside the container that no longer "
        f"resolves to {CONTAINER_FIXTURE_PATH}/api. Update docker-compose.yml to match."
    )


def test_sync_fixtures_degrades_instead_of_killing_the_dev_server():
    """A predev crash presents as 'connection refused' with no obvious cause."""
    script = (REPO_ROOT / "frontend/scripts/sync-fixtures.mjs").read_text(encoding="utf-8")
    assert "existsSync" in script, "sync-fixtures must check the source exists"
    assert "process.exit(0)" in script, (
        "sync-fixtures must exit 0 when the source is missing. A non-zero predev exit stops "
        "`npm run dev` from starting at all."
    )


@pytest.mark.parametrize(
    ("context", "excluded"),
    [("frontend", "node_modules"), ("backend", ".venv")],
)
def test_dockerignore_excludes_host_dependency_directories(context: str, excluded: str):
    """`COPY . .` after install would otherwise overwrite container deps with host ones."""
    path = REPO_ROOT / context / ".dockerignore"
    assert path.is_file(), f"{context}/.dockerignore is missing"
    entries = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert excluded in entries, f"{context}/.dockerignore must exclude '{excluded}'"


def test_datastores_are_not_published_to_the_host(compose: dict):
    """Regression guard on the loopback-only posture."""
    for service in ("postgres", "redis"):
        assert "ports" not in compose["services"][service], (
            f"{service} must not publish a host port; use `docker compose exec` instead"
        )

    for service in ("api", "web"):
        published = compose["services"][service]["ports"]
        assert published, f"{service} must publish a port for the demo to be reachable"
        for mapping in published:
            assert mapping.startswith("127.0.0.1:"), (
                f"{service} publishes {mapping} beyond loopback"
            )
