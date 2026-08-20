# 26. MVP Implementation Contracts

This closes the minimum API, state, security, observability and deployment ambiguity required to start
coding. FastAPI generates the formal OpenAPI document; this file fixes behaviour and invariants.

## API surface

Base path: `/api/v1`. JSON only. UTC timestamps in RFC 3339. Mutation requests accept
`Idempotency-Key`; repeated keys return the original result.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health/live` | Process alive; no dependency checks |
| `GET` | `/health/ready` | Database, Redis and configured providers; reports degraded modes |
| `GET` | `/system/mode` | LLM/weather/policy/notification modes and provenance; secrets excluded |
| `GET` | `/flights` | Flight board with risk and source provenance |
| `GET` | `/incident-groups` | Cascade summary |
| `GET` | `/incident-groups/{id}` | Flights, connections and crew-pairing graph |
| `POST` | `/scenarios/{key}/inject` | Idempotently inject allowed demo fixture |
| `POST` | `/scenarios/{key}/reset` | Reset only demo-owned records; disabled outside demo mode |
| `GET` | `/incidents/{id}` | Current state, plan, tasks and rollups |
| `GET` | `/incidents/{id}/timeline` | Ordered immutable event/action records |
| `GET` | `/incidents/{id}/assurance` | Gate evaluations and failed checks |
| `POST` | `/assurance/{id}/decision` | Operator approve/reject with reason |
| `GET` | `/incidents/{id}/policy` | Applicability and cited entitlement evaluations |
| `POST` | `/incidents/{id}/run` | Continue workflow from current legal state |
| `GET` | `/reports/{incident_id}` | Structured metrics + generated narrative if available |

List endpoints use cursor pagination. Every error follows:

```json
{
  "error": {
    "code": "ASSURANCE_BLOCKED",
    "message": "Action requires operator approval",
    "correlation_id": "...",
    "details": {"assurance_id": 42, "blocking_checks": ["action_risk"]}
  }
}
```

## Provenance contract

Any external/seeded datum includes:

```json
{
  "provenance": {
    "kind": "real|simulated|synthetic|fixture|unavailable",
    "provider": "awc|ourairports|aikosh|local-simulator|generator",
    "source_ref": "...",
    "observed_at": "...",
    "retrieved_at": "...",
    "is_stale": false
  }
}
```

A UI component may not infer provenance from provider names; it renders this contract.

## Incident state machine

```text
detected → assessing → planning → assuring
                              ├─→ awaiting_approval
                              ├─→ executing → resolved
                              └─→ blocked
Any active state → failed
```

These lower-case values are canonical in PostgreSQL, API schemas, events and tests. Display labels may
be title-cased, but no layer defines an alternate state vocabulary.

Legal transitions are explicit. Invalid transitions return `409 INVALID_STATE_TRANSITION`. A retry may
resume from the last durable state; it may not recreate completed side effects.

## Task/action lifecycle

```text
Task: PENDING → PROPOSED → ASSURED → EXECUTING → SUCCEEDED | FAILED | SKIPPED
                         └→ NEEDS_HUMAN → ASSURED | REJECTED
```

Invariants:

1. No action row without an assurance evaluation.
2. No side effect when assurance decision is `needs_human` or when the operator rejects it.
3. An operator response is an immutable `human_decision` keyed to one assurance evaluation, with actor,
   timestamp and reason. It never mutates the original gate record.
4. An action following `needs_human` must reference an `approved` human decision for the same evaluation;
   a rejected decision cannot be reused.
5. Idempotency key scopes to action type + target entity + incident + intended version.
6. Compensation action requires an approved policy pack in verified mode; demo mode cannot produce an
   authoritative external side effect.

## Typed events

| Event | Minimum payload |
| --- | --- |
| `WEATHER_OBSERVED` | airport, values/units, provenance |
| `HIGH_RISK_DELAY` | flight, risk index/level, factors, rule version, evidence |
| `INCIDENT_OPENED` | incident/group IDs, trigger, affected entity IDs |
| `PLAN_PROPOSED` | plan ID, generator/prompt version, task IDs, evidence |
| `ASSURANCE_EVALUATED` | evaluation ID, task ID, decision, checks, config hash |
| `ACTION_COMPLETED` | action ID, status, actor, cost, provenance |
| `HUMAN_DECISION_RECORDED` | evaluation, operator pseudonymous ID, decision, reason |
| `INCIDENT_RESOLVED` | outcome metrics derived from records |

Events carry `event_id`, `schema_version`, `correlation_id`, `causation_id`, `incident_id`,
`occurred_at` and `producer`. Consumers are idempotent by `event_id`.

## Provider protocols

Each provider exposes capability, health and provenance plus domain methods. Startup validates selected
modes but must not require live providers in fixture/offline mode.

- Weather: `get_observation`, `get_forecast`
- Schedules: `list_flights`
- Flight status: `get_status`, `apply_simulated_transition`
- Notifications: `prepare`, `send_allowlisted`, `record_simulated_bulk`
- LLM: `generate_structured`; live/fixture/off implementations

Provider errors are typed as unavailable, timeout, rate-limited, invalid-response or forbidden. The
orchestrator maps them to deterministic fallback or `needs_human`—never silent success.

## Demo authentication and authorisation

This is a local, single-operator prototype, not a production IAM implementation.

- The FastAPI process listens on the container interface (`0.0.0.0`) so Docker networking works.
- Docker publishes frontend/API host ports to `127.0.0.1` by default. PostgreSQL and Redis are not
  host-published; they remain on the internal Compose network.
- Any LAN/public host publication requires an explicit authenticated demo-host configuration and team
  approval.
- Mutation endpoints require a generated demo session token stored outside Git.
- Roles: `viewer` (read), `operator` (inject/approve/run), `admin` (reset/config validation).
- The demo UI may use one local operator account, but approval records store a pseudonymous actor ID.
- Production roadmap: enterprise SSO/OIDC, MFA, airline RBAC, separation of duties and approval limits.

## Threat boundaries

| Threat | MVP control |
| --- | --- |
| Prompt injection through external text | Planner receives typed fields; legal retrieval is display context, never executable instruction |
| Hallucinated action/entity | Action enum + entity validation + gate |
| Secret leakage | Environment secrets, redacted structured logs, no raw provider headers |
| PII leakage | Synthetic passengers only; controlled demo recipient allowlist outside Git |
| Duplicate booking/notification | Idempotency keys + unique constraints |
| Stale data | Provenance timestamp + configured freshness check |
| Unsafe high-risk automation | Mandatory human approval |
| Policy tampering | Pack/config hash, approval status, immutable evaluation record |
| Event replay/loop | Event IDs, consumer idempotency, workflow step/time caps |
| Demo reset damage | Reset restricted to records tagged with demo dataset ID |

## Observability

Structured log fields: timestamp, level, service, event, correlation ID, incident/group ID, actor,
provider mode, duration, outcome and error code. Never log secrets, raw personal addresses or full prompt
context.

Minimum metrics:

- workflow duration and state-transition count
- provider latency/error/fallback count
- assurance decisions by action/check
- human approve/reject count
- task success/failure/idempotent-replay count
- notification real/simulated count
- LLM calls/tokens only when provider supplies them

Prototype impact metrics must be derived from these records. No invented savings percentage.

## Deployment topology

```text
Browser → frontend → FastAPI
                     ├→ PostgreSQL
                     ├→ Redis Streams
                     └→ optional HTTPS providers
```

Local Docker Compose is authoritative for the hackathon. All containers have health checks, pinned major
versions and persistent named volumes. The demo reset targets only tagged fixture data.

## Configuration validation

Startup prints a non-secret mode summary and validates:

- unknown mode/enum: refuse startup
- missing assurance config or hash: refuse workflow execution
- `POLICY_MODE=verified` with unapproved/mismatched pack: refuse verified mode
- live LLM with no key: degrade to fixture only when explicitly allowed
- Gmail/Mailtrap mode with no credentials: degrade to console only when explicitly allowed
- empty recipient allowlist: no real external email

Failing safely is a feature; implicit fallback that changes a claim is not.

## Definition of implementation-ready

Backend and frontend work can start when this file, [`09-requirements.md`](09-requirements.md),
[`11-data-model.md`](11-data-model.md), [`16-folder-structure.md`](16-folder-structure.md), and
[`21-design-system.md`](21-design-system.md) agree. Any generated OpenAPI difference must update the
relevant canonical doc in the same PR.
