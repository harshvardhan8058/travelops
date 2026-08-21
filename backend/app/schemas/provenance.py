"""The provenance contract.

Every response carrying external or seeded data states where that data came from.
A UI component must never infer provenance from a provider name.

Owner: Stream A (contract). All streams populate it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ProvenanceKind(StrEnum):
    real = "real"
    simulated = "simulated"
    synthetic = "synthetic"
    fixture = "fixture"
    unavailable = "unavailable"


class Provenance(BaseModel):
    kind: ProvenanceKind
    provider: str = Field(description="awc | ourairports | aikosh | local-simulator | generator")
    source_ref: str | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime | None = None
    is_stale: bool = False

    @classmethod
    def synthetic_data(cls, provider: str = "generator", source_ref: str | None = None) -> Provenance:
        return cls(kind=ProvenanceKind.synthetic, provider=provider, source_ref=source_ref)

    @classmethod
    def from_fixture(cls, source_ref: str) -> Provenance:
        return cls(kind=ProvenanceKind.fixture, provider="fixture", source_ref=source_ref)
