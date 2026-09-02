"""The provenance ledger contract.

This file exists because the ledger used to be a hand-written JSON document. It named `groq`
as the reasoning provider while the configured default was OpenRouter, it reported
`current_mode: off` whatever `LLM_MODE` actually was, and it put `kind: real` on a row whose own
`current_mode` said `fixture` and on a source that had never been called at all. Every one of
those is a claim about the running system, and none of them was read from the running system.

Two questions get conflated whenever a ledger has one status column, so this contract keeps them
apart by construction:

`kind` answers **what the data is** — a real external record, a synthetic row this project
generated, a simulated outcome, a committed fixture, or nothing at all.

`usage` answers **whether this source served the run you are looking at**. A provider can be
configured, healthy and completely uninvolved. `unused` is the honest word for that, and it is a
different fact from `unavailable`, which means something was asked and could not answer.

The pair is what makes the ledger readable: `kind: real, usage: unused` is the Open-Meteo row —
a genuine external source that this deployment never calls — and it is not the same statement as
`kind: fixture, usage: used`, which is a committed snapshot standing in for a live read.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.provenance import ProvenanceKind


class SourceUsage(StrEnum):
    """Whether this source actually served the current run.

    Deliberately not merged with `kind`. `kind` is a property of the data; `usage` is a property
    of this process's behaviour, and the two disagree constantly — which is the entire reason the
    old ledger read as incoherent.
    """

    #: Something in this run read from it, and there is a recorded artefact to point at.
    used = "used"
    #: Registered and possibly configured, but nothing asked it. Not a fault.
    unused = "unused"
    #: It would be asked, and it cannot answer. `usage_detail` says why.
    unavailable = "unavailable"


class SourceRow(BaseModel):
    """One row of the ledger, every field derived from settings or from recorded rows."""

    model_config = ConfigDict(extra="forbid")

    name: str
    #: What this source is for, in the product's own terms. Answers "why is this row here?".
    role: str
    kind: ProvenanceKind
    provider: str
    #: The model identifier where the provider has one. Null everywhere else.
    model: str | None = None
    #: The effective mode as the running process resolved it — never the requested one.
    current_mode: str
    #: Whether the credential or configuration this source needs is present. Never the key.
    configured: bool
    usage: SourceUsage
    #: One sentence a reader can act on. For `unavailable`, the actual reason.
    usage_detail: str
    #: What backs the `usage` claim — a row count, an artefact reference. Null when `unused`,
    #: because there is deliberately nothing to show.
    evidence: str | None = None
    last_checked: datetime | None = None
    licence: str
    attribution_required: bool = False
    #: Free text, kept from the original contract so existing consumers keep working.
    health: str
    note: str | None = None


class SourcesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceRow] = Field(default_factory=list)
    #: Sources with `kind: real` AND `usage: used`. Both facts, never one standing for the other.
    live_count: int = 0
    unused_count: int = 0
    unavailable_count: int = 0
    note: str
