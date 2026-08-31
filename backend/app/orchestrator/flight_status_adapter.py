"""Observed flight status as an input to the connection walk.

Stream C built the provider (`app.providers.flight_status`), the normaliser, and the mapping
from a vendor status to a `SegmentFlight` (`app.services.flight_status_ingest`). None of it had
a caller: the services are pure and hold no session, so nothing could translate a domain
`flight_id` into the IATA number AviationStack knows, and nothing could put the answer in front
of the connection service. This module is that seam and nothing more.

It sits in `app/orchestrator/` rather than `app/services/` deliberately. A service here takes
value objects and returns a `ServiceResult` with no session and no I/O — that purity is what
makes the no-LLM boundary a thin surface and the services trivially testable. Something that
holds an `AsyncSession` and makes a network call is not a service, and putting it there would
quietly retire the rule. `assurance_adapter` and `service_registry` are the existing precedents:
the orchestrator is the layer that knows which incident and which flights are in scope.

## What it does, and what it deliberately does not

An observed status contributes **one** thing to the analysis: the departure delay actually
being reported. It overlays that onto the `SegmentFlight` the domain already built, leaving the
flight number, the airports and both scheduled times as the database recorded them.

That asymmetry is the important design point. A connection breaks when the *revised* arrival of
the inbound flight lands too close to the *sold* departure of the onward one. If a live source
were allowed to replace the schedule as well, an external feed could redefine the times the
tickets were sold against — and the walk would then be comparing a vendor's idea of the
timetable with itself, silently, while reporting a passenger count. The schedule stays the
domain's; only the disruption is observed.

A cancellation is not a delay, so it is not overlaid as one. `segment_from_status` already
refuses to build a segment for a cancelled flight, and that refusal is recorded here as an
unusable lookup rather than converted into a very large delay — which would let the walk
"recover" a cancelled flight if some later onward departure happened to fit.

## Failure is recorded, never absorbed

Every lookup that cannot be used is named in `FlightStatusOverlay.unusable` with the reason the
provider or the ingest mapping gave, and the whole record travels into the action's payload and
evidence refs through the existing dispatch path. The domain's own derived delay stands in that
case, because it is the only figure the system actually has — but nothing reports a live source
it did not get. A missing status must never read as an on-time flight.

Owner: Stream A (this seam) / Stream C (the provider, the normaliser and the ingest mapping).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import FlightStatusMode, Settings, get_settings
from app.models.reference import Flight
from app.observability.logging import get_logger
from app.services.connection import SegmentFlight
from app.services.flight_status_ingest import FlightStatusIngestResult, ingest_flight_status

log = get_logger(__name__)

#: Key under which the overlay is recorded in the service result's payload, and therefore in the
#: `action` row and the decision-log detail the existing dispatch path already writes.
PAYLOAD_KEY = "flight_status"


def external_flight_number(flight_number: str) -> str:
    """``"6E 2134"`` -> ``"6E2134"``.

    The domain stores an IATA flight number with a space, because that is how an operator reads
    it and how every fixture and assertion in the repository spells it. AviationStack's
    ``flight_iata`` has no space (``6E512``, ``AI2811`` in the committed snapshot). Passing the
    stored string through unchanged is a lookup that always misses, and a lookup that always
    misses is indistinguishable from a flight the vendor does not track — so it would have
    surfaced as a plausible "no current status" rather than as the mapping bug it is.

    Whitespace only. It does not invent a carrier prefix or reformat a number, so a flight number
    the vendor genuinely does not know still fails as itself.
    """
    return "".join(str(flight_number).split())


async def build_flight_index(
    session: AsyncSession, flight_ids: set[int] | frozenset[int]
) -> dict[int, str]:
    """Map the domain's `flight_id` to the identifier the vendor knows the flight by.

    `LiveFlightStatusProvider` requires this and refuses to guess: without an entry it reports
    `unavailable` rather than deriving a flight number from an integer id. The provider factory
    cannot build it — it has no session — which is the one genuine hole in the seam Stream C
    left, and closing it is this module's job rather than a change to their package.

    Scoped to the flights actually in scope, so a connection check on one flight does not read
    the whole flight table.
    """
    if not flight_ids:
        return {}
    rows = (
        await session.execute(
            select(Flight.id, Flight.flight_number).where(Flight.id.in_(set(flight_ids)))
        )
    ).all()
    return {int(flight_id): external_flight_number(number) for flight_id, number in rows}


@dataclass(frozen=True)
class FlightStatusOverlay:
    """What an observed-status sweep changed, and what it could not.

    Both halves matter. `applied` is the evidence that live data reached the decision; `unusable`
    is the evidence that it did not, per flight and with the provider's own reason. A record with
    neither means no sweep was attempted at all, which is the ordinary fixture-mode case.
    """

    mode: str
    #: flight_id -> the delay in minutes now driving the walk, as observed.
    applied: dict[int, int] = field(default_factory=dict)
    #: flight_id -> the delay the domain had derived, kept for traceability when it changed.
    replaced: dict[int, int] = field(default_factory=dict)
    #: flight_id -> why the observed status could not be used.
    unusable: dict[int, str] = field(default_factory=dict)
    #: flight_id -> `real` | `fixture` | `simulated` | `unavailable`, from the provider's stamp.
    provenance_kinds: dict[int, str] = field(default_factory=dict)
    #: Flights whose reading was older than the provider's freshness ceiling. Recorded, not
    #: suppressed: the assurance gate's `sources_fresh` check is the enforcer, not this module.
    stale: list[int] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    #: False when no provider was consulted, so an empty record cannot read as "all lookups failed".
    consulted: bool = False

    @property
    def changed_any_input(self) -> bool:
        """True when an observed delay differs from what the domain had derived."""
        return any(self.applied[fid] != self.replaced.get(fid) for fid in self.applied)

    def as_payload(self) -> dict[str, Any]:
        """The record to attach to the service result, and therefore to the audit trail."""
        return {
            "mode": self.mode,
            "consulted": self.consulted,
            "applied": {str(fid): minutes for fid, minutes in sorted(self.applied.items())},
            "replaced": {str(fid): minutes for fid, minutes in sorted(self.replaced.items())},
            "unusable": {str(fid): reason for fid, reason in sorted(self.unusable.items())},
            "provenance_kinds": {
                str(fid): kind for fid, kind in sorted(self.provenance_kinds.items())
            },
            "stale": sorted(self.stale),
            "changed_decision_input": self.changed_any_input,
        }


def _provider_for(
    mode: FlightStatusMode, *, settings: Settings, flight_index: dict[int, str]
) -> Any:
    """The provider for the resolved mode, with the index the live one needs.

    `get_flight_status_provider()` is Stream C's selector and is used verbatim for fixture. Live
    is constructed directly because the factory builds `LiveFlightStatusProvider` with no
    `flight_index` and therefore refuses every lookup — this supplies the argument their
    constructor already declares. It consumes their interface; it does not fork their selector,
    and the moment the factory learns to take an index this can go back through it.
    """
    # Imported inside the function to keep the provider packages off the orchestrator's import
    # path at module scope, exactly as the reasoning-agent imports are.
    from app.providers.flight_status import (
        LiveFlightStatusProvider,
        get_flight_status_provider,
    )

    if mode is FlightStatusMode.live:
        return LiveFlightStatusProvider(
            api_key=settings.aviationstack_api_key, flight_index=flight_index
        )
    return get_flight_status_provider(mode.value)


async def apply_live_flight_status(
    session: AsyncSession,
    flights: dict[int, SegmentFlight],
    *,
    settings: Settings | None = None,
    mode: FlightStatusMode | None = None,
    provider: Any | None = None,
) -> tuple[dict[int, SegmentFlight], FlightStatusOverlay]:
    """Overlay observed departure delays onto the segments the domain built.

    Returns a new map plus the record of what happened. The input map is never mutated.

    **A sweep only happens in live mode, or when a provider is injected.** In fixture mode the
    map is returned untouched, and that is not a shortcut: the committed snapshot describes the
    vendor's own flight ids, not this dataset's, so consulting it about the seeded cascade would
    report "no current status" for every flight and change nothing while filling the audit trail
    with failures that mean nothing. The seeded `estimated_departure` the domain already derives
    from *is* the fixture flight state. Passing `provider=` is an explicit request and therefore
    always sweeps, which is what lets fixture/live parity be asserted directly.

    Nothing here decides anything. The Decision Assurance Gate still evaluates the action, a
    person still approves what is high risk, and the connection service still does the walk.
    """
    resolved_settings = settings or get_settings()
    resolved_mode = mode if mode is not None else resolved_settings.flight_status_mode

    if provider is None and resolved_mode is not FlightStatusMode.live:
        return dict(flights), FlightStatusOverlay(mode=resolved_mode.value, consulted=False)

    if not flights:
        return dict(flights), FlightStatusOverlay(mode=resolved_mode.value, consulted=False)

    active = provider
    if active is None:
        index = await build_flight_index(session, set(flights))
        active = _provider_for(resolved_mode, settings=resolved_settings, flight_index=index)

    overlay = FlightStatusOverlay(mode=resolved_mode.value, consulted=True)
    updated = dict(flights)

    for flight_id in sorted(flights):
        result = await ingest_flight_status(active, flight_id=flight_id)
        _record(overlay, result)
        if not result.usable or result.delay_minutes is None:
            continue

        domain = flights[flight_id]
        observed = int(result.delay_minutes)
        overlay.replaced[flight_id] = domain.delay_minutes
        overlay.applied[flight_id] = observed
        # Only the delay. The schedule, the airports and the flight number stay the domain's —
        # see the module docstring for why letting an external feed redefine the sold schedule
        # would corrupt the very comparison the connection walk exists to make.
        updated[flight_id] = domain.model_copy(update={"delay_minutes": observed})

    log.info(
        "flight_status_overlay_applied",
        mode=overlay.mode,
        flights=len(flights),
        applied=len(overlay.applied),
        unusable=len(overlay.unusable),
        changed_decision_input=overlay.changed_any_input,
    )
    return updated, overlay


def _record(overlay: FlightStatusOverlay, result: FlightStatusIngestResult) -> None:
    """Fold one lookup into the record, usable or not."""
    overlay.provenance_kinds[result.flight_id] = result.provenance_kind
    for ref in result.evidence_refs:
        if ref not in overlay.evidence_refs:
            overlay.evidence_refs.append(ref)
    if result.is_stale:
        overlay.stale.append(result.flight_id)
    if not result.usable:
        overlay.unusable[result.flight_id] = (
            result.reason or "the provider returned no usable status and gave no reason"
        )
        log.info(
            "flight_status_unusable",
            flight_id=result.flight_id,
            provenance_kind=result.provenance_kind,
            reason=(result.reason or "")[:200],
        )


def merge_into_result(result: Any, overlay: FlightStatusOverlay) -> Any:
    """Attach the overlay record to a `ServiceResult` without touching its verdict.

    Strictly additive: the status, the reason and every count stay exactly as the service
    returned them. `service_registry`'s contract is that no adapter interprets a result, and
    recording which inputs were used is not interpreting one — but rewriting the count or the
    provenance the service chose would be, so neither happens here. The payload carries the
    per-flight provenance the reading arrived with, which is the part an auditor needs.
    """
    if not overlay.consulted:
        return result
    payload = {**(result.payload or {}), PAYLOAD_KEY: overlay.as_payload()}
    evidence = list(result.evidence_refs or [])
    for ref in overlay.evidence_refs:
        if ref not in evidence:
            evidence.append(ref)
    return result.model_copy(update={"payload": payload, "evidence_refs": evidence})


__all__ = [
    "PAYLOAD_KEY",
    "FlightStatusOverlay",
    "apply_live_flight_status",
    "build_flight_index",
    "external_flight_number",
    "merge_into_result",
]
