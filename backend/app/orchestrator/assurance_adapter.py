"""Adapter onto the Decision Assurance Gate — STREAM A.

Execution is authorised by the gate and by nothing else. Stream B owns the six checks and
their aggregation; Stream A only asks. This module is the single place where that ask
happens, so there is exactly one code path to audit.

Stream B has now landed the canonical entry point, so this wires straight to it:

    gate.evaluate(*, inputs: GateInputs, config, config_hash=None, now=None) -> AssuranceResult

The fail-closed wrapper stays, because "the gate is present" and "the gate answered" are
different claims. Every one of these is a refusal, never a pass:

* the symbol is missing                     -> refuse
* it raises NotImplementedError              -> refuse
* its signature no longer matches            -> refuse, naming the mismatch
* the config is absent or will not parse      -> refuse
* it returns something other than an AssuranceResult -> refuse

Note what this module does NOT do. It does not evaluate a check, classify a risk tier, read
a policy pack or decide what a warning means. Reimplementing any of that here would put the
safety boundary in two places and guarantee they drift. Assembling `GateInputs` is the
caller's job by Stream B's own contract — "everything the six checks need, gathered by the
caller" — and gathering facts is not the same as judging them.

A refusal record is not an evaluation, and it is labelled as such: every check comes back
FAIL with `CONFIG_MISSING` and a reason saying the gate did not run.

Owner: Stream A. Interface owner: Stream B.
"""

from __future__ import annotations

import inspect
from datetime import datetime
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

#: Stream B's canonical entry point.
GATE_ENTRY_POINT = "app.assurance.gate.evaluate"

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


def load_config() -> tuple[Any | None, str | None]:
    """Load the versioned gate config and the digest of the bytes it came from.

    Returns `(None, None)` when the config is unavailable. The caller must treat that as a
    block: `docs/26-implementation-contracts.md` says missing assurance config refuses
    workflow execution, and `ResolvedModes.workflow_executable` already encodes it.

    The digest comes from Stream B's loader rather than being recomputed here, so the value
    stored on an evaluation is the same one `GET /system/mode` reports.
    """
    from app.assurance import gate
    from app.config import get_settings, resolve_repo_path

    path = resolve_repo_path(get_settings().assurance_config_path)
    if not path.is_file():
        return None, None
    try:
        with_digest = getattr(gate, "load_config_with_digest", None)
        if with_digest is not None:
            config, digest = with_digest(str(path))
            return config, digest
        return gate.load_config(str(path)), None
    except NotImplementedError:
        return None, None
    except Exception as exc:
        # A config that will not parse is indistinguishable from no config, safety-wise.
        log.error(
            "assurance_config_unloadable",
            outcome="error",
            path=str(path),
            detail=type(exc).__name__,
        )
        return None, None


async def evaluate(
    *,
    action_type: str,
    target_refs: list[str],
    referenced_refs: list[str],
    resolved_entities: dict[str, Any],
    payload: dict[str, Any],
    pending_or_executed: list[dict[str, Any]],
    sources: dict[str, datetime | None],
    required_facts: list[str],
    provided_facts: dict[str, Any],
    constraints: list[dict[str, Any]],
    extra_evidence_refs: list[str],
    config: Any | None,
    config_hash: str | None = None,
    now: datetime | None = None,
) -> AssuranceResult:
    """Ask the gate. Raise GateUnavailableError if it cannot answer.

    The caller turns that into a refusal record and blocks. This function never returns an
    executable result it did not receive from the gate itself.

    `now` is passed through explicitly so an evaluation is reproducible, which is what makes
    a replay meaningful.
    """
    from app.assurance import gate

    entry = getattr(gate, "evaluate", None)
    if entry is None:
        raise GateUnavailableError(
            f"{GATE_ENTRY_POINT} is not implemented",
            detail="Stream B owns the gate entry point; Stream A must not substitute one",
        )
    if config is None:
        raise GateUnavailableError(
            "assurance config is unavailable, so no action can be authorised",
        )

    inputs_model = getattr(gate, "GateInputs", None)
    if inputs_model is None:
        raise GateUnavailableError(f"{GATE_ENTRY_POINT} exists but GateInputs does not")

    try:
        inputs = inputs_model(
            action_type=action_type,
            required_facts=required_facts,
            provided_facts=provided_facts,
            sources=sources,
            referenced_refs=referenced_refs,
            resolved_entities=resolved_entities,
            payload=payload,
            constraints=constraints,
            target_refs=target_refs,
            pending_or_executed=pending_or_executed,
            extra_evidence_refs=extra_evidence_refs,
        )
    except Exception as exc:
        # The inputs contract moved. Loud, and still a refusal.
        raise GateUnavailableError(
            "GateInputs no longer accepts the fields the orchestrator gathers",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    try:
        result = entry(inputs=inputs, config=config, config_hash=config_hash, now=now)
        if inspect.isawaitable(result):
            result = await result
    except NotImplementedError as exc:
        raise GateUnavailableError(
            f"{GATE_ENTRY_POINT} is still a stub",
            detail=str(exc) or None,
        ) from exc
    except TypeError as exc:
        raise GateUnavailableError(
            f"{GATE_ENTRY_POINT} signature does not match the expected interface",
            detail=str(exc),
        ) from exc

    if not isinstance(result, AssuranceResult):
        raise GateUnavailableError(
            f"{GATE_ENTRY_POINT} returned {type(result).__name__}, not AssuranceResult",
        )
    return result
