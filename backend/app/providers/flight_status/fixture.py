"""Fixture flight-status provider: archived AviationStack payloads, replayed offline.

This is what makes an unavailable vendor API — or an exhausted free-tier quota — unable to
block a checkpoint. It reads `fixtures/flight_status/aviationstack_snapshot.json` and
normalises it through **the same functions the live provider uses**. If the two normalised
independently, a contract test passing in both modes would prove nothing.

**Every status it returns is stamped `kind=fixture`, never `real`.** The archived bytes are
shaped like a real capture, but a replay is not an observation of a live flight, and the
provenance ledger and every UI badge derive from this field.

Unlike the live provider, the fixture supports `apply_simulated_transition`: it is the
simulator the recovery workflow drives, so a flight can be moved to `cancelled` or `delayed`
deterministically without touching a real airline.

Resolution mirrors `app/providers/weather/fixture.py` exactly: repo root locally, `/fixtures`
inside the container, because `./fixtures` is the volume mount.

Owner: Stream C.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.models.enums import ProvenanceKind
from app.providers.base import (
    ProvenanceStamp,
    ProviderError,
    ProviderErrorKind,
    ProviderHealth,
)
from app.providers.flight_status.normalise import normalise_status_row

#: backend/app/providers/flight_status/fixture.py -> parents[4] is the repo root locally and
#: `/` inside the container, where ./fixtures is mounted at /fixtures.
FIXTURE_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "flight_status"
SNAPSHOT_FILE = FIXTURE_DIR / "aviationstack_snapshot.json"

PROVIDER_NAME = "aviationstack-fixture"

#: Kept in step with the live provider and the assurance gate's five-minute limit.
DEFAULT_MAX_STATUS_AGE_MINUTES = 5


@lru_cache(maxsize=1)
def load_snapshot(path: Path = SNAPSHOT_FILE) -> dict[str, Any]:
    if not path.is_file():
        raise ProviderError(
            ProviderErrorKind.unavailable,
            f"flight-status fixture snapshot not found at {path}",
            provider=PROVIDER_NAME,
        )
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureFlightStatusProvider:
    """Implements `app.providers.base.FlightStatusProvider` from the archived snapshot."""

    name = PROVIDER_NAME
    mode = "fixture"

    def __init__(
        self,
        *,
        snapshot_path: Path = SNAPSHOT_FILE,
        now: datetime | None = None,
        max_status_age_minutes: int = DEFAULT_MAX_STATUS_AGE_MINUTES,
    ) -> None:
        self._path = snapshot_path
        #: Frozen clock. A fixture provider whose output moves with the wall clock is not a
        #: fixture, and `is_stale` would flip mid-demo.
        self._now = now
        self._max_age = max_status_age_minutes
        #: In-memory simulated transitions, keyed by flight_id. Never written to the snapshot
        #: on disk — the archive stays pristine and the simulation lives only for this process.
        self._transitions: dict[int, dict[str, Any]] = {}

    def _clock(self) -> datetime:
        if self._now is not None:
            return self._now
        snapshot = load_snapshot(self._path)
        retrieved = snapshot.get("source", {}).get("retrieved_at")
        if retrieved:
            parsed = datetime.fromisoformat(str(retrieved).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return datetime.now(tz=UTC)

    def _rows(self) -> list[dict[str, Any]]:
        snapshot = load_snapshot(self._path)
        data = snapshot.get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def _row_for(self, flight_id: int) -> dict[str, Any] | None:
        for row in self._rows():
            fixture_meta = row.get("_fixture") if isinstance(row.get("_fixture"), dict) else {}
            if fixture_meta.get("flight_id") == flight_id:
                return row
        return None

    def _stamp(self, normalised: dict[str, Any], *, now: datetime) -> ProvenanceStamp:
        observed = normalised.get("revised_departure") or normalised.get("scheduled_departure")
        age_minutes = 0
        if observed is not None:
            age_minutes = max(0, int((now - observed).total_seconds() // 60))
        return ProvenanceStamp(
            kind=ProvenanceKind.fixture,
            provider=self.name,
            source_ref=f"fixture:flight_status:{normalised['flight_id']}",
            observed_at=observed,
            retrieved_at=now,
            is_stale=age_minutes > self._max_age,
        )

    async def health(self) -> ProviderHealth:
        """Never raises."""
        checked_at = self._clock()
        try:
            rows = self._rows()
            healthy = bool(rows)
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=healthy,
                detail=(
                    f"{len(rows)} flights in archived snapshot"
                    if healthy
                    else "snapshot contains no flight rows"
                ),
                checked_at=checked_at,
            )
        except ProviderError as exc:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=False,
                detail=f"{exc.kind.value}: {exc.message}",
                checked_at=checked_at,
            )
        except Exception as exc:
            return ProviderHealth(
                provider=self.name,
                mode=self.mode,
                healthy=False,
                detail=f"unexpected: {exc!r}",
                checked_at=checked_at,
            )

    async def get_status(self, flight_id: int) -> dict[str, Any]:
        now = self._clock()

        # A simulated transition, if one was applied, overrides the archived row so the
        # recovery workflow sees the state it just set.
        if flight_id in self._transitions:
            return self._transitions[flight_id]

        row = self._row_for(flight_id)
        if row is None:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"archived snapshot has no flight status for flight_id={flight_id}",
                provider=self.name,
            )

        normalised = normalise_status_row(row, flight_id=flight_id, provider=self.name)
        normalised["provenance"] = self._stamp(normalised, now=now).model_dump(mode="json")
        return normalised

    async def apply_simulated_transition(self, flight_id: int, status: str) -> dict[str, Any]:
        """Move a flight to a new status for the rest of this process.

        Deterministic and offline. A `cancelled` transition zeroes the delay and marks the
        flight cancelled; a `delayed:<minutes>` form shifts the delay. The result is stamped
        `kind=simulated`, never `real` and never `fixture`, so the provenance ledger records
        that a human/simulator moved it rather than that it was observed or archived.
        """
        now = self._clock()
        row = self._row_for(flight_id)
        if row is None:
            raise ProviderError(
                ProviderErrorKind.unavailable,
                f"cannot transition unknown flight_id={flight_id}",
                provider=self.name,
            )

        normalised = normalise_status_row(row, flight_id=flight_id, provider=self.name)

        requested = status.strip().lower()
        if requested == "cancelled":
            normalised["status"] = "cancelled"
            normalised["status_is_known"] = True
            normalised["cancelled"] = True
            normalised["delay_minutes"] = 0
            normalised["arrival_delay_minutes"] = 0
        elif requested.startswith("delayed:"):
            try:
                minutes = max(0, int(requested.split(":", 1)[1]))
            except (ValueError, IndexError) as exc:
                raise ProviderError(
                    ProviderErrorKind.invalid_response,
                    f"malformed delayed transition {status!r}; expected 'delayed:<minutes>'",
                    provider=self.name,
                ) from exc
            normalised["status"] = "active"
            normalised["status_is_known"] = True
            normalised["delay_minutes"] = minutes
            scheduled = normalised.get("scheduled_departure")
            if isinstance(scheduled, datetime):
                normalised["revised_departure"] = scheduled + timedelta(minutes=minutes)
        else:
            raise ProviderError(
                ProviderErrorKind.invalid_response,
                f"unsupported simulated transition {status!r}; "
                "expected 'cancelled' or 'delayed:<minutes>'",
                provider=self.name,
            )

        stamp = ProvenanceStamp(
            kind=ProvenanceKind.simulated,
            provider=self.name,
            source_ref=f"simulated:flight_status:{flight_id}:{requested}",
            observed_at=normalised.get("revised_departure")
            or normalised.get("scheduled_departure"),
            retrieved_at=now,
            is_stale=False,
        )
        normalised["provenance"] = stamp.model_dump(mode="json")
        self._transitions[flight_id] = normalised
        return normalised
