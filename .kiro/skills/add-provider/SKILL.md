---
name: add-provider
description: Implement an external provider in app/providers/ with both a live and a fixture implementation. Use when integrating weather, flight status, notifications or an LLM, or when adding any boundary to an outside system.
---

# Add a provider

A provider is the only place the system talks to the outside world. **Every provider needs at
least two implementations behind the same Protocol: a live one and a fixture or simulated one.**

That is what makes an unavailable vendor API unable to block a checkpoint demo — and it is why
the demo survives a dead venue network.

Owner: Stream C. Protocols already defined in `app/providers/base.py` — implement against
them, do not change them.

## Structure

```text
app/providers/weather/
├── __init__.py      # picks the implementation from settings.weather_mode
├── live.py          # real HTTP call
└── fixture.py       # reads the committed snapshot
```

Selection is by config, never by a hardcoded import at the call site.

## Normalise units at the boundary

Store knots, metres, feet. Convert once, here, so no downstream service has to guess.

A 45 km/h wind reading mistaken for 45 kt is the classic version of this bug: it produces a
plausible risk score that is quietly wrong, and nothing downstream can detect it.

## Return provenance with every value

```python
ProvenanceStamp(
    kind=ProvenanceKind.real,
    provider="awc",
    source_ref="metar:VOBL:2026-08-20T15:30:00Z",
    observed_at=observed,
    retrieved_at=now,
    is_stale=False,
)
```

The fixture implementation returns `kind=fixture`. Never label fixture output as `real` — the
provenance ledger and every UI badge derive from this field.

## Errors are typed, and never silent success

```python
from app.providers.base import ProviderError, ProviderErrorKind

raise ProviderError(ProviderErrorKind.timeout, "AWC did not respond in 5s", provider="awc")
```

Kinds: `unavailable`, `timeout`, `rate_limited`, `invalid_response`, `forbidden`. The
orchestrator maps these to a deterministic fallback or `needs_human`. Returning empty or
default data on failure is the one outcome that must never happen — it looks like success.

## The LLM provider is special

Three implementations: `live`, `fixture`, `off`.

**`off` must raise `ProviderError(unavailable)`** so the orchestrator takes the deterministic
fallback path. It must never fabricate a plan. `LLM_MODE=off` completing a recovery is a
demonstrated property, not a hope.

## The notification provider is special

Real sends go **only** to `DEMO_RECIPIENT_ALLOWLIST`. Every other recipient gets a
`notification` row with `delivery_mode=simulated`.

Three real emails and 601 simulated is honest. Implying all 604 were delivered is not.

## Health

Implement `health()` returning `ProviderHealth`. It must never raise — a probe that crashes is
worse than one that reports `down`.

## Tests

Contract tests in `tests/contract/` that run against **both** implementations, asserting the
same shape from each. Test the failure paths explicitly: timeout, rate limit, malformed
response.

Then run the skill `verify-before-commit`.
