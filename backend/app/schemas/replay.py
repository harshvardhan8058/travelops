"""Replay contracts — STREAM A.

Replay is a **read**, not a subsystem. `decision_log` already holds the full chronology, which is
why `docs/08-blueprint-backlog.md` could call the replay engine "nearly free" while the digital
twin is not.

Three of these fields are not columns, and it is worth being precise about where they come from,
because a reader who assumes they are stored will look for a migration that does not exist:

| Field | Source |
| --- | --- |
| `sequence` | **Derived** — ordinal position in the `(occurred_at, id)` ordering |
| `state_before` / `state_after` | `detail["from"]` / `detail["to"]` on `STATE_CHANGED` rows |
| `assurance_id`, `human_decision_id` | `detail` keys the engine already writes |
| `actor_kind` | The shared `actor_kind` mapping, imported — never re-implemented |

Because `sequence` is positional, "no gaps" is a property of the response rather than a claim
about the table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReplayFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    occurred_at: datetime
    stage: str
    actor: str
    #: The Phase 1 fix, visible through replay: a human decision reads as `human`.
    actor_kind: str
    event_type: str
    summary: str
    state_before: str | None = None
    state_after: str | None = None
    incident_reference: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    assurance_id: int | None = None
    human_decision_id: int | None = None
    #: `action` or `plan` when a human decision is referenced, so a plan-covered action is
    #: distinguishable from a per-action approval. Both are a person's act.
    decision_scope: str | None = None
    plan_approval_id: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class ReplayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Set for an incident replay; null for a group replay.
    incident_reference: str | None = None
    #: Set for a group replay; null for an incident replay.
    group_reference: str | None = None
    frame_count: int
    frames: list[ReplayFrame] = Field(default_factory=list)
    #: Row counts are identical before and after: replay is read-only, asserted by a test.
    is_read_only: bool = True
    note: str
