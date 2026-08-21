"""Fixture weather provider: archived AWC payloads, replayed offline.

This is what makes an unavailable vendor API unable to block a checkpoint demo. It reads
`fixtures/weather/awc_snapshot.json` — real payloads captured from the Aviation Weather
Center — and normalises them through **the same functions the live provider uses**. If the
two normalised independently, a contract test passing in both modes would prove nothing.

**Every reading it returns is stamped `kind=fixture`, never `real`.** The archived bytes came
from a real source, but a replay is not an observation of current conditions, and the
provenance ledger and every UI badge derive from this field.

The snapshot also carries the injected `bengaluru_storm` conditions, so the scenario the demo
runs is served from the same place as the background weather rather than from a special code
path. That reading is synthesised and says so in the file.

Resolution mirrors `app/api/fixtures_router.py` exactly: repo root locally, `/fixtures` inside
the container, because `./fixtures` is the volume mount and `data/` is not in the image.

Owner: Stream C.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.enums import ProvenanceKind
from app.providers.base import (
    ProvenanceStamp,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
    WeatherReading,
)
from app.providers.weather.live import reading_from_metar, readings_from_taf

#: backend/app/providers/weather/fixture.py -> parents[4] is the repo root locally and `/`
#: inside the container, where ./fixtures is mounted at /fixtures.
FIXTURE_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "weather"
SNAPSHOT_FILE = FIXTURE_DIR / "awc_snapshot.json"

PROVIDER_NAME = "awc-fixture"


@lru_cache(maxsize=1)
def load_snapshot(path: Path = SNAPSHOT_FILE) -> dict[str, Any]:
    if not path.is_file():
        raise ProviderError(
            ProviderErrorKind.unavailable,
            f"weather fixture snapshot not found at {path}",
            provider=PROVIDER_NAME,
        )
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureWeatherProvider:
    """Implements `app.providers.base.WeatherProvider` from the archived snapshot."""

    name = PROVIDER_NAME
    mode = "fixture"

    def __init__(
        self,
        *,
        snapshot_path: Path = SNAPSHOT_FILE,
        inject_scenario: bool = True,
        now: datetime | None = None,
    ) -> None:
        self._path = snapshot_path
        #: When true, the scenario airport returns the injected storm conditions. The demo
        #: runs with this on; a contract test runs it both ways.
        self._inject_scenario = inject_scenario
        #: Frozen clock. A fixture provider whose output moves with the wall clock is not a
        #: fixture, and `is_stale` would flip mid-demo.
        self._now = now

    def _clock(self) -> datetime:
        if self._now is not None:
            return self._now
        snapshot = load_snapshot(self._path)
        retrieved = snapshot.get("source", {}).get("retrieved_at")
        if retrieved:
            parsed = datetime.fromisoformat(str(retrieved).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return datetime.now(tz=UTC)

    def _injected(self) -> dict[str, Any] | None:
        if not self._inject_scenario:
            return None
        return load_snapshot(self._path).get("injected")

    async def health(self) -> ProviderHealth:
        """Never raises."""
        checked_at = self._clock()
        try:
            snapshot = load_snapshot(self._path)
            stations = {row.get("icaoId") for row in snapshot.get("metar", [])}
            healthy = bool(stations)
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=healthy,
                detail=(
                    f"{len(stations)} stations in archived snapshot"
                    if healthy
                    else "snapshot contains no METAR rows"
                ),
                checked_at=checked_at,
            )
        except ProviderError as exc:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=False,
                detail=f"{exc.kind.value}: {exc.message}",
                checked_at=checked_at,
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=False,
                detail=f"unexpected: {exc!r}",
                checked_at=checked_at,
            )

    async def get_observation(self, airport_icao: str) -> WeatherReading:
        snapshot = load_snapshot(self._path)
        now = self._clock()

        injected = self._injected()
        if injected and injected.get("airport_icao") == airport_icao:
            return self._injected_reading(injected, now=now)

        rows = [row for row in snapshot.get("metar", []) if row.get("icaoId") == airport_icao]
        if not rows:
            # VAPO is in the airport set and files no METAR. The fixture mode reproduces that
            # gap rather than inventing calm weather for it.
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"archived snapshot has no METAR for {airport_icao}",
                provider=self.name,
            )

        newest = max(rows, key=lambda row: row.get("obsTime") or 0)
        return reading_from_metar(
            newest,
            now=now,
            provenance_kind=ProvenanceKind.fixture,
            provider=self.name,
        )

    async def get_forecast(self, airport_icao: str) -> list[WeatherReading]:
        snapshot = load_snapshot(self._path)
        rows = [row for row in snapshot.get("taf", []) if row.get("icaoId") == airport_icao]
        if not rows:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"archived snapshot has no TAF for {airport_icao}",
                provider=self.name,
            )

        now = self._clock()
        readings: list[WeatherReading] = []
        for row in rows:
            readings.extend(
                readings_from_taf(
                    row,
                    now=now,
                    provenance_kind=ProvenanceKind.fixture,
                    provider=self.name,
                )
            )
        return readings

    def _injected_reading(self, injected: dict[str, Any], *, now: datetime) -> WeatherReading:
        """The scenario conditions from data/fixtures/bengaluru_storm.yaml.

        Units are already canonical in the file — knots, metres, feet — because the fixture
        is authored against the same boundary rule the providers enforce. The YAML comment
        about 45 km/h being 24 kt is the reason that rule exists.
        """
        observed_raw = injected.get("observed_at")
        observed_at = datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)

        scenario = injected.get("scenario", "injected")
        return WeatherReading(
            airport_icao=injected["airport_icao"],
            observed_at=observed_at,
            wind_speed_kt=injected.get("wind_speed_kt"),
            wind_direction_deg=injected.get("wind_direction_deg"),
            visibility_m=injected.get("visibility_m"),
            ceiling_ft=injected.get("ceiling_ft"),
            precipitation=injected.get("precipitation"),
            raw_metar=injected.get("raw_metar"),
            provenance=ProvenanceStamp(
                kind=ProvenanceKind.fixture,
                provider=self.name,
                source_ref=f"fixture:{scenario}:metar:{injected['airport_icao']}",
                observed_at=observed_at,
                retrieved_at=now,
                # The scenario observation is the current condition for the scenario clock.
                is_stale=False,
            ),
        )
