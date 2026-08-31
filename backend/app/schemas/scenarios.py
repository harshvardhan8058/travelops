"""Contracts for the authored scenario lifecycle.

A scenario is persisted through the existing ``IncidentGroup`` aggregate and its declared
``IncidentGroupFlight`` membership. These API models name that aggregate from the Scenario
Builder's point of view without introducing a second operational lifecycle.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.models.enums import IncidentState, TriggerType
from app.schemas.cascade import GroupMemberOut, ProvenanceBlock

ScenarioMemberRole = Literal["primary", "affected_departure", "affected_arrival"]


class ScenarioMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_id: int = Field(gt=0)
    role: ScenarioMemberRole
    delay_minutes: int = Field(ge=0, le=32_767)


class ScenarioCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause: TriggerType
    airport_icao: str = Field(min_length=4, max_length=4, pattern=r"^[A-Z]{4}$")
    severity: str = Field(min_length=1, max_length=12, pattern=r"^[a-z][a-z0-9_]*$")
    effective_at: datetime
    actor_id: str = Field(min_length=1, max_length=64)
    members: list[ScenarioMemberInput] = Field(min_length=1, max_length=64)

    @field_validator("airport_icao", mode="before")
    @classmethod
    def normalise_airport_icao(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("effective_at")
    @classmethod
    def require_aware_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise PydanticCustomError(
                "timezone_aware", "effective_at must include a timezone offset"
            )
        return value

    @model_validator(mode="after")
    def validate_declared_membership(self) -> Self:
        flight_ids = [member.flight_id for member in self.members]
        if len(set(flight_ids)) != len(flight_ids):
            raise PydanticCustomError("duplicate_flight", "each flight_id may appear only once")
        primary_count = sum(member.role == "primary" for member in self.members)
        if primary_count != 1:
            raise PydanticCustomError(
                "primary_count", "members must contain exactly one primary flight"
            )
        return self


class ScenarioStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, max_length=64)


class ScenarioMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flight_id: int
    flight_number: str
    role: ScenarioMemberRole
    delay_minutes: int


class ScenarioCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_reference: str
    state: IncidentState
    root_cause: TriggerType
    airport_icao: str
    severity: str
    effective_at: datetime
    members: list[ScenarioMemberOut] = Field(default_factory=list)
    created_by: str
    created_at: datetime
    provenance: ProvenanceBlock
    replayed: bool = False


class ScenarioStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_reference: str
    state: IncidentState
    members: list[GroupMemberOut] = Field(default_factory=list)
    opened_incident_ids: list[int] = Field(default_factory=list)
    blocked_reason: str | None = None
    awaiting_approval_count: int = 0
    started_by: str
    started_at: datetime
    provenance: ProvenanceBlock
    replayed: bool = False
