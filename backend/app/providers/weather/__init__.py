"""Weather provider selection.

Selection is by config, never by a hardcoded import at the call site. A service asks for
`get_weather_provider()` and does not know or care which implementation it received — that is
what lets `WEATHER_MODE=fixture` carry a demo on a dead venue network.

Both implementations satisfy `app.providers.base.WeatherProvider` and share one normalisation
path, so units are guaranteed identical between them: knots, metres, feet.

Owner: Stream C.
"""

from __future__ import annotations

from app.config import WeatherMode, get_settings
from app.providers.base import WeatherProvider
from app.providers.weather.fixture import FixtureWeatherProvider
from app.providers.weather.live import LiveWeatherProvider

__all__ = [
    "FixtureWeatherProvider",
    "LiveWeatherProvider",
    "get_weather_provider",
]


def get_weather_provider(mode: WeatherMode | None = None) -> WeatherProvider:
    """Return the configured implementation.

    An unknown mode raises rather than defaulting. Guessing which weather source is in use
    is precisely the ambiguity the provenance ledger exists to remove.
    """
    resolved = mode if mode is not None else get_settings().weather_mode

    if resolved is WeatherMode.live:
        return LiveWeatherProvider()
    if resolved is WeatherMode.fixture:
        return FixtureWeatherProvider()

    raise ValueError(f"unknown weather mode: {resolved!r}")
