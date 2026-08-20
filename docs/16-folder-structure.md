# 16. Folder Structure and Coding Standards

Resolves backlog item #7. Follows the Master Blueprint layout, adapted to the confirmed stack in
[`DECISIONS.md`](DECISIONS.md).

## Layout

```
travelops/
├── docker-compose.yml
├── .env.example
├── Makefile                       # make up / seed / test / demo
│
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                # FastAPI entrypoint
│   │   ├── config.py              # settings; NO hardcoded thresholds (A5)
│   │   │
│   │   ├── agents/
│   │   │   ├── base.py            # Agent ABC + response contract
│   │   │   ├── prediction.py      # rules engine, no LLM
│   │   │   ├── planner.py         # Groq
│   │   │   ├── flight_recovery.py
│   │   │   ├── hotel.py
│   │   │   ├── transport.py
│   │   │   ├── communication.py
│   │   │   ├── finance.py         # DGCA compensation, no LLM
│   │   │   ├── crew.py            # coordination only, not legality
│   │   │   ├── analytics.py
│   │   │   └── learning.py
│   │   │
│   │   ├── orchestrator/
│   │   │   ├── engine.py          # workflow execution, parallelism
│   │   │   ├── limits.py          # recursion / iteration / timeout caps
│   │   │   └── state.py           # incident state transitions
│   │   │
│   │   ├── events/
│   │   │   ├── bus.py             # Redis Streams
│   │   │   └── types.py           # typed event definitions
│   │   │
│   │   ├── services/              # deterministic; agents call these
│   │   │   ├── weather.py         # aviationweather.gov + Open-Meteo
│   │   │   ├── flight_sim.py      # local flight state machine
│   │   │   ├── hotel_search.py    # SQL
│   │   │   ├── compensation.py    # DGCA rules table
│   │   │   ├── notification.py    # channel interface
│   │   │   └── validation.py      # schema + policy gate
│   │   │
│   │   ├── memory/
│   │   │   ├── retrieval.py       # SQL precedent (D2)
│   │   │   └── outcomes.py        # incident outcome recording
│   │   │
│   │   ├── llm/
│   │   │   ├── client.py          # Groq wrapper: retry, cache, fixtures
│   │   │   ├── prompts/           # versioned .md files, one per agent
│   │   │   │   ├── planner.v1.md
│   │   │   │   ├── explainer.v1.md
│   │   │   │   └── report.v1.md
│   │   │   └── fixtures/          # recorded responses for offline dev
│   │   │
│   │   ├── models/                # SQLAlchemy + Pydantic schemas
│   │   ├── api/                   # routers
│   │   └── migrations/
│   └── tests/
│
├── frontend/
│   ├── package.json
│   └── src/
│       ├── api/                   # generated/typed client
│       ├── components/ui/         # shadcn
│       ├── features/
│       │   ├── dashboard/
│       │   ├── incident/
│       │   ├── timeline/          # replay
│       │   ├── analytics/
│       │   └── reports/
│       └── types/
│
├── data/
│   ├── loaders/                   # REAL data
│   │   ├── load_airports.py
│   │   ├── load_schedules.py
│   │   └── backfill_weather.py
│   ├── generators/                # SYNTHETIC data
│   │   ├── generate_passengers.py
│   │   ├── generate_hotels.py
│   │   ├── generate_crew.py
│   │   └── generate_history.py
│   ├── fixtures/
│   │   └── bengaluru_storm.yaml
│   └── dumps/
│       └── demo_dataset.sql       # frozen, committed (Day 6)
│
└── docs/                          # this directory
```

## Why it is shaped this way

**`agents/` versus `services/` is the load-bearing split.** Agents decide; services do. An agent has a
goal, tools and constraints. A service is a deterministic function. If a file in `services/` imports
the Groq client, something has gone wrong.

**`llm/prompts/` holds versioned files, not inline strings.** This is the mitigation for prompt drift
(risk #9). One prompt per agent, changes reviewed like code.

**`llm/fixtures/` is not optional.** With ~100K Groq tokens/day, offline development is a hard
requirement. Recorded responses let the UI and orchestration be built without spending budget.

**`data/loaders/` versus `data/generators/` keeps real and synthetic separable.** This matters for
honesty — you must be able to answer "which data is real?" precisely.

**`orchestrator/limits.py` is its own module** so loop caps are impossible to overlook. Risk #2 is easy
to forget until an agent loop burns the token budget in ten minutes.

## Coding standards

### Python

| Rule | Detail |
| --- | --- |
| Formatter / linter | `ruff` — format and lint, one tool |
| Type hints | Required on all public functions |
| Async | `async def` for I/O; never block the event loop |
| Config | Pydantic settings from env. **No magic numbers in code** |
| Errors | Typed exceptions; never bare `except:` |
| Logging | Structured (`structlog`); every agent action logs to `decision_log` |

### The agent contract

Every agent implements the same interface and returns the same shape:

```python
class AgentResponse(BaseModel):
    status: Literal["success", "failure", "skipped", "needs_human"]
    confidence: int          # 0-100
    action: str              # known enum value
    reason: str              # human-facing, surfaced in UI
    payload: dict = {}
    cost_inr: int | None = None
```

Never return prose. The orchestrator branches on `status` and thresholds on `confidence`; it must never
parse English. See [`03-agent-design.md`](03-agent-design.md).

### TypeScript

| Rule | Detail |
| --- | --- |
| `strict` mode | On. No `any` |
| Types | Generated from the OpenAPI schema — never hand-written |
| Components | Function components; hooks for state |
| Server state | React Query; no manual fetch/`useEffect` chains |
| Styling | Tailwind + shadcn, **theme overridden** per [`21-design-system.md`](21-design-system.md). Tokens only — no colour literals in components. No bespoke CSS files |
| Icons | Lucide only. 16px dense, 20px rail, `1.5` stroke |

### Naming

| Thing | Convention |
| --- | --- |
| Python modules, functions | `snake_case` |
| Python classes | `PascalCase` |
| React components | `PascalCase` |
| Database tables, columns | `snake_case`, singular table names |
| Event types | `SCREAMING_SNAKE_CASE` (`HIGH_RISK_DELAY`) |
| Action types | `snake_case` (`reserve_hotels`) |
| Prompt files | `<agent>.v<n>.md` |

### Git

Trunk-based, short-lived branches, `main` always runnable.

```
feat(agents): add hotel agent with budget constraint
fix(orchestrator): enforce max iteration cap
docs: record DGCA compensation rules
```

**`main` must always run.** During a 7-day sprint with daily integration, a broken `main` blocks three
other people.

### Testing

Given [`14-hackathon-plan.md`](14-hackathon-plan.md), testing is targeted rather than exhaustive.

| Priority | What | Why |
| --- | --- | --- |
| Must | Compensation calculator | Regulatory correctness; pure function, trivial to test |
| Must | Prediction rules engine | Deterministic; drives the whole demo |
| Must | Plan schema validation | Guards the LLM boundary |
| Must | End-to-end `bengaluru_storm` | The demo itself |
| Should | Idempotency of execution actions | Prevents double-booking |
| Should | Fallback playbook with Groq disabled | This is Act 6 of the demo |
| Skip | UI unit tests | Rehearsal covers it more cheaply |
| Skip | LLM output assertions | Non-deterministic; assert the schema, not the content |

Test the deterministic half properly and the demo path end to end. Do not write tests that assert what
a language model said.

## Environment

```bash
# .env.example
GROQ_API_KEY=
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_TEMPERATURE=0.1              # NFR-1 reproducibility
LLM_MODE=live                     # live | fixture | off  <- 'off' powers demo Act 6

DATABASE_URL=postgresql://travelops:travelops@localhost:5432/travelops
REDIS_URL=redis://localhost:6379/0

WEATHER_POLL_SECONDS=60
DELAY_RISK_THRESHOLD=0.75
HOTEL_MAX_RATE_INR=6000           # A5: config, never hardcoded
MEAL_THRESHOLD_MINUTES=120        # DGCA
HOTEL_THRESHOLD_MINUTES=360       # DGCA

MAX_RECURSION_DEPTH=5
MAX_ITERATIONS=20
AGENT_TIMEOUT_SECONDS=30

NOTIFICATION_MODE=mailtrap        # mailtrap | gmail | console
DATA_SEED=20260807
```

`LLM_MODE=off` is deliberately a first-class configuration value, not a hack. It is both the graceful
degradation requirement (NFR-5) and the demo's strongest moment.
