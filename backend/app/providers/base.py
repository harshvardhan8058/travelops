"""Provider protocols.

Every external dependency sits behind a Protocol with at least two implementations: a live
one and a fixture/simulated one. This is what makes an unavailable vendor API unable to
block a checkpoint demo.

Provider errors are typed. The orchestrator maps them to deterministic fallback or
`needs_human` — never to silent success.

Owner: Stream C.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProvenanceKind


class ProviderErrorKind(StrEnum):
    unavailable = "unavailable"
    timeout = "timeout"
    rate_limited = "rate_limited"
    invalid_response = "invalid_response"
    forbidden = "forbidden"


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.message = message


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    mode: str
    healthy: bool
    detail: str | None = None
    checked_at: datetime | None = None


class ProvenanceStamp(BaseModel):
    """Attached to every value a provider returns. The UI renders this, never infers it."""

    model_config = ConfigDict(extra="forbid")

    kind: ProvenanceKind
    provider: str
    source_ref: str | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    is_stale: bool = False


class WeatherReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airport_icao: str
    observed_at: datetime
    # Units normalised at the boundary. Knots, metres, feet — never km/h.
    wind_speed_kt: int | None = None
    wind_direction_deg: int | None = None
    visibility_m: int | None = None
    ceiling_ft: int | None = None
    precipitation: str | None = None
    raw_metar: str | None = None
    provenance: ProvenanceStamp


@runtime_checkable
class WeatherProvider(Protocol):
    name: str

    async def health(self) -> ProviderHealth: ...
    async def get_observation(self, airport_icao: str) -> WeatherReading: ...
    async def get_forecast(self, airport_icao: str) -> list[WeatherReading]: ...


@runtime_checkable
class FlightStatusProvider(Protocol):
    name: str

    async def health(self) -> ProviderHealth: ...
    async def get_status(self, flight_id: int) -> dict[str, Any]: ...
    async def apply_simulated_transition(self, flight_id: int, status: str) -> dict[str, Any]: ...


class NotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passenger_id: int
    recipient: str
    channel: str
    subject: str | None = None
    body: str


class NotificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passenger_id: int
    channel: str
    # `real` only for allowlisted recipients. Everything else is `simulated`.
    delivery_mode: str
    status: str
    provider_message_id: str | None = None
    sent_at: datetime | None = None


@runtime_checkable
class NotificationProvider(Protocol):
    name: str

    async def health(self) -> ProviderHealth: ...
    async def prepare(self, request: NotificationRequest) -> NotificationRequest: ...
    async def send_allowlisted(self, request: NotificationRequest) -> NotificationResult: ...
    async def record_simulated_bulk(
        self, requests: list[NotificationRequest]
    ) -> list[NotificationResult]: ...


class StructuredRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_name: str
    prompt_version: str
    context: dict[str, Any] = Field(default_factory=dict)
    # The response model the caller expects, by payload_type discriminator.
    expected_payload_type: str


@runtime_checkable
class LLMProvider(Protocol):
    """Live, fixture and off implementations.

    `off` must raise ProviderError(unavailable) so the orchestrator takes the deterministic
    fallback path. It must never fabricate a plan.
    """

    name: str
    mode: str

    async def health(self) -> ProviderHealth: ...
    async def generate_structured(self, request: StructuredRequest) -> dict[str, Any]: ...
