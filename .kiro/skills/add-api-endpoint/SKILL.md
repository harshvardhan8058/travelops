---
name: add-api-endpoint
description: Add a real API endpoint or replace a fixture-backed one in app/api/. Use when implementing routes, changing a response shape, handling errors, or moving an endpoint out of fixtures_router.py.
---

# Add or replace an API endpoint

Wave 0 shipped fixture-backed endpoints so the frontend was never blocked. Your job is to
replace them one at a time **without changing the response shape**.

Owner: Stream A. Contract: `docs/26-implementation-contracts.md`.

## The shape is contractual

`fixtures/api/*.json` and the frontend both depend on it. Streams E and F are building against
those files right now. Changing a field name breaks their work silently.

If a shape genuinely must change: say so, update the fixture and the TypeScript type in the
same PR, and tell the frontend streams. Never change it quietly.

## Replacing a fixture endpoint

1. Implement it in your own router module under `app/api/`.
2. Register that router in `app/api/__init__.py`.
3. **Delete the endpoint from `fixtures_router.py`** so there is one implementation, not two.
4. Confirm `tests/contract/test_api_shapes.py` still passes unchanged. If you had to edit that
   test to make it pass, you changed the contract.

## Every response carrying external or seeded data includes provenance

```json
{
  "provenance": {
    "kind": "real | simulated | synthetic | fixture | unavailable",
    "provider": "awc | ourairports | local-simulator | generator",
    "source_ref": "...",
    "observed_at": "...",
    "is_stale": false
  }
}
```

The UI renders this. It must never infer provenance from a provider name.

## Errors use the typed envelope

Raise the exception; the handler in `app/main.py` formats it:

```python
from app.errors import EntityNotFound, InvalidStateTransition, AssuranceBlocked

raise EntityNotFound("incident not found", details={"reference": ref})
```

Produces:

```json
{"error": {"code": "ENTITY_NOT_FOUND", "message": "...", "correlation_id": "...", "details": {}}}
```

Never return a bare string, never invent a new error shape, and add new codes to `ErrorCode`
rather than inlining a literal.

## Mutations

- Accept `Idempotency-Key`. A replay returns the original result rather than acting twice.
- An illegal state change raises `InvalidStateTransition` → HTTP 409, with the allowed
  transitions in `details` so the response is actionable.
- Destructive demo helpers (`reset`, `demo-reset`) raise `DemoActionForbidden` outside
  development or demo environments.

## Never

- Return an entitlement figure the policy engine did not produce.
- Execute an action without an `assurance_evaluation` authorising it.
- Log a secret, a raw personal address, or full prompt context.
- Add a field to an event type in `app/events/types.py` without raising it — other streams
  consume those.

## After changes

```bash
cd backend && uv run pytest && uv run ruff check .
make openapi   # regenerate docs/openapi.json when routes change
```
