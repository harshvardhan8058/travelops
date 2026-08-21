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
| Current repository status | Wave 0 bootstrap complete: runnable scaffold, schema, contracts, fixtures and UI shell |
| Submitted deck | Frozen; see [`docs/17-presentation-prompt.md`](docs/17-presentation-prompt.md) |
| Next build target | Stage 2 working deterministic vertical slice |

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
```

Then open <http://127.0.0.1:8000/docs> for the API and <http://127.0.0.1:5173> for the console.

Ports bind to `127.0.0.1` only. PostgreSQL and Redis are not published to the host at all —
use `make db-shell`.

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

## Start here

1. [`docs/DECISIONS.md`](docs/DECISIONS.md) — canonical decisions and event dates
2. [`docs/09-requirements.md`](docs/09-requirements.md) — scoped functional/non-functional requirements
3. [`docs/14-hackathon-plan.md`](docs/14-hackathon-plan.md) — stage-aligned delivery gates
4. [`docs/24-input-acquisition.md`](docs/24-input-acquisition.md) — what only the team must provide and where to get it
5. [`docs/25-evaluation-readiness.md`](docs/25-evaluation-readiness.md) — checkpoint pass/fail checklist
6. [`docs/21-design-system.md`](docs/21-design-system.md) — premium Operations Room UI; no purple
7. [`docs/26-implementation-contracts.md`](docs/26-implementation-contracts.md) — API, state, security and observability baseline
8. [`docs/27-ui-specification.md`](docs/27-ui-specification.md) — every screen, feature and interaction
9. [`docs/28-parallel-workstreams.md`](docs/28-parallel-workstreams.md) — running six Kiro accounts without merge conflicts
10. [`docs/kickoff/`](docs/kickoff/README.md) — six ready-to-paste prompts, one per Kiro account
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
