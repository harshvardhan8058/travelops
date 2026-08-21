"""Live Aviation Weather Center provider.

The AWC Data API is a US Government machine-to-machine service, public domain, and needs no
API key — which is why it is the primary source. A live observation is the credibility anchor
of the demo: it is the one number a judge can independently verify.

It is also the reason the fixture twin exists. A dead venue network must not be able to stop
a checkpoint, so `WEATHER_MODE=fixture` replays an archived snapshot through exactly the same
normalisation path.

Errors are typed and never mapped to silent success. Returning an empty or default reading on
failure is the one outcome that must never happen, because it looks like a clear sky.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.enums import ProvenanceKind
from app.providers.base import (
    ProvenanceStamp,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
    WeatherReading,
)
from app.providers.weather.normalise import (
    ceiling_ft_from_clouds,
    knots_from_knots,
    precipitation_from_text,
    utc_from_epoch,
    utc_from_iso,
    visibility_m_from_statute_miles,
    wind_direction_from_awc,
)

BASE_URL = "https://aviationweather.gov/api/data"

PROVIDER_NAME = "awc"

#: METAR is issued hourly, so anything older than one missed cycle plus a margin is stale.
#: Injectable rather than a literal in the logic; Stream A can wire it from config once a
#: WEATHER_MAX_OBSERVATION_AGE_MINUTES setting exists.
DEFAULT_MAX_OBSERVATION_AGE_MINUTES = 90

DEFAULT_TIMEOUT_SECONDS = 8.0


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status == 429:
        raise ProviderError(
            ProviderErrorKind.rate_limited,
            f"AWC rate limited the request (HTTP {status})",
            provider=PROVIDER_NAME,
        )
    if status in {401, 403}:
        raise ProviderError(
            ProviderErrorKind.forbidden,
            f"AWC refused the request (HTTP {status})",
            provider=PROVIDER_NAME,
        )
    if status >= 500:
        raise ProviderError(
            ProviderErrorKind.unavailable,
            f"AWC returned HTTP {status}",
            provider=PROVIDER_NAME,
        )
    if status >= 400:
        raise ProviderError(
            ProviderErrorKind.invalid_response,
            f"AWC returned HTTP {status}",
            provider=PROVIDER_NAME,
        )


def reading_from_metar(
    row: dict[str, Any],
    *,
    now: datetime,
    provenance_kind: ProvenanceKind = ProvenanceKind.real,
    provider: str = PROVIDER_NAME,
    max_age_minutes: int = DEFAULT_MAX_OBSERVATION_AGE_MINUTES,
) -> WeatherReading:
    """Normalise one AWC METAR row.

    Shared by the live and fixture providers on purpose: if the two normalised
    independently, a contract test passing in both modes would prove nothing.
    """
    icao = row.get("icaoId")
    if not icao:
        raise ProviderError(
            ProviderErrorKind.invalid_response,
            "AWC METAR row has no icaoId",
            provider=provider,
        )

    observed_at = utc_from_epoch(row.get("obsTime")) or utc_from_iso(row.get("reportTime"))
    if observed_at is None:
        raise ProviderError(
            ProviderErrorKind.invalid_response,
            f"AWC METAR for {icao} has no usable observation time",
            provider=provider,
        )

    age_minutes = max(0, int((now - observed_at).total_seconds() // 60))
    raw = row.get("rawOb")

    return WeatherReading(
        airport_icao=icao,
        observed_at=observed_at,
        wind_speed_kt=knots_from_knots(row.get("wspd")),
        wind_direction_deg=wind_direction_from_awc(row.get("wdir")),
        visibility_m=visibility_m_from_statute_miles(row.get("visib")),
        ceiling_ft=ceiling_ft_from_clouds(row.get("clouds")),
        precipitation=precipitation_from_text(raw, row.get("wxString")),
        raw_metar=raw,
        provenance=ProvenanceStamp(
            kind=provenance_kind,
            provider=provider,
            source_ref=f"metar:{icao}:{observed_at.isoformat()}",
            observed_at=observed_at,
            retrieved_at=now,
            is_stale=age_minutes > max_age_minutes,
        ),
    )


def readings_from_taf(
    row: dict[str, Any],
    *,
    now: datetime,
    provenance_kind: ProvenanceKind = ProvenanceKind.real,
    provider: str = PROVIDER_NAME,
) -> list[WeatherReading]:
    """One reading per forecast period.

    `source_ref` is prefixed `taf:` so a forecast is never mistaken for an observation.
    Training or scoring on a TAF as though it were a METAR is a subtle and common leakage
    bug — see docs/11-data-model.md — and `weather_observation.is_forecast` records it in
    the database.
    """
    icao = row.get("icaoId")
    if not icao:
        raise ProviderError(
            ProviderErrorKind.invalid_response,
            "AWC TAF row has no icaoId",
            provider=provider,
        )

    raw = row.get("rawTAF")
    readings: list[WeatherReading] = []

    for period in row.get("fcsts") or []:
        valid_from = utc_from_epoch(period.get("timeFrom"))
        if valid_from is None:
            continue
        readings.append(
            WeatherReading(
                airport_icao=icao,
                observed_at=valid_from,
                wind_speed_kt=knots_from_knots(period.get("wspd")),
                wind_direction_deg=wind_direction_from_awc(period.get("wdir")),
                visibility_m=visibility_m_from_statute_miles(period.get("visib")),
                ceiling_ft=ceiling_ft_from_clouds(period.get("clouds")),
                precipitation=precipitation_from_text(period.get("wxString")),
                raw_metar=raw,
                provenance=ProvenanceStamp(
                    kind=provenance_kind,
                    provider=provider,
                    source_ref=f"taf:{icao}:{valid_from.isoformat()}",
                    observed_at=valid_from,
                    retrieved_at=now,
                    # A forecast is not stale for being about the future.
                    is_stale=False,
                ),
            )
        )

    return readings


class LiveWeatherProvider:
    """Implements `app.providers.base.WeatherProvider` against the AWC Data API."""

    name = PROVIDER_NAME
    mode = "live"

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_observation_age_minutes: int = DEFAULT_MAX_OBSERVATION_AGE_MINUTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_age = max_observation_age_minutes
        self._client = client

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    async def _get_json(self, path: str, params: dict[str, str]) -> Any:
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self._base_url}/{path}", params=params, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(f"{self._base_url}/{path}", params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                ProviderErrorKind.timeout,
                f"AWC did not respond within {self._timeout}s",
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"AWC is unreachable: {exc}",
                provider=self.name,
            ) from exc

        _raise_for_status(response)

        # AWC answers a station it has no report for with 204 and an empty body. That is
        # "no data", not a malformed response, and it must reach the caller as
        # `unavailable` so the orchestrator can fall back. Observed against VAPO, which
        # files a TAF but no METAR.
        if response.status_code == 204 or not response.content.strip():
            return []

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                ProviderErrorKind.invalid_response,
                "AWC returned a body that is not JSON",
                provider=self.name,
            ) from exc

    async def health(self) -> ProviderHealth:
        """Never raises. A probe that crashes is worse than one reporting down."""
        checked_at = self._now()
        try:
            payload = await self._get_json("metar", {"ids": "VOBL", "format": "json"})
            healthy = isinstance(payload, list) and bool(payload)
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=healthy,
                detail=None if healthy else "AWC returned no observation for VOBL",
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
        payload = await self._get_json("metar", {"ids": airport_icao, "format": "json"})
        if not isinstance(payload, list):
            raise ProviderError(
                ProviderErrorKind.invalid_response,
                f"AWC METAR response for {airport_icao} is not a list",
                provider=self.name,
            )

        rows = [row for row in payload if row.get("icaoId") == airport_icao]
        if not rows:
            # Real case, not hypothetical: VAPO files a TAF but no METAR. Reporting this as
            # `unavailable` lets the orchestrator fall back; returning a blank reading would
            # look like calm, clear weather.
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"AWC has no current METAR for {airport_icao}",
                provider=self.name,
            )

        newest = max(rows, key=lambda row: row.get("obsTime") or 0)
        return reading_from_metar(newest, now=self._now(), max_age_minutes=self._max_age)

    async def get_forecast(self, airport_icao: str) -> list[WeatherReading]:
        payload = await self._get_json("taf", {"ids": airport_icao, "format": "json"})
        if not isinstance(payload, list):
            raise ProviderError(
                ProviderErrorKind.invalid_response,
                f"AWC TAF response for {airport_icao} is not a list",
                provider=self.name,
            )

        rows = [row for row in payload if row.get("icaoId") == airport_icao]
        if not rows:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"AWC has no current TAF for {airport_icao}",
                provider=self.name,
            )

        now = self._now()
        readings: list[WeatherReading] = []
        for row in rows:
            readings.extend(readings_from_taf(row, now=now))
        return readings
