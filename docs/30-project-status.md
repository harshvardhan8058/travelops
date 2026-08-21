# 30. Project Status — Single Source of Truth

The definitive answer to "what is done, what is left, and what do you need from us."

Verified against `main` at commit `17bf407`. Re-verify with:

```bash
cd backend && uv run pytest && uv run ruff check .
cd ../frontend && npm run typecheck && npm run lint && npm run tokens:check && npm run build
cd .. && python3 scripts/verify_docs.py
```

---

## 1. What the team must supply — the complete list

**Read this section once. Nothing else is needed from you.**

### Required — only these three

| # | Item | Blocks | Who to ask | Fallback if it never arrives |
| --- | --- | --- | --- | --- |
| 1 | **Groq API key** | Live reasoning (Phase 3) | Create at [console.groq.com](https://console.groq.com/) | `LLM_MODE=fixture` and `off` both work. Demo survives |
| 2 | **Run the stack once on the demo laptop** — ✅ API confirmed 21 Aug on Windows/Docker Desktop 29.x. Console and migration still to confirm | Confidence the stack starts on your hardware | You | None. This must actually be done |
| 3 | **Aviation/legal SME to sign off the policy rules** | `POLICY_MODE=verified` | Mentor, or Arcolab/CIMS architecture lead, or `TechCon.x@Coforge.com` | `POLICY_MODE=charter` shows real cited figures behind a dated badge |

**The Groq key goes in `backend/.env` as `GROQ_API_KEY=`. Never paste it into chat, a commit,
an issue or a screenshot.** Only the rate limits shown in your console are safe to share.

### Optional — improves the demo, blocks nothing

| Item | Effect if absent |
| --- | --- |
| Mailtrap or Gmail SMTP + 2–3 allowlisted inboxes | Notifications record as simulated instead of sending one real email |
| Official Coforge internal-tool name **and** team access | That scoring line is omitted rather than invented |
| Current DGCA CAR PDF (development will attempt the public download first) | Stays in `charter` mode |
| 20-minute airline-operations SME call | Problem and value framing stay labelled as hypotheses |

### Explicitly NOT needed — do not spend time on these

Real passenger or PNR data · paid flight-status, hotel or GDS APIs · SMS gateway · real
bookings, payments or refunds · crew duty-time legality rules · Kubernetes, Kafka, RabbitMQ
or a graph database · MCP, LangGraph, CrewAI or AutoGen · EU/UK/US policy packs · production
cloud infrastructure.

Every one of those is either simulated by design or deliberately out of scope. Weather needs
no key. Airport data is a public CSV. Everything else is generated from a fixed seed.

---

## 2. Is the UI user-centric? Honestly assessed

**Design: yes, deliberately and specifically. Validation: not yet.**

The interface is built for one named user in one named situation — an **Operations Controller
during a live disruption** — and every screen is justified against four questions that user
must answer within ten seconds:

1. What is broken, and how bad is it?
2. What has the system already done without me?
3. What is waiting for my decision?
4. Can I trust the number I am looking at?

A feature that serves none of those is not built, which is why there is no chat interface, no
passenger portal, no dashboard customisation and no mobile layout.

Concrete decisions that follow from taking that user seriously:

| Decision | The user problem it solves |
| --- | --- |
| Persistent timeline rail on every route | "What happened while I was looking elsewhere?" |
| Blocked-actions bar always visible | A decision waiting on you must never be hidden behind navigation |
| `WhyPopover` on every derived number | "Can I defend this figure to my manager?" |
| Provenance dot on every panel | "Is this real data or a simulation?" |
| Observation age turns amber *before* the gate blocks | Understand the block before it happens, not after |
| 14px body, 34px rows, tabular monospace | Controllers scan hundreds of rows; alignment beats prettiness |
| Status = icon **and** label **and** colour | Colour-blind users, and projectors that wash out hue |
| Risk shown as index + band, never a bare % | An uncalibrated percentage invites a question we cannot answer |
| Empty states say what to do next | A blank panel mid-demo reads as broken |

**The honest gap:** this is *expert-informed design*, not *user-validated design*. No airline
operations controller has used it. Until the SME call happens, describe it as "designed for
the operations-controller workflow", never as "validated with controllers."

What would upgrade it to validated: a 20-minute session where a controller attempts one
recovery unaided, and we record where they hesitate. That is the single highest-value hour
available and it is currently unscheduled.

---

## 3. What is DONE — verified, on `main`

### Infrastructure

- `docker compose` stack: API, PostgreSQL, Redis, web. Loopback-only host publication;
  datastores not host-published at all; healthchecks on all three backing services.
- `Makefile` with `doctor`, `env`, `up`, `migrate`, `seed`, `reset`, `demo`, `test`, `lint`,
  `openapi`, `verify-docs`.
- `scripts/doctor.sh` pre-flight check for the demo machine.
- `.env.example` where every mode fails closed.

### Backend — 66 Python files

- **Config** with fail-closed resolution: refuses live-LLM-without-key, refuses
  `POLICY_MODE=verified`, refuses SMTP-without-credentials. Degrades only when explicitly
  permitted, and always reports it.
- **Schema — 33 tables.** Crew modelled as pairings → legs → flights with roles, so the
  8-flights/9-rotations claim is countable. `action.assurance_id` is `NOT NULL`.
  `human_decision` append-only and unique per evaluation. Policy applicability tri-state.
  Partial unique index enforcing one active incident per flight.
- **Migration** `0001_initial_schema` renders valid Postgres DDL: 34 tables, JSONB preserved,
  partial index with its `WHERE` clause.
- **Incident state machine** with the full legal transition table and a test proving
  `executing` is unreachable except through `assuring`.
- **Contracts:** nine typed events; three *distinct* reasoning payloads discriminated by
  `payload_type`; six-check assurance record where `WARN` is representable; provider protocols
  with typed error kinds.
- **Gate config** `config/assurance.v1.yaml`, versioned and hashed. Unknown action type ⇒
  **high** risk. `WARN` reaches `execute_flagged` only for three explicitly listed low-risk
  actions.
- **Working endpoints:** `/health/live`, `/health/ready`, `/system/mode`, plus nine
  fixture-backed endpoints so the frontend is never blocked.
- **Crosswind trigonometry**, tested, because a units error there would invalidate every risk
  score downstream.
- **96 tests passing.** Ruff check and format clean across 66 files.

### Frontend — 9 TypeScript files

- Token layer: graphite base, instrument-cyan accent, semantic state ramp. Tailwind's
  `theme.colors` **replaced** rather than extended, so purple/violet/indigo/fuchsia are
  unavailable by construction.
- `scripts/check-tokens.mjs` fails the build on any hand-written colour literal.
- Primitives: `MonoValue`, `StateBadge`, `RiskChip`, `ProvenanceDot`, `WhyPopover`, `Panel`,
  `EmptyState`, `LoadingState`, `ErrorState`, `AgeIndicator`.
- App shell: icon rail, top bar with mode chips and the policy badge, degradation banner,
  persistent timeline rail, blocked-actions bar.
- Ops Board (network strip + flight board) and Decision Timeline, both working.
- Typed client with `VITE_USE_FIXTURES` so the entire UI runs with **no backend**.
- Typecheck, ESLint, Prettier, token guard and production build all clean.

### Policy

- **MoCA Passenger Charter (Feb 2019) encoded** as a versioned pack: 40 rules, 23 test cases,
  8 open review questions. Status `official_guidance_dated`, so it produces real cited figures
  but can never satisfy `verified` mode.
- Three corrections the source forced: delay attracts **no** cash compensation in this
  instrument; the hotel trigger is narrower than we had documented; meals thresholds are tiered
  by block time.
- The 24-hour cancellation rule is marked `superseded_suspected` and excluded from evaluation.

### Documentation — 42 files

Architecture, requirements, data model, assurance gate, policy packs, crew pairings, phased
delivery, design system, UI specification, workstream ownership, and four ready-to-paste
kickoff prompts.

---

## 4. What is NOT done — the honest list

Eighteen files contain `NotImplementedError`. That is deliberate: Wave 0 built the contracts
and left each stream's logic to its owner, with required behaviour in the docstring.

The work is allocated across **four** Kiro accounts, not six. Ownership rationale and the
token-load ranking: [`28-parallel-workstreams.md`](28-parallel-workstreams.md). Paste-ready
prompts: [`kickoff/`](kickoff/README.md).

| Stream | Files to implement | First slice | Token load |
| --- | --- | --- | --- |
| **A · Core & API** | `orchestrator/engine.py`, `events/bus.py` (new), `cli.py`, 9 endpoints out of `fixtures_router.py`, `agents/`, `observability/` | Event bus, then the engine run loop | Medium |
| **B · Assurance & Policy** | `assurance/checks.py`, `assurance/gate.py`, `policy/{loader,resolver,engine}.py` | Six checks as pure functions | **Lowest** |
| **C · Data, Providers & Services** | `providers/*` implementations, `data/loaders/`, `data/generators/`, 10 service `execute()` bodies, `memory/retrieval.py` | Airport loader, then the pairing generator | **Second highest** |
| **D · Frontend** | `WhyPopover` upgrade, recovery workspace, assurance panel, approval queue, policy citation, cascade, report, sources, replay, command palette | Positioned popover, then the workspace layout | **Highest** |

Streams C and D each carry more than a sprint, so both prompts front-load the Stage 2 demo and
mark the rest deferrable. C does Delay Risk, Connection, Crew Impact and Communication before
the other six services. D does the recovery workspace and assurance panel before the other
screens. Hitting a quota ceiling then costs a screen or a deferred service, never the demo.

### Not verified, and I will not claim otherwise

- **`docker compose up` now confirmed working** on Windows with Docker Desktop 29.x (WSL2) on
  21 August: the stack builds, starts, and `/docs` serves all 12 endpoints. Still unconfirmed on
  that machine: postgres/redis health, `alembic upgrade head`, and the console at `:5173`.
- **The `make` targets do not run on Windows.** PowerShell equivalents are in
  [`31-team-actions.md`](31-team-actions.md).
- **Fixture endpoints have no response models.** They return `Any`, so OpenAPI renders their
  schema as `"string"`. Harmless now — Stream D hand-wrote its types — but Stream A must add a
  Pydantic response model to each endpoint it makes real, otherwise a generated client would be
  useless. The `add-api-endpoint` skill now requires this.
- No live Groq call has been made.
- No live METAR fetch has been made.
- No real email has been sent.
- No airline SME has reviewed the workflow.
- No legal reviewer has approved the policy rules.

---

## 5. Definition of "project done"

By stage, from `docs/25-evaluation-readiness.md`:

**Stage 2 (20–24 Aug)** — one disrupted flight recovered end to end with the assurance gate,
`LLM_MODE=off`, traceable cascade data, Ops Board and timeline. No AI required.

**Stage 3 (1–2 Sep)** — three reasoning agents behind typed contracts, all three LLM modes
working, SQL precedent, charter-mode policy flow with citations.

**Semi-finals (9–10 Sep)** — full 8-flight cascade with the 9-pairing graph, replay,
executive report, three clean seven-minute rehearsals, offline backup video.

**Finals (16 Sep)** — hardened, frozen, measured prototype metrics, stated production gaps.

### The four things that must never be cut

1. The deterministic recovery path
2. The Decision Assurance Gate and its audit trail
3. `LLM_MODE=off` completing a recovery
4. Provenance labels and citation on every claim

---

## 6. For the other agents

Point every session at its file in [`kickoff/`](kickoff/README.md). Each is a single paste with
no placeholders, lists what is already built so nothing is rebuilt, and ends by asking the
session to state its plan first.

Three rules that prevent the usual disaster:

1. **`migrations/` is Stream C only.** Two streams autogenerating produces unorderable heads.
2. **Review the PR file list before the code.** Touching unowned paths is the finding.
3. **Everything mergeable merges daily.** A conflict found on day two costs an hour; the day
   before Stage 2 it costs the demo.
