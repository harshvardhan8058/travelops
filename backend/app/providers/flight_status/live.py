"""Live flight-status provider, against the AviationStack `flights` API.

AviationStack was chosen as the first practical live flight-status source because, unlike an
ADS-B position feed, it returns scheduled-versus-actual times and a per-leg delay directly —
the exact inputs the connection and recovery services need — behind a single API-key query
parameter with a free developer tier. The full rationale is in
`backend/app/providers/flight_status/README.md`.

This adapter mirrors `app.providers.weather.live` deliberately: an injectable `httpx` client
for testing, typed `ProviderError`s, and a normalisation path shared with the fixture twin so
`WEATHER_MODE`-style fixture replay is exact. A dead vendor API must never be able to block a
checkpoint, and — more importantly — a failed lookup must never look like an on-time flight.

Two AviationStack-specific quirks are handled here:

* **Errors arrive as HTTP 200 with an `{"error": ...}` body**, not as a 4xx/5xx status. The
  usage-limit case in particular (`usage_limit_reached`) is a 200. Mapping it to a typed
  `ProviderError` is what lets the orchestrator fall back instead of parsing a success that
  contains no flights.
* **The key travels in the query string** (`access_key`), so it must never be logged. Only the
  path and the non-secret params are ever put in an error message.

Errors are typed and never mapped to silent success. Returning an empty or default status on
failure is the one outcome that must never happen.

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
)
from app.providers.flight_status.normalise import normalise_status_row

BASE_URL = "https://api.aviationstack.com/v1"

PROVIDER_NAME = "aviationstack"

DEFAULT_TIMEOUT_SECONDS = 8.0

#: A live status older than this is stale. Kept in step with
#: `app.assurance.contract.FreshnessLimits.flight_status_minutes`, which the gate enforces at
#: five minutes; a provider that flagged staleness later than the gate would let a source the
#: gate rejects still look fresh in the UI.
DEFAULT_MAX_STATUS_AGE_MINUTES = 5

#: AviationStack `error.code` values that mean "your access is the problem", mapped to the
#: typed kinds the orchestrator branches on.
_FORBIDDEN_ERROR_CODES = frozenset(
    {"invalid_access_key", "missing_access_key", "inactive_user", "function_access_restricted"}
)
_RATE_LIMIT_ERROR_CODES = frozenset({"usage_limit_reached", "rate_limit_reached"})


def _raise_for_status(response: httpx.Response) -> None:
    """Map an HTTP status to a typed error. AviationStack mostly answers 200, so this is the
    second line of defence behind `_raise_for_api_error`."""
    status = response.status_code
    if status == 429:
        raise ProviderError(
            ProviderErrorKind.rate_limited,
            f"AviationStack rate limited the request (HTTP {status})",
            provider=PROVIDER_NAME,
        )
    if status in {401, 403}:
        raise ProviderError(
            ProviderErrorKind.forbidden,
            f"AviationStack refused the request (HTTP {status})",
            provider=PROVIDER_NAME,
        )
    if status >= 500:
        raise ProviderError(
            ProviderErrorKind.unavailable,
            f"AviationStack returned HTTP {status}",
            provider=PROVIDER_NAME,
        )
    if status >= 400:
        raise ProviderError(
            ProviderErrorKind.invalid_response,
            f"AviationStack returned HTTP {status}",
            provider=PROVIDER_NAME,
        )


def _raise_for_api_error(payload: Any) -> None:
    """AviationStack signals failure with a 200 and an `error` object. Map it to a typed error.

    The `error` block looks like `{"code": "usage_limit_reached", "message": "..."}`. An
    unrecognised code is treated as `unavailable` rather than `invalid_response`, because the
    safe fallback for "the vendor is unhappy for a reason we do not model" is to defer, not to
    declare the vendor broken.
    """
    if not isinstance(payload, dict):
        return
    error = payload.get("error")
    if not error:
        return
    code = ""
    if isinstance(error, dict):
        code = str(error.get("code") or error.get("type") or "").lower()
    message = ""
    if isinstance(error, dict):
        message = str(error.get("message") or code or "unknown error")
    else:
        message = str(error)

    if code in _RATE_LIMIT_ERROR_CODES:
        kind = ProviderErrorKind.rate_limited
    elif code in _FORBIDDEN_ERROR_CODES:
        kind = ProviderErrorKind.forbidden
    else:
        kind = ProviderErrorKind.unavailable
    raise ProviderError(kind, f"AviationStack error: {message}", provider=PROVIDER_NAME)


class LiveFlightStatusProvider:
    """Implements `app.providers.base.FlightStatusProvider` against AviationStack.

    `flight_index` maps the domain's integer `flight_id` to the identifier AviationStack knows
    the flight by (an IATA flight number such as ``AI2811``). The domain has no notion of an
    external flight number, so the caller supplies this mapping; without an entry the provider
    reports `unavailable` rather than guessing a flight number from an id.
    """

    name = PROVIDER_NAME
    mode = "live"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = BASE_URL,
        flight_index: dict[int, str] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_status_age_minutes: int = DEFAULT_MAX_STATUS_AGE_MINUTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._flight_index = dict(flight_index or {})
        self._timeout = timeout_seconds
        self._max_age = max_status_age_minutes
        self._client = client

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    def _flight_number_for(self, flight_id: int) -> str:
        number = self._flight_index.get(flight_id)
        if not number:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"no external flight number is mapped for flight_id={flight_id}; "
                "the domain id cannot be resolved to an AviationStack flight",
                provider=self.name,
            )
        return number

    async def _get_json(self, path: str, params: dict[str, str]) -> Any:
        # The key is added here and only here, so it never reaches a caller-built error string.
        query = {**params, "access_key": self._api_key}
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self._base_url}/{path}", params=query, timeout=self._timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(f"{self._base_url}/{path}", params=query)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                ProviderErrorKind.timeout,
                f"AviationStack did not respond within {self._timeout}s",
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"AviationStack is unreachable: {exc}",
                provider=self.name,
            ) from exc

        _raise_for_status(response)

        if response.status_code == 204 or not response.content.strip():
            raise ProviderError(
                ProviderErrorKind.unavailable,
                "AviationStack returned no content",
                provider=self.name,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                ProviderErrorKind.invalid_response,
                "AviationStack returned a body that is not JSON",
                provider=self.name,
            ) from exc

        _raise_for_api_error(payload)
        return payload

    def _select_row(self, payload: Any, *, flight_number: str) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError(
                ProviderErrorKind.invalid_response,
                f"AviationStack response for {flight_number} has no data list",
                provider=self.name,
            )
        rows = [row for row in payload["data"] if isinstance(row, dict)]
        if not rows:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"AviationStack has no current status for {flight_number}",
                provider=self.name,
            )

        # Newest scheduled departure wins when the vendor returns several instances of a flight
        # number across days. Missing schedules sort last.
        def _key(row: dict[str, Any]) -> str:
            departure = row.get("departure") if isinstance(row.get("departure"), dict) else {}
            return str(departure.get("scheduled") or "")

        return max(rows, key=_key)

    def _stamp(self, normalised: dict[str, Any], *, now: datetime) -> ProvenanceStamp:
        observed = normalised.get("revised_departure") or normalised.get("scheduled_departure")
        age_minutes = 0
        if observed is not None:
            age_minutes = max(0, int((now - observed).total_seconds() // 60))
        return ProvenanceStamp(
            kind=ProvenanceKind.real,
            provider=self.name,
            source_ref=f"flight_status:{normalised['flight_id']}:{now.isoformat()}",
            observed_at=observed,
            retrieved_at=now,
            is_stale=age_minutes > self._max_age,
        )

    async def health(self) -> ProviderHealth:
        """Never raises. A probe that crashes is worse than one reporting down.

        With no key configured the provider is honestly down rather than pretending: a live
        mode that reports healthy on an empty key would mask a misconfiguration until the first
        real lookup failed inside an incident.
        """
        checked_at = self._now()
        if not self._api_key:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=False,
                detail="no AviationStack API key configured",
                checked_at=checked_at,
            )
        try:
            payload = await self._get_json("flights", {"limit": "1"})
            healthy = isinstance(payload, dict) and isinstance(payload.get("data"), list)
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=healthy,
                detail=None if healthy else "AviationStack returned an unexpected shape",
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

    async def get_status(self, flight_id: int) -> dict[str, Any]:
        """Current status for one domain flight, normalised and provenance-stamped.

        The returned dict is `normalise_status_row(...)` plus a `provenance` block. A missing
        flight, a usage limit, a timeout — all surface as a typed `ProviderError`, never as an
        on-time default.
        """
        flight_number = self._flight_number_for(flight_id)
        payload = await self._get_json("flights", {"flight_iata": flight_number})
        row = self._select_row(payload, flight_number=flight_number)
        now = self._now()
        normalised = normalise_status_row(row, flight_id=flight_id, provider=self.name)
        normalised["provenance"] = self._stamp(normalised, now=now).model_dump(mode="json")
        return normalised

    async def apply_simulated_transition(self, flight_id: int, status: str) -> dict[str, Any]:
        """Not available against a live source.

        Simulated transitions belong to the fixture/simulator path. A live provider must never
        pretend to have moved a real flight, so this fails closed with a typed error rather
        than returning a fabricated transition.
        """
        raise ProviderError(
            ProviderErrorKind.forbidden,
            "the live flight-status provider cannot apply a simulated transition; "
            "use the fixture provider for simulated state changes",
            provider=self.name,
        )
