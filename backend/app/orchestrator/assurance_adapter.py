"""Fail-closed adapter onto the Decision Assurance Gate — STREAM A.

Execution is authorised by the gate and by nothing else. Stream B owns the six checks and
their aggregation; Stream A only asks. This module is the single place where that ask
happens, so there is exactly one code path to audit.

Stream B's `app/assurance/gate.py` currently exposes `aggregate()` and `load_config()` but
no single entry point that gathers the six checks and returns a decision. **The requested
interface is `gate.evaluate(...) -> AssuranceResult`** (see REQUESTED_ENTRY_POINT). Until
it lands, every call here refuses.

Refusing means `needs_human`, never `execute`. An unimplemented gate is a *hard block*:

* the symbol is missing            -> refuse
* it raises NotImplementedError    -> refuse
* its signature does not match     -> refuse, naming the mismatch
* the config is absent or unloadable -> refuse
* it returns something that is not an AssuranceResult -> refuse

Note what this module does NOT do. It does not evaluate a check, classify a risk tier,
read a policy pack or decide what a warning means. Reimplementing any of that here would
put the safety boundary in two places and guarantee they drift. A refusal record is not an
evaluation, and it is labelled as such: every check comes back FAIL with
`CONFIG_MISSING` and a reason saying the gate did not run.

Owner: Stream A. Interface owner: Stream B.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.assurance.contract import (
    CHECK_ORDER,
    AssuranceResult,
    CheckResult,
    ReasonCode,
)
from app.models.enums import AssuranceDecision, CheckState, RiskTier
from app.observability.logging import get_logger

log = get_logger(__name__)

#: The interface requested from Stream B. Keyword-only so adding a parameter later is not
#: a breaking change.
#:
#:     async def evaluate(
#:         *,
#:         action_type: str,
#:         target_refs: list[str],
#:         inputs: dict,
#:         evidence_refs: list[str],
#:         incident_state: str,
#:         config: AssuranceConfig | None = None,
#:     ) -> AssuranceResult
#:
#: May be sync or async; this adapter awaits it either way.
REQUESTED_ENTRY_POINT = "app.assurance.gate.evaluate"

UNAVAILABLE = "unavailable"


class GateUnavailableError(Exception):
    """The gate could not produce an evaluation. Always becomes a refusal, never a pass."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def refusal(
    reason: str,
    *,
    config_version: str | None = None,
    config_hash: str | None = None,
    evidence_refs: list[str] | None = None,
) -> AssuranceResult:
    """Build the record for "the gate did not evaluate this".

    Every check is FAIL/CONFIG_MISSING carrying the same reason, so nothing downstream can
    mistake this for a real evaluation in which six checks were actually run. The decision
    is `needs_human`: a person decides what happens when the authorisation boundary is
    unavailable.
    """
    checks = [
        CheckResult(
            name=name,
            state=CheckState.failed,
            reason_code=ReasonCode.CONFIG_MISSING,
            reason=reason,
        )
        for name in CHECK_ORDER
    ]
    return AssuranceResult(
        decision=AssuranceDecision.needs_human,
        # Unknown means dangerous, which is the same rule the gate's own config uses.
        risk_tier=RiskTier.high,
        checks=checks,
        blocking=list(CHECK_ORDER),
        evidence_refs=evidence_refs or [],
        config_version=config_version or UNAVAILABLE,
        config_hash=config_hash or UNAVAILABLE,
    )


async def evaluate(
    *,
    action_type: str,
    target_refs: list[str],
    inputs: dict[str, Any],
    evidence_refs: list[str],
    incident_state: str,
    config: Any | None = None,
) -> AssuranceResult:
    """Ask the gate. Raise GateUnavailable if it cannot answer.

    The caller turns that into a refusal record and blocks. This function never returns an
    executable result it did not receive from the gate itself.
    """
    from app.assurance import gate

    entry = getattr(gate, "evaluate", None)
    if entry is None:
        raise GateUnavailableError(
            f"{REQUESTED_ENTRY_POINT} is not implemented yet",
            detail="Stream B owns the gate entry point; Stream A must not substitute one",
        )

    try:
        result = entry(
            action_type=action_type,
            target_refs=target_refs,
            inputs=inputs,
            evidence_refs=evidence_refs,
            incident_state=incident_state,
            config=config,
        )
        if inspect.isawaitable(result):
            result = await result
    except NotImplementedError as exc:
        raise GateUnavailableError(
            f"{REQUESTED_ENTRY_POINT} is still a stub",
            detail=str(exc) or None,
        ) from exc
    except TypeError as exc:
        # Signature drift. Loud, and still a refusal — never a silent pass.
        raise GateUnavailableError(
            f"{REQUESTED_ENTRY_POINT} signature does not match the requested interface",
            detail=str(exc),
        ) from exc

    if not isinstance(result, AssuranceResult):
        raise GateUnavailableError(
            f"{REQUESTED_ENTRY_POINT} returned {type(result).__name__}, not AssuranceResult",
        )
    return result


def load_config() -> Any | None:
    """Load the versioned gate config through Stream B's loader.

    Returns None when the config is unavailable. The caller must treat that as a block:
    `docs/26-implementation-contracts.md` says missing assurance config refuses workflow
    execution, and `Settings.workflow_executable` already encodes it.
    """
    from app.assurance import gate
    from app.config import get_settings, resolve_repo_path

    path = resolve_repo_path(get_settings().assurance_config_path)
    if not path.is_file():
        return None
    try:
        return gate.load_config(str(path))
    except NotImplementedError:
        return None
    except Exception as exc:
        # A config that will not parse is indistinguishable from no config, safety-wise.
        log.error(
            "assurance_config_unloadable",
            outcome="error",
            path=str(path),
            detail=type(exc).__name__,
        )
        return None
