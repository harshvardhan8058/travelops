"""Policy pack loader — STREAM B.

Enforces the status ladder. This is where POLICY_MODE is honoured:

    demo     loads a fictional fixture. No citation, no real figure.
    charter  loads `official_guidance_dated`. Real cited figures, dated badge.
    verified loads ONLY `approved` packs whose verified_mode_eligible is true.

The charter pack MUST be rejected in verified mode with PACK_NOT_VERIFIED_ELIGIBLE. Test
case `verified_mode_rejects_this_pack` exists for exactly this.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import PolicyMode


def load_pack(*, pack_dir: Path, pack_id: str, version: str, mode: PolicyMode) -> Any:
    """Load, validate and return a policy pack.

    Must reject when:
      * the pack directory or pack.yaml is missing            -> POLICY_PACK_UNAVAILABLE
      * mode is verified and status != approved               -> PACK_NOT_VERIFIED_ELIGIBLE
      * mode is verified and verified_mode_eligible is false  -> PACK_NOT_VERIFIED_ELIGIBLE
      * a rule lacks source_clause_refs while status=approved -> POLICY_PACK_UNAVAILABLE

    Must compute and return the pack hash so every entitlement can be pinned to it.
    """
    raise NotImplementedError("Stream B: implement loader with status-ladder enforcement")
