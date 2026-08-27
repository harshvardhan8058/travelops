# 16. Folder Structure and Coding Standards

Canonical implementation layout for **1 orchestrator + 3 reasoning agents + 10 deterministic services**.
It replaces the older layout that incorrectly put rules engines and integrations under `agents/`.

## Layout

```text
travelops/
├── docker-compose.yml
├── .env.example
├── Makefile                         # up / seed / test / demo / reset
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                  # FastAPI entrypoint
│   │   ├── config.py                # typed settings; no magic numbers
│   │   ├── api/                     # routers + generated OpenAPI
│   │   ├── models/                  # SQLAlchemy and Pydantic schemas
│   │   ├── migrations/              # Alembic
│   │   │
│   │   ├── agents/                  # ONLY the three reasoning components
│   │   │   ├── contract.py
│   │   │   ├── planner.py
│   │   │   ├── explainer.py
│   │   │   └── report_generator.py
│   │   │
│   │   ├── services/                # deterministic capabilities; never import LLM client
│   │   │   ├── delay_risk.py
│   │   │   ├── flight_recovery.py
│   │   │   ├── hotel.py
│   │   │   ├── transport.py
│   │   │   ├── communication.py
│   │   │   ├── compensation.py
│   │   │   ├── crew_impact.py
│   │   │   ├── connection.py
│   │   │   ├── resource.py
│   │   │   └── analytics_learning.py
│   │   │
│   │   ├── orchestrator/
│   │   │   ├── engine.py            # sequencing, retries, idempotency, parallel tasks
│   │   │   ├── state.py             # incident state machine
│   │   │   └── limits.py            # iteration and timeout caps
│   │   ├── assurance/
│   │   │   ├── gate.py              # pure deterministic aggregation
│   │   │   ├── checks.py            # six checks
│   │   │   └── config.py            # version + hash, fail closed
│   │   ├── policy/
│   │   │   ├── resolver.py          # trip context → applicable packs
│   │   │   ├── engine.py            # generic rule DSL
│   │   │   ├── loader.py
│   │   │   └── schemas.py
│   │   ├── providers/               # external boundaries, each with fixture implementation
│   │   │   ├── weather/
│   │   │   ├── flight_status/
│   │   │   ├── schedules/
│   │   │   ├── notifications/
│   │   │   └── llm/
│   │   ├── events/                  # Redis Streams + typed events
│   │   ├── memory/                  # SQL precedent + outcomes
│   │   ├── observability/           # structlog + decision/event records
│   │   └── llm/
│   │       ├── prompts/              # planner/explainer/report, versioned files
│   │       └── fixtures/             # recorded structured responses
│   └── tests/
│       ├── unit/                     # services, gate, policy DSL
│       ├── contract/                 # providers and API schemas
│       └── e2e/                      # bengaluru_storm
├── frontend/
│   ├── package.json
│   └── src/
│       ├── api/                      # generated typed client
│       ├── components/ui/            # themed shadcn primitives
│       ├── design/                   # tokens from docs/21
│       └── features/
│           ├── ops-board/
│           ├── cascade/
│           ├── timeline/
│           ├── assurance/
│           ├── policy-citation/
│           └── reports/
├── policy_packs/
│   ├── demo-policy-fixture/          # fictional; proves the engine
│   └── in-moca-charter-2019/2019.02/ # official guidance, dated; labelled in UI
├── data/
│   ├── loaders/                      # public real data
│   ├── generators/                   # synthetic data
│   ├── fixtures/bengaluru_storm.yaml
│   └── dumps/demo_dataset.sql
└── docs/
```

## Dependency rules

1. `agents/` may call typed tools through the orchestrator; agents never execute side effects directly.
2. `services/` and `assurance/` must never import an LLM client.
3. `providers/` own network I/O. Every provider has a fixture/offline implementation.
4. `policy/engine.py` is jurisdiction-neutral. DGCA-specific data lives only in a policy pack.
5. `frontend/` consumes generated OpenAPI types; never hand-maintain a parallel API model.
6. Real, simulated, synthetic and fixture data stay distinguishable in storage and API responses.

## Reasoning-agent contract

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field

class AgentEnvelope(BaseModel):
    status: Literal["success", "failure", "skipped", "needs_human"]
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    payload_type: str

class PlanTask(BaseModel):
    action: str                      # validated against the known action enum
    target_refs: list[str]
    inputs: dict[str, object] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)

class PlannerResponse(AgentEnvelope):
    payload_type: Literal["planner.v1"]
    tasks: list[PlanTask]

class ExplanationResponse(AgentEnvelope):
    payload_type: Literal["explanation.v1"]
    explanation: str
    citation_refs: list[str]

class ReportResponse(AgentEnvelope):
    payload_type: Literal["report.v1"]
    summary: str
    sections: list[dict[str, object]]
    metric_refs: list[str]

ReasoningResponse = Annotated[
    PlannerResponse | ExplanationResponse | ReportResponse,
    Field(discriminator="payload_type"),
]
```

`confidence` is deliberately absent. If a model emits one, store it separately as
`model_self_report` in model-call audit metadata and never branch on it. Only
`PlannerResponse.tasks[]` enters action-enum/entity validation and then the Decision Assurance Gate;
Explanation and Report responses are read-only artifacts.

## Python standards

| Rule | Standard |
| --- | --- |
| Runtime | Python 3.12 |
| Dependency/build | `uv` + `pyproject.toml` |
| Formatting/lint | Ruff |
| Types | Type hints on public functions; mypy/pyright-compatible |
| I/O | Async where the library supports it; no blocking calls in request handlers |
| Config | Pydantic Settings from environment; missing safety config fails closed |
| Errors | Typed exceptions; no bare `except` |
| Logging | Structlog; correlation ID, incident ID and actor on every record |
| Time | UTC in storage, explicit local zone only in display |
| Money | Integer minor units or integer INR for the demo; never float |

## TypeScript and UI standards

| Rule | Standard |
| --- | --- |
| TypeScript | `strict`; no `any` |
| Server state | React Query; no manual fetch chains |
| Styling | Tailwind + shadcn theme overridden by [`21-design-system.md`](21-design-system.md) |
| Colour | Tokens only; zero purple/violet/indigo; no colour literals in components |
| Icons | Lucide only, 16px dense / 20px rail, 1.5 stroke |
| Data typography | JetBrains Mono + tabular numerals |
| Accessibility | WCAG AA, visible focus, status uses icon + label—not colour alone |

## API and persistence standards

- Resources use stable IDs; mutation endpoints accept `Idempotency-Key`.
- Every response that mixes data sources includes `provenance` (`real`, `simulated`, `synthetic`,
  `fixture`, `unavailable`) plus source timestamp where applicable.
- Every action references the immutable assurance evaluation that authorised or blocked it; an action
  following `needs_human` also references the matching immutable approval record.
- Every entitlement references policy pack, pack version, rule ID and source clause.
- API contract and state transitions are specified in [`26-implementation-contracts.md`](26-implementation-contracts.md).

## Test priorities

| Priority | Scope |
| --- | --- |
| Must | Assurance aggregation and fail-closed behaviour |
| Must | Policy DSL and every verified entitlement rule |
| Must | Delay-risk rule output and units |
| Must | Agent schema validation and unknown-action rejection |
| Must | Idempotency and conflict detection |
| Must | End-to-end `bengaluru_storm` in fixture and LLM-off modes |
| Should | Provider contracts, cold start and reset |
| Should | Accessibility smoke check and projector viewport |
| Avoid | Assertions on free-form LLM wording |

## Environment contract

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://travelops:travelops@postgres:5432/travelops
REDIS_URL=redis://redis:6379/0

LLM_MODE=fixture                       # live | fixture | off
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-120b         # llama-3.3-70b-versatile retired 2026-08-16

WEATHER_MODE=fixture                   # live | fixture
WEATHER_POLL_SECONDS=60
DELAY_RISK_EVENT_THRESHOLD=75          # risk index, not calibrated probability

NOTIFICATION_MODE=console              # console | mailtrap | gmail
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
DEMO_RECIPIENT_ALLOWLIST=

POLICY_MODE=charter                    # demo | charter | verified
POLICY_PACK_DIR=/app/policy_packs
ASSURANCE_CONFIG_PATH=/app/config/assurance.v1.yaml

MAX_WORKFLOW_STEPS=20
ACTION_TIMEOUT_SECONDS=30
DATA_SEED=20260807
```

No unverified DGCA threshold or amount belongs in environment defaults. Verified values live in a
reviewed, versioned policy pack.

## Git discipline

- Short-lived branches; `main` runnable.
- Conventional commits.
- Never commit secrets, real PII, SMTP credentials or personal email addresses.
- Freeze the dataset, policy-pack hashes and prompts before each evaluation.
