---
name: implement-service
description: Implement one of the ten deterministic domain services in app/services/. Use when building delay risk, connection, crew impact, hotel, transport, communication, compensation, flight recovery, resource or analytics logic.
---

# Implement a deterministic service

Ten services do the actual work. They are **tools, not agents** — that distinction is the
architecture, and it is what the mentor review corrected.

Owner: Stream D. Design: `docs/03-agent-design.md`, `docs/06-ai-vs-deterministic.md`.

## The absolute rule

**Nothing under `app/services/` may import `groq`, `openai`, `anthropic`, `litellm`, `ollama`
or `app.llm`.** `tests/unit/test_no_llm_in_services.py` enforces it with an AST check. If that
test fails, your change is wrong.

A service also does not decide whether it is allowed to run. The orchestrator asks the
Decision Assurance Gate first, then dispatches to you.

## Shape

```python
from app.services.base import ServiceResult

class ConnectionService:
    name = "connection"

    async def execute(self, **kwargs) -> ServiceResult:
        return ServiceResult(
            status=ActionStatus.success,
            reason="6 itineraries no longer feasible",   # human-facing, specific
            payload={"at_risk_booking_ids": [...]},       # typed data
            evidence_refs=["flight:1", "booking:...."],   # what this rests on
            provenance_kind="synthetic",
        )
```

`reason` is read by an operator under time pressure. "6 itineraries no longer feasible" is
useful; "processed successfully" is not.

## Non-negotiables

- **Deterministic.** Identical input yields identical output. No `random` without a seeded
  generator, no unseeded `datetime.now()` inside logic — take `now` as an argument.
- **No magic numbers.** Thresholds, budgets and caps come from config or
  `business_constraint` rows. Never hardcode ₹6000.
- **Evidence references always.** An action a controller cannot trace is an action they cannot
  defend.
- **Units at the boundary.** Knots, metres, feet, integer INR. A 45 km/h reading mistaken for
  45 kt silently invalidates every downstream score.
- **Money is integer.** Never float.

## Two services with special constraints

**Compensation** assembles facts and calls `app.policy.engine`. It must never compute an
entitlement itself and never infer a legal outcome from `trigger_type`. It returns whatever
the engine returns, including `needs_human`.

**Crew Impact** is coordination and display only. It must never validate duty-time legality or
generate a legal replacement roster. It reports which pairings are at risk and the mechanism
for each: `operating`, `onward_duty`, `second_pairing` or `positioning`.

## Delay Risk: index, not probability

Return a risk **index** (0–100) and **level** (`low`/`elevated`/`high`/`severe`) plus named
contributing factors and `RULE_VERSION`. Nothing here is calibrated against observed outcomes,
so "87% chance of delay" would be an unearned claim.

`crosswind_component_kt` and `headwind_component_kt` already exist and are tested. Use them.

## Tests

One test file per service in `tests/unit/services/`. Cover the normal case, each threshold
boundary, the missing-input case, and reproducibility. Follow the conventions in
`tests/unit/test_crosswind.py`.

Then run the skill `verify-before-commit`.
