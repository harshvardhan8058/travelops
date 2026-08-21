"""The unit boundary. Convert once, here, so no downstream service has to guess.

**This module is the single most dangerous file in the provider layer**, because every bug
in it produces a plausible number rather than an error. The Aviation Weather Center JSON
reports `visib` in **statute miles** while METAR reports metres: VOBL's `8000` metre
visibility arrives as `4.97`. Storing that as metres would turn an 8 km day into an
800 metre day and drive a severe risk index on a clear evening. Nothing downstream could
detect it.

The same trap in the other direction is the documented one: a 45 km/h wind read as 45 kt.
AWC `wspd` is already knots, so the conversion here is the identity — but it is named and
tested so that a future source in km/h cannot be wired in silently.

Canonical units stored everywhere: **knots, metres, feet.**

Owner: Stream C.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

#: 1 statute mile, exactly.
METRES_PER_STATUTE_MILE = 1609.344

#: 1 knot in km/h. Present so a km/h source can never be added without converting.
KMH_PER_KNOT = 1.852

#: METAR reports visibility in whole hundreds of metres below 10 km, so rounding to the
#: nearest 100 recovers the value the station actually published rather than leaving
#: 7998.44 in the database. Asserted against `rawOb` in the contract tests.
VISIBILITY_ROUNDING_M = 100

#: Cloud covers that constitute a ceiling. SCT and FEW do not: a scattered layer at 1200 ft
#: is not a ceiling, and treating it as one would flag half of monsoon India as severe.
CEILING_COVERS = frozenset({"BKN", "OVC", "OVX", "VV"})

#: Present-weather codes mapped to the vocabulary the risk rules read. Obscurations (BR
#: mist, HZ haze, FG fog) are deliberately NOT precipitation — they reduce visibility, which
#: is already scored from `visibility_m`, and counting them twice would double-penalise.
_PRECIPITATION_CODES: tuple[tuple[str, str], ...] = (
    ("TS", "thunderstorm"),
    ("GR", "hail"),
    ("GS", "hail"),
    ("SN", "snow"),
    ("SG", "snow"),
    ("PL", "ice_pellets"),
    ("FZRA", "freezing_rain"),
    ("SHRA", "showers"),
    ("SH", "showers"),
    ("RA", "rain"),
    ("DZ", "drizzle"),
    ("UP", "unknown_precipitation"),
)


def knots_from_knots(value: float | int | None) -> int | None:
    """Identity, named on purpose.

    AWC `wspd` is knots. This exists so that adding a source reporting km/h forces the
    author to look for the conversion instead of assuming the field means knots.
    """
    if value is None:
        return None
    return round(float(value))


def knots_from_kmh(value: float | int | None) -> int | None:
    """For any future source that reports km/h. 45 km/h is 24 kt, not 45 kt."""
    if value is None:
        return None
    return round(float(value) / KMH_PER_KNOT)


def visibility_m_from_statute_miles(value: Any) -> int | None:
    """AWC `visib` -> metres.

    Handles the `"10+"` and `"6+"` forms the API uses for "at or above", and the bare
    numeric forms. Returns None rather than 0 when unparseable, because 0 metres means
    fog and would be a fabricated observation.
    """
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip().rstrip("+").replace(",", "")
        if not cleaned:
            return None
        try:
            miles = float(cleaned)
        except ValueError:
            return None
    else:
        try:
            miles = float(value)
        except (TypeError, ValueError):
            return None

    if miles < 0:
        return None

    metres = miles * METRES_PER_STATUTE_MILE
    return int(round(metres / VISIBILITY_ROUNDING_M) * VISIBILITY_ROUNDING_M)


def ceiling_ft_from_clouds(clouds: list[dict[str, Any]] | None) -> int | None:
    """Lowest broken, overcast or vertical-visibility layer, in feet AGL.

    Returns None when there is no ceiling, which is a different statement from a ceiling of
    zero and must stay distinguishable.
    """
    if not clouds:
        return None

    bases: list[int] = []
    for layer in clouds:
        cover = (layer.get("cover") or "").strip().upper()
        base = layer.get("base")
        if cover in CEILING_COVERS and base is not None:
            try:
                bases.append(int(base))
            except (TypeError, ValueError):
                continue

    return min(bases) if bases else None


def precipitation_from_text(*sources: str | None) -> str | None:
    """Normalise present weather to one token, or None.

    Checked longest-code-first so `SHRA` is showers rather than rain and `TSRA` is a
    thunderstorm rather than rain.
    """
    haystack = " ".join(source.upper() for source in sources if source)
    if not haystack:
        return None

    for code, label in _PRECIPITATION_CODES:
        if code in haystack:
            return label
    return None


def wind_direction_from_awc(value: Any) -> int | None:
    """AWC `wdir` is degrees true, or the string `VRB` for variable.

    Variable wind has no direction, so it returns None. Defaulting it to 0 would compute a
    crosswind against a northerly wind that was never reported.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().upper() in {"VRB", "VAR", ""}:
            return None
        try:
            value = float(value)
        except ValueError:
            return None
    try:
        return round(float(value)) % 360
    except (TypeError, ValueError):
        return None


def utc_from_epoch(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def utc_from_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def observation_age_minutes(*, observed_at: datetime, now: datetime) -> int:
    """How stale the reading is, in whole minutes.

    Exposed as a function because `ProvenanceStamp` is a frozen contract owned upstream and
    carries `observed_at`, `retrieved_at` and `is_stale` but not the age itself. Callers that
    want to render "observed 12 minutes ago" compute it from the stamp with this.
    """
    delta = now - observed_at
    return max(0, int(delta.total_seconds() // 60))
