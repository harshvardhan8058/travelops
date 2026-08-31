"""Flight-status provider selection.

Selection is by explicit mode, never by a hardcoded import at the call site — the same
principle as `app.providers.weather`. A service asks for `get_flight_status_provider()` and
does not know or care which implementation it received, which is what lets a demo run on the
fixture snapshot when the vendor API is unavailable or its free-tier quota is spent.

Both implementations satisfy `app.providers.base.FlightStatusProvider` and share one
normalisation path, so a live status and a replayed one are shaped identically.

## Mode resolution and why it fails safe

Weather has a `WEATHER_MODE` setting in `app.config`, owned by Stream A. There is no
`FLIGHT_STATUS_MODE` setting yet. Rather than reach into another stream's config module, this
selector:

* takes an explicit `mode` argument when the caller knows which it wants;
* otherwise reads a `flight_status_mode` attribute from `Settings` **if Stream A has added
  one** (forward-compatible, no hard dependency);
* otherwise defaults to `"fixture"`.

Defaulting to fixture is the fail-safe choice: live flight status requires a configured API
key, and silently attempting live calls with no key would turn a missing setting into repeated
runtime failures inside incidents. Fixture mode always works offline. Turning live on is an
explicit act — passing `mode="live"` or setting the future `FLIGHT_STATUS_MODE=live` — never a
default the system falls into.

Owner: Stream C.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import get_settings
from app.providers.flight_status.fixture import FixtureFlightStatusProvider
from app.providers.flight_status.live import LiveFlightStatusProvider

if TYPE_CHECKING:
    from app.providers.base import FlightStatusProvider

__all__ = [
    "FixtureFlightStatusProvider",
    "LiveFlightStatusProvider",
    "get_flight_status_provider",
]

#: The valid mode strings. Kept as plain strings, not a config enum, precisely because the
#: enum belongs in `app.config` (Stream A) and this package must not fork it.
_VALID_MODES = frozenset({"live", "fixture"})

#: Safe default when nothing selects a mode. Fixture works offline; live needs a key.
DEFAULT_MODE = "fixture"


def _resolved_mode(mode: str | None) -> str:
    if mode is not None:
        return str(mode).lower()
    settings = get_settings()
    # Forward-compatible: use the setting if Stream A adds `FLIGHT_STATUS_MODE`, else default.
    configured = getattr(settings, "flight_status_mode", None)
    if configured is None:
        return DEFAULT_MODE
    return str(getattr(configured, "value", configured)).lower()


def get_flight_status_provider(mode: str | None = None) -> FlightStatusProvider:
    """Return the configured implementation.

    An unknown mode raises rather than defaulting. Guessing which flight-status source is in
    use is precisely the ambiguity the provenance ledger exists to remove.

    In live mode the AviationStack key is read from `Settings.aviationstack_api_key` if present
    (again forward-compatible with a future Stream A setting), else the empty string — in which
    case the provider's own `health()` reports it down rather than pretending to be live.
    """
    resolved = _resolved_mode(mode)

    if resolved == "fixture":
        return FixtureFlightStatusProvider()
    if resolved == "live":
        settings = get_settings()
        api_key = str(getattr(settings, "aviationstack_api_key", "") or "")
        return LiveFlightStatusProvider(api_key=api_key)

    raise ValueError(f"unknown flight status mode: {resolved!r}")
