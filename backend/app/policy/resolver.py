"""Jurisdiction resolver — STREAM B.

Maps a trip context to the applicable policy pack(s). Applicability is TRI-STATE:

    applicable | not_applicable | undetermined

A missing required fact yields `undetermined`, never `not_applicable`. Collapsing unknown
into false is how a system accidentally denies a passenger an entitlement.

No global "most favourable to the passenger" rule is assumed. Where two packs overlap and
no reviewed conflict rule exists, the result is needs_human.
"""

from __future__ import annotations

from typing import Any

from app.policy.engine import ApplicabilityResult

RESOLVER_VERSION = "resolver-v1"


def resolve(*, trip_context: dict[str, Any], packs: list[Any]) -> list[ApplicabilityResult]:
    """Return one applicability result per candidate pack, with its basis and gaps."""
    raise NotImplementedError("Stream B: implement tri-state applicability resolution")
