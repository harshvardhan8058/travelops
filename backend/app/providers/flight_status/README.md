# Flight-status provider (Phase 5 live-data foundation)

Live and fixture implementations of `app.providers.base.FlightStatusProvider`, mirroring the
weather provider. This document is the configuration reference for the flight-status source: it
lives beside the code (Stream C) rather than in `docs/` or `.env.example`, which other streams
own, so it stays accurate as the adapter evolves.

## Provider chosen: AviationStack

| Criterion | AviationStack | Why it won |
| --- | --- | --- |
| API availability | REST `/v1/flights`, global coverage incl. Indian carriers | Returns scheduled **flight status** with delays, not just positions |
| Documentation | Public, stable field names | Response shape is easy to map and pin in tests |
| Free / developer access | Free tier (~100 requests/month) | Enough for a demo and for wiring; no card required |
| Required credentials | A single API key as the `access_key` query param | No OAuth handshake to implement now |
| Response quality | Per-leg `scheduled` / `estimated` / `actual` times and a `delay` in minutes | Maps directly onto `SegmentFlight.delay_minutes` and revised times |
| Rate limits | Monthly request cap; failures returned as an `error` body | Handled as typed `rate_limited` so fixture fallback is clean |

**Rejected alternatives.** OpenSky Network is an ADS-B position feed (lat/lon/velocity state
vectors), not a scheduled-versus-actual delay source, so it cannot populate `delay_minutes`
without inference; it also moved to OAuth2 client-credentials in 2026. FlightAware AeroAPI has
richer data but requires a card on the free tier and is heavier to integrate safely now. Both
remain future options behind the same `FlightStatusProvider` protocol.

**Weather** (the other Phase 5 data type) already has a complete live+fixture adapter — the
US-government **Aviation Weather Center** (`app.providers.weather`), public domain, no key,
aviation-grade METAR/TAF. Nothing about it needed changing; it is already the best practical
source for its domain.

## Required environment variables

There is **no live flight-status call without an explicit opt-in.** The default mode is
`fixture`, which works fully offline.

| Variable | Purpose | Default | Notes |
| --- | --- | --- | --- |
| `FLIGHT_STATUS_MODE` | `live` or `fixture` | `fixture` (fail-safe) | Read from `Settings` **if** Stream A adds it; until then pass `mode=` explicitly. |
| `AVIATIONSTACK_API_KEY` | AviationStack `access_key` | `""` | Read from `Settings` if present. With no key, live `health()` reports **down** rather than pretending to be live. |

These names are forward-compatible: the selector reads them off `Settings` via `getattr` and
does not require Stream A to have added the fields yet. Wiring them into `app/config.py` and
`.env.example` (both outside Stream C's ownership) is the remaining integration step — see
below. Until then, callers choose the provider explicitly:

```python
from app.providers.flight_status import get_flight_status_provider

provider = get_flight_status_provider("fixture")  # offline, deterministic
provider = get_flight_status_provider("live")  # needs AVIATIONSTACK_API_KEY
```

## Adapter contract

Both providers satisfy `FlightStatusProvider` and return the **same normalised dict** from
`get_status(flight_id)`:

| Field | Meaning |
| --- | --- |
| `flight_id` | The domain flight id passed in |
| `flight_number` | Vendor flight number (IATA preferred) |
| `status`, `status_is_known` | Raw status, and whether it is in the known set |
| `cancelled` | True for a cancellation (never modelled as a delay) |
| `origin_icao`, `destination_icao` | ICAO endpoints (IATA is never substituted) |
| `scheduled_departure` / `_arrival` | Timezone-aware UTC |
| `revised_departure` / `_arrival` | Best-known actual, else estimated |
| `delay_minutes` | Departure delay, whole minutes, **never negative** |
| `arrival_delay_minutes` | Arrival delay, reported separately |
| `provenance` | `ProvenanceStamp`: `kind` (`real` live / `fixture` replay / `simulated` transition), `source_ref`, `observed_at`, `retrieved_at`, `is_stale` |

`app.services.flight_status_ingest.segment_from_status` maps that dict into a
`SegmentFlight` (the type `app.services.connection` already consumes) and carries provenance +
evidence refs. `ingest_flight_status(provider, flight_id=...)` wraps a fetch and turns any
`ProviderError` into a fail-safe result.

### Fail-safe guarantees

- Every failure — timeout, rate limit, missing flight, forbidden key, malformed body, a 200
  with an AviationStack `error` envelope — surfaces as a typed `ProviderError`, **never** as an
  on-time, zero-delay status.
- The ingest mapper converts a `ProviderError` (or a cancelled/incomplete status) into a result
  with `provenance_kind=unavailable` and no `SegmentFlight`, so a caller routes it to
  `needs_human`. It never fabricates schedule data.
- Staleness is flagged at five minutes, in step with
  `app.assurance.contract.FreshnessLimits.flight_status_minutes`, so the gate's `sources_fresh`
  check and the provider agree.
- Providers only supply evidence. They authorise nothing: the orchestrator still calls the
  Decision Assurance Gate before any action. `apply_simulated_transition` is available on the
  **fixture** provider only; the live provider refuses it (`forbidden`) so a real flight is
  never "moved" by simulation.

## Remaining integration work

1. **Stream A** adds `flight_status_mode: FlightStatusMode` and `aviationstack_api_key: str`
   to `app/config.py`, a `resolve_modes` branch that fails closed when `live` is requested
   without a key (or degrades only under an explicit allow flag, and records the degradation),
   and the two variables in `.env.example`.
2. **flight-number mapping**: the live provider needs `flight_id -> IATA flight number`. Wire
   this from the flight table (`flight.flight_number`) at the call site.
3. Optionally feed `ingest_flight_status` into the connection/recovery workflow so a live
   delay refreshes `SegmentFlight.delay_minutes` before the connection walk runs.
4. Not in scope for this foundation: hotels, email/notifications, payments, or any other
   external integration.
