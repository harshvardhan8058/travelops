# TravelOps AI

**An autonomous operating layer for airline disruption recovery.**

TravelOps AI detects disruption risk, coordinates a bounded recovery workflow, executes permitted
deterministic actions and records the evidence behind every decision. It is built by **Team SkyForge
AI** (Registration ID 201) for the Coforge TechCon 2026 Hackathon.

| Field | Value |
| --- | --- |
| Project / use case | TravelOps AI |
| Team | SkyForge AI |
| Industry | Travel Transport Hospitality (TTH) → Airlines Operations |
| Theme | Engineering the Autonomous Enterprise |
| Current repository status | Stage 2 deterministic slice runs end to end: inject → risk → plan → gate → approval → execute → resolved, with `LLM_MODE=off` |
| Submitted deck | Frozen; see [`docs/17-presentation-prompt.md`](docs/17-presentation-prompt.md) |
| Next build target | Stage 3 bounded reasoning: the three agents behind the same typed contracts |

## Architecture in one view

```text
Signals + fixtures
       │
       ▼
┌──────────────────── TravelOps Orchestrator ────────────────────┐
│ workflow state · sequencing · limits · idempotency · audit     │
└──────────────┬──────────────────────────────┬───────────────────┘
               │                              │
      3 reasoning agents              10 deterministic services
  Planner · Explainer · Reporter      risk · recovery · hotel · transport
               │                      communication · compensation · crew
               │                      connections · resources · analytics
               └───────────┬──────────────────┘
                           ▼
                Decision Assurance Gate
          evidence · freshness · entities · policy
                    conflicts · risk tier
                           ▼
              execute / flagged / human approval
```

The LLM plans and explains. It never authorises or directly executes an action. The same core recovery
must complete with `LLM_MODE=off`.

## Fixed demonstration scenario

```text
Bengaluru storm fixture
  → 8 traceable flights
  → ~600 synthetic passengers
  → 22 at-risk connections
  → 11 synthetic hotels
  → 9 traceable crew pairings
  → recovery actions + decision timeline
```

These are fixed-seed fixture targets, not production statistics. Weather can be live; flight status,
passengers, hotels, crew, transport and bulk notifications are simulated or synthetic and visibly
labelled.

## Run it

```bash
make doctor          # check the toolchain and required files first
make env             # create .env from .env.example
make up              # build and start api + postgres + redis + web
make migrate         # apply the schema
make seed            # load the fixed-seed dataset: 2083 rows, digest 70fbdf8947c638e5
make demo            # inject bengaluru_storm -> one incident, risk 80 (severe)
```

`make seed` and `make demo` are not optional. Without them the database is empty, there is no
incident, and every incident endpoint correctly returns `ENTITY_NOT_FOUND`.

Then open <http://127.0.0.1:8000/docs> for the API and <http://127.0.0.1:5173> for the console.

Ports bind to `127.0.0.1` only. PostgreSQL and Redis are not published to the host at all —
use `make db-shell`.

### The recovery, end to end

`make demo` opens the incident in `detected`. The workflow then advances on request, which is
what makes the gate visible: it stops and waits rather than running to completion on its own.

```bash
REF=INC-2026-0820-VOBL-01
API=http://127.0.0.1:8000/api/v1

# 1. advance until the gate needs a person
curl -sS -X POST $API/incidents/$REF/run                     # -> awaiting_approval

# 2. see why it stopped
curl -sS $API/incidents/$REF/assurance                       # notify_passengers: needs_human, high

# 3. approve the one evaluation waiting on a decision
ID=$(curl -sS $API/incidents/$REF/assurance \
     | python3 -c 'import json,sys; print(next(e["id"] for e in json.load(sys.stdin)["evaluations"] if e["decision"]=="needs_human"))')
curl -sS -X POST $API/assurance/$ID/decision \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approved","reason":"confirmed against the ops board"}'

# 4. finish
curl -sS -X POST $API/incidents/$REF/run                     # -> resolved
```

The evaluation ID is derived rather than hardcoded, because it depends on how many tasks have
already been assured.

What that produces, all of it computed from seeded records:

| | |
| --- | --- |
| Risk | index 80, band `severe`, six named factors with observed values |
| `check_connections` | 8 of 10 connecting itineraries no longer feasible |
| `assess_crew_impact` | 2 crew rotations at risk for this flight |
| `notify_passengers` | held by the gate on `action_risk` alone, then 0 real and 174 simulated |
| Timeline | An ordered, append-only record per step, every action referencing its assurance evaluation |

The run stops at `awaiting_approval` because `notify_passengers` is a high-risk bulk external
effect and `high_risk_requires_human` is set. Every check passes; the gate holds it for what
the action *does*. That pause is the product, not a limitation.

`make demo-reset` returns to a clean injected state and is safe to run repeatedly.

### What has been verified, and where

Being precise about this matters more than a green tick.

| Verified | How |
| --- | --- |
| `alembic upgrade head`, `make seed`, `make demo` | Inside the built API image, against PostgreSQL 16 |
| The recovery journey above, to `resolved` | The real Uvicorn process over HTTP, against PostgreSQL 16, with Redis deliberately unreachable |
| Backend suite | 1068 passing; 1084 with `TRAVELOPS_TEST_DATABASE_URL` set, including 16/16 real-app PostgreSQL tests |
| Determinism | Seed digest `70fbdf8947c638e5` reproduced across runs |

**Not yet confirmed by anyone:** `docker compose up` orchestrating all four services together on
a real machine, and the console at `:5173`. That is team action 2 in
[`docs/31-team-actions.md`](docs/31-team-actions.md) and it is the one gap that needs a human with
Docker Desktop.

**The frontend runs with no backend.** `VITE_USE_FIXTURES=true` serves the committed fixtures in
`fixtures/api/`, so UI work never waits on an endpoint:

```bash
cd frontend && npm install && npm run dev
```

Useful checks:

| Command | Purpose |
| --- | --- |
| `make test` | Backend unit and contract tests |
| `make lint` | Ruff and ESLint |
| `make openapi` | Regenerate `docs/openapi.json` |
| `make verify-docs` | Confirm every relative doc link resolves |
| `cd frontend && npm run tokens:check` | Fail the build on a colour literal or banned hue |

## Project status

**[`docs/30-project-status.md`](docs/30-project-status.md)** is the single source of truth for what is
built, what remains, and the complete list of what the team needs to supply. Read it first.

The three actions only a human can do — Groq key, one run of the stack on the demo laptop, and SME
sign-off on the policy rules — are written out step by step in
**[`docs/31-team-actions.md`](docs/31-team-actions.md)**.

Agents working this repo get eight on-demand procedures from [`.kiro/skills/`](.kiro/skills/); see
[`docs/32-skills.md`](docs/32-skills.md).

## Start here

1. [`docs/DECISIONS.md`](docs/DECISIONS.md) — canonical decisions and event dates
2. [`docs/09-requirements.md`](docs/09-requirements.md) — scoped functional/non-functional requirements
3. [`docs/14-hackathon-plan.md`](docs/14-hackathon-plan.md) — stage-aligned delivery gates
4. [`docs/24-input-acquisition.md`](docs/24-input-acquisition.md) — what only the team must provide and where to get it
5. [`docs/25-evaluation-readiness.md`](docs/25-evaluation-readiness.md) — checkpoint pass/fail checklist
6. [`docs/21-design-system.md`](docs/21-design-system.md) — premium Operations Room UI; no purple
7. [`docs/26-implementation-contracts.md`](docs/26-implementation-contracts.md) — API, state, security and observability baseline
8. [`docs/27-ui-specification.md`](docs/27-ui-specification.md) — every screen, feature and interaction
9. [`docs/28-parallel-workstreams.md`](docs/28-parallel-workstreams.md) — running four Kiro accounts without merge conflicts, and which one needs the highest token limit
10. [`docs/kickoff/`](docs/kickoff/README.md) — four ready-to-paste prompts, one per Kiro account
11. [`docs/29-kickoff-prompts.md`](docs/29-kickoff-prompts.md) — wave sequencing and ownership rationale

## Core design documents

| Document | Purpose |
| --- | --- |
| [01 Architecture](docs/01-architecture.md) | Control plane and deterministic/LLM boundary |
| [03 Agent design](docs/03-agent-design.md) | 3 reasoning agents + 10 deterministic services |
| [11 Data model](docs/11-data-model.md) | PostgreSQL schema including assurance and policy packs |
| [13 Policy status](docs/13-compensation-and-policy.md) | Encoded MoCA charter rules; verified mode still blocked |
| [18 Assurance Gate](docs/18-decision-assurance-gate.md) | Deterministic execution gate replacing self-confidence |
| [19 Policy packs](docs/19-jurisdiction-and-policy-packs.md) | Jurisdiction-neutral rules + cited explanation |
| [20 Phased delivery](docs/20-phased-delivery.md) | Five demonstrable product phases |
| [22 Crew pairings](docs/22-crew-pairing-model.md) | Why 8 flights can affect 9 rotations |
| [23 Stack alignment](docs/23-stack-alignment.md) | Optional Coforge open-source list mapped honestly |

## Settled stack

| Concern | Choice |
| --- | --- |
| Frontend | React, TypeScript, Vite, Tailwind, themed shadcn/ui, React Query |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic |
| Orchestration | Custom typed Python workflow orchestrator |
| Data/events | PostgreSQL, Redis Streams + Redis |
| Reasoning | Open-weight Llama 3.3 70B via Groq; fixture/off modes mandatory |
| Retrieval | Explainable structured SQL for MVP; Chroma/BGE optional |
| Deployment | Local Docker Compose |
| Tests/observability | pytest, httpx, structlog, immutable decision records |

The Coforge AI-tool spreadsheet is a list of free suggestions, not a mandatory checklist. We use tools
only when they solve a real requirement. MCP, LangGraph, CrewAI, Kafka, Kubernetes and a graph database
are not required for this MVP.

## Non-negotiable rules

1. One orchestrator, three reasoning agents, ten deterministic services.
2. Structured model output; no parsing English for control flow.
3. Deterministic code for rules, calculations, validation and execution.
4. Decision Assurance Gate—not LLM self-reported confidence.
5. Pack status governs legal claims: `demo` fixture, `charter` (real cited figures, dated source),
   `verified` (current primary CAR + SME sign-off). Only `verified` is current law.
6. Every external provider has a fixture/offline implementation.
7. Every data surface states provenance.
8. Operations-console UI: graphite, instrument cyan, semantic status colours; no purple gradients or AI-template styling.
