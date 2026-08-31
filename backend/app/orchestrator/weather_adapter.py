"""Live weather observations entering the evidence ledger.

Stream C's Aviation Weather Center provider has been complete since Phase 1 and, like the
flight-status provider before #108, had no application caller: `get_weather_provider()` was
referenced by nothing outside its own tests, and every `weather_observation` row in the database
came from the seeder. `WEATHER_MODE=live` therefore selected an implementation that was never
invoked. This module is the caller.

## One selection rule, not two

The delay/risk pipeline already answers "which observation explains this incident", in
`load_delay_risk_inputs`: the newest **actual** observation (`is_forecast = false`) at or before
the incident's own clock. That rule is load-bearing — it is what stops a storm being scored
against the next morning's clear-weather METAR — so this module does not bypass it, extend it or
duplicate it. It **adds a row to the ledger the rule already reads**, and then lets the rule
decide. The risk service, the ruleset, the `Prediction` row and the gate's freshness check are
all untouched.

## The clock is not negotiable

A live METAR is stamped with the time it was actually observed, and that is what gets persisted.
It is never back-dated to fit the incident being worked, however convenient that would be for a
demo. `WeatherObservation.observed_at` is the evidence timestamp the whole audit trail is
selected and aged against; moving it would make a replay reproduce a number that was never true.

The honest consequence is worth stating plainly, because it surprises people: replaying a
*historical* scenario in live mode does **not** score live weather. The incident clock is in the
past, a METAR observed now is in its future, and the existing rule correctly declines it. That is
the leakage guard working, not a bug — but it must never be invisible, so the caller reports the
retrieved-but-not-scored case explicitly rather than letting a live-badged run quietly reason
from archived data. An incident opened now, which is what a real detection is, scores the live
observation.

## Failure is an enrichment failure, never an incident failure

`_assess_delay_risk` runs inside the assessing step and is not wrapped in a recovery handler, so
anything raised here would fail the incident. Nothing is raised. Every outcome — a timeout, a
rate limit, no current METAR, an airport the reference data does not know, or an unexpected fault
— is returned as a recorded `WeatherIngestOutcome`, and the pipeline proceeds on whatever the
ledger already held. A missing live reading must read as a missing live reading, never as calm,
clear weather.

Owner: Stream A (this seam) / Stream C (the provider, the normaliser and the models).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, WeatherMode, get_settings
from app.models.enums import ProvenanceKind
from app.models.reference import Airport, WeatherObservation
from app.observability.logging import get_logger

log = get_logger(__name__)

#: Key under which the ingest record is attached to the delay-risk journal detail.
DETAIL_KEY = "live_weather_ingest"

#: Journalled when a live observation was retrieved but the incident clock did not select it.
#:
#: Its own event type on purpose. This is the one case where a run configured for live weather
#: reasons from an archived row, and burying it inside another entry's detail is how "live" ends
#: up meaning "live was switched on" rather than "live data was used".
EVENT_LIVE_NOT_SCORED = "WEATHER_LIVE_OBSERVATION_NOT_SCORED"

#: Journalled when live mode was on and no live observation could be obtained at all.
EVENT_LIVE_UNAVAILABLE = "WEATHER_LIVE_UNAVAILABLE"


@dataclass(frozen=True)
class WeatherIngestOutcome:
    """What one live-weather lookup produced, including every way it produced nothing.

    `consulted=False` means no provider was asked — the ordinary fixture-mode case — and is
    deliberately distinct from a lookup that was attempted and failed. An empty record must not
    be readable as a failure, and a failure must not be readable as an absence.
    """

    mode: str
    airport_icao: str
    consulted: bool = False
    #: A new row was written to the ledger.
    persisted: bool = False
    #: The exact observation was already in the ledger, so nothing was written. Not a failure.
    already_recorded: bool = False
    observed_at: datetime | None = None
    source_ref: str | None = None
    provenance_kind: str = ProvenanceKind.unavailable.value
    provider: str | None = None
    #: The provider's own staleness verdict, which the ledger has no column for.
    is_stale: bool = False
    #: Whether the reading is at or before the incident clock, i.e. whether the existing
    #: selection rule can choose it. `None` when nothing was retrieved.
    within_incident_clock: bool | None = None
    reason: str | None = None

    @property
    def retrieved(self) -> bool:
        """True when the provider returned an observation, whether or not it was new."""
        return self.source_ref is not None

    @property
    def usable_as_evidence(self) -> bool:
        """Retrieved, in the ledger, and old enough for the incident being assessed."""
        return self.retrieved and bool(self.within_incident_clock)

    def as_detail(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "airport_icao": self.airport_icao,
            "consulted": self.consulted,
            "retrieved": self.retrieved,
            "persisted": self.persisted,
            "already_recorded": self.already_recorded,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "source_ref": self.source_ref,
            "provenance_kind": self.provenance_kind,
            "provider": self.provider,
            "is_stale": self.is_stale,
            "within_incident_clock": self.within_incident_clock,
            "reason": self.reason,
        }


async def _airport_exists(session: AsyncSession, airport_icao: str) -> bool:
    """Whether the reference data knows this station.

    `weather_observation.airport_icao` is a foreign key to `airport.icao_code`. AWC will happily
    return a station this dataset has never heard of, and inserting it would raise an
    `IntegrityError` on Postgres — from inside the assessing step, where it would fail the
    incident over a missing enrichment. Checked first, and reported as a reason.
    """
    found = await session.scalar(select(Airport.icao_code).where(Airport.icao_code == airport_icao))
    return found is not None


async def _already_recorded(
    session: AsyncSession, *, source_ref: str | None, airport_icao: str, observed_at: datetime
) -> bool:
    """Whether this exact observation is already in the ledger.

    `weather_observation` has no unique constraint, so nothing at the schema level stops the same
    METAR being written on every assessment. Duplicates would not change any score — every read
    path takes the newest row — but they would corrupt the one thing the ledger exists for, which
    is being able to say how many distinct observations were actually seen.

    `source_ref` is unique by construction for a live reading (`metar:<ICAO>:<observed_at>`), so
    it is the natural key. The airport-and-time pair is the fallback for a reading that carries
    no source ref, which is the same identity expressed less precisely.
    """
    if source_ref:
        existing = await session.scalar(
            select(WeatherObservation.id).where(WeatherObservation.source_ref == source_ref)
        )
        return existing is not None
    existing = await session.scalar(
        select(WeatherObservation.id).where(
            WeatherObservation.airport_icao == airport_icao,
            WeatherObservation.observed_at == observed_at,
            WeatherObservation.is_forecast.is_(False),
        )
    )
    return existing is not None


def _row_from_reading(reading: Any) -> WeatherObservation:
    """Build the ledger row from a normalised reading.

    Column-for-column what `app.db.seed._weather_row` writes for an archived METAR, so a live row
    and a seeded one are the same shape and every existing read path treats them alike. The only
    differences are the ones that must differ: the provenance kind, the provider and the source
    ref, which is exactly how the audit and UI layers tell live from fixture.

    `is_forecast=False` is stated rather than defaulted: `get_observation` returns a METAR, and a
    TAF reaching this row without the flag set is the leakage bug the column was added to prevent.
    """
    return WeatherObservation(
        airport_icao=reading.airport_icao,
        observed_at=reading.observed_at,
        is_forecast=False,
        wind_speed_kt=reading.wind_speed_kt,
        wind_direction_deg=reading.wind_direction_deg,
        visibility_m=reading.visibility_m,
        ceiling_ft=reading.ceiling_ft,
        precipitation=reading.precipitation,
        raw_metar=reading.raw_metar,
        provenance_kind=reading.provenance.kind,
        provenance_provider=reading.provenance.provider,
        source_ref=reading.provenance.source_ref,
    )


async def ingest_live_weather(
    session: AsyncSession,
    airport_icao: str,
    *,
    as_of: datetime | None = None,
    settings: Settings | None = None,
    mode: WeatherMode | None = None,
    provider: Any | None = None,
) -> WeatherIngestOutcome:
    """Fetch the current observation for one airport and add it to the evidence ledger.

    Returns a record of what happened and never raises. Flushes but does not commit: the caller's
    transaction owns the boundary, so the observation becomes durable with the assessment it
    informed rather than separately from it.

    In fixture mode no provider is consulted and nothing is written, so `WEATHER_MODE=fixture` is
    unchanged down to the absence of a journal entry. Passing `provider=` is an explicit request
    and always consults, which is what makes the causality of a given observation testable.
    """
    resolved_settings = settings or get_settings()
    resolved_mode = mode if mode is not None else resolved_settings.weather_mode

    if provider is None and resolved_mode is not WeatherMode.live:
        return WeatherIngestOutcome(
            mode=resolved_mode.value, airport_icao=airport_icao, consulted=False
        )

    if not await _airport_exists(session, airport_icao):
        return WeatherIngestOutcome(
            mode=resolved_mode.value,
            airport_icao=airport_icao,
            consulted=False,
            reason=(
                f"{airport_icao} is not in the airport reference data, so an observation for it "
                "cannot be recorded against a known station"
            ),
        )

    active = provider
    if active is None:
        # Imported inside the function, like the reasoning-agent and flight-status imports, so a
        # deterministic run does not load a provider package it will never call.
        from app.providers.weather import get_weather_provider

        active = get_weather_provider(resolved_mode)

    from app.providers.base import ProviderError

    try:
        reading = await active.get_observation(airport_icao)
    except ProviderError as exc:
        # The expected shape of a live failure. Recorded, and the ledger keeps whatever it had.
        log.info(
            "live_weather_unavailable",
            airport_icao=airport_icao,
            kind=exc.kind.value,
            reason=str(exc.message)[:200],
        )
        return WeatherIngestOutcome(
            mode=resolved_mode.value,
            airport_icao=airport_icao,
            consulted=True,
            reason=f"{exc.kind.value}: {exc.message}",
        )
    except Exception as exc:
        # Nothing about a weather lookup justifies failing an incident. `_assess_delay_risk` has
        # no recovery handler around it, so an unexpected fault here would abort the assessment
        # of a real disruption over a missing enrichment.
        log.error(
            "live_weather_ingest_failed",
            airport_icao=airport_icao,
            error=type(exc).__name__,
            detail=str(exc)[:200],
        )
        return WeatherIngestOutcome(
            mode=resolved_mode.value,
            airport_icao=airport_icao,
            consulted=True,
            reason=f"{type(exc).__name__}: {exc}",
        )

    observed_at = reading.observed_at
    source_ref = reading.provenance.source_ref
    within_clock = None if as_of is None else observed_at <= as_of

    already = await _already_recorded(
        session,
        source_ref=source_ref,
        airport_icao=reading.airport_icao,
        observed_at=observed_at,
    )
    if not already:
        session.add(_row_from_reading(reading))
        await session.flush()

    outcome = WeatherIngestOutcome(
        mode=resolved_mode.value,
        airport_icao=reading.airport_icao,
        consulted=True,
        persisted=not already,
        already_recorded=already,
        observed_at=observed_at,
        source_ref=source_ref,
        provenance_kind=str(getattr(reading.provenance.kind, "value", reading.provenance.kind)),
        provider=reading.provenance.provider,
        is_stale=bool(reading.provenance.is_stale),
        within_incident_clock=within_clock,
    )
    log.info(
        "live_weather_ingested",
        airport_icao=outcome.airport_icao,
        observed_at=outcome.observed_at.isoformat() if outcome.observed_at else None,
        persisted=outcome.persisted,
        already_recorded=outcome.already_recorded,
        provenance_kind=outcome.provenance_kind,
        is_stale=outcome.is_stale,
        within_incident_clock=outcome.within_incident_clock,
    )
    return outcome


__all__ = [
    "DETAIL_KEY",
    "EVENT_LIVE_NOT_SCORED",
    "EVENT_LIVE_UNAVAILABLE",
    "WeatherIngestOutcome",
    "ingest_live_weather",
]
