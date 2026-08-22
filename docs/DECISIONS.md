# Decisions

All previously open questions, now answered. This supersedes most of the original
`OPEN-QUESTIONS.md`; what remains genuinely unresolved is in
[`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

Recorded so that nobody re-litigates a settled decision mid-sprint, and so the *reasoning* survives
alongside the choice.

---

## The hackathon

### Official registration (canonical — use these exact strings everywhere)

| Field | Value |
| --- | --- |
| **Registration ID** | 201 |
| **Team Name** | SkyForge AI |
| **Industry** | Travel Transport Hospitality (TTH) |
| **Sub-Industry** | Airlines Operations |
| **Project / Use Case Title** | TravelOps AI |

**Naming rule:** *SkyForge AI* is the **team identity**. *TravelOps AI* is the **project / use case
title**. Write "TravelOps AI, by Team SkyForge AI" — never merge the two into one product name, and never
use "TravelOps OS" (an earlier working title, now retired).

### Team members

| Name | ID |
| --- | --- |
| Harshvardhan Sharma | 136764 |
| Karthikeyan D | 138062 |
| Harshvardhan Jha | 136761 |
| Sabyasachin Biswal | 136794 |

Project: Arcolab · Department: CIMS

### Event

| | |
| --- | --- |
| **Event** | TechCon 2026 Hackathon (Coforge) |
| **Theme** | Engineering the Autonomous Enterprise using AI, internal tools, and open-source technologies |
| **Idea submission** | 10 August 2026 — complete; submitted deck frozen |
| **Stage 1 evaluations** | 14–16 August 2026 |
| **Stage 2 evaluations** | 20–24 August 2026 |
| **Stage 3 evaluations** | 1–2 September 2026 |
| **Semi-finals** | 9–10 September 2026 |
| **Finals** | 16 September 2026 |
| **Working model** | Iterative delivery through checkpoints; every stage ends with a working vertical slice |

### Team

| Member | Responsibility |
| --- | --- |
| Harsh | AI, backend, orchestration, architecture |
| Member 2 | Frontend dashboard |
| Member 3 | APIs, database, integrations |
| Member 4 | Testing, PPT, demo, documentation |

See [`14-hackathon-plan.md`](14-hackathon-plan.md) for the stage-aligned plan.

### Judging criteria (official)

- Creativity
- Feasibility
- Relevance
- Use of Internal Tools
- Use of Open Source
- Engineering the Autonomous Enterprise

### Effort weighting (our interpretation)

| Dimension | Weight |
| --- | --- |
| Innovation | 35% |
| Architecture | 25% |
| Autonomy | 20% |
| Demo | 15% |
| Business value | 5% |

**Governing rule: don't build another chatbot. Build autonomous workflows.**

Note that "Use of Internal Tools" is an official criterion we cannot currently design for — see
[`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

---

## Positioning

Present the project as:

> **TravelOps AI** — an autonomous operating layer for airline disruption recovery.
> Built by **Team SkyForge AI** (Registration ID 201).

⚠️ **Superseded.** An earlier draft proposed renaming the product to "TravelOps OS". The registration
sheet fixes the official title as **TravelOps AI**, so that rename is retired. Registration naming wins
over positioning preference.

The *narrative* framing survives and is still worth using in the pitch: a chatbot answers questions, a
dashboard displays data, but an **operating layer** coordinates people, services and agents to keep
operations running. Describe it that way in prose — "an operating layer, not an assistant" — without
altering the registered title.

The repository remains `travelops` — renaming it buys nothing.

---

## Resolved: blocking questions

| ID | Question | Decision |
| --- | --- | --- |
| B1 | Hackathon and timeline | TechCon 2026 (Coforge); iterative checkpoints through 16 Sep; 4 members |
| B2 | Judging criteria | As above; effort weighted toward innovation and architecture |
| B3 | Ops-facing or passenger-facing | **Operations-first.** Passenger portal is a secondary interface |
| B4 | Groq access | Confirmed. Free tier |
| B5 | Language and stack | FastAPI / React / Postgres — see below |

### B3 — user hierarchy

Primary user is the **Operations Controller**. Others are secondary surfaces, built only if time allows:

- Operations Controller ← primary
- Airline Operations Manager
- Executive Dashboard
- Customer Support
- Passenger Portal

### B4 — Groq models

| Model | Use |
| --- | --- |
| `llama-3.3-70b-versatile` | Primary planner |
| `qwen3` | Alternate / comparison |
| `deepseek-r1-distill` | Reasoning-heavy explanation |

Groq performs **planning, reasoning, explanations and reports only**. Everything else is
deterministic code. Unchanged from [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md).

### B5 — confirmed stack

| Layer | Choice |
| --- | --- |
| Backend | FastAPI |
| Frontend | React, TypeScript, Tailwind, shadcn/ui |
| Database | Postgres |
| Cache | Redis |
| Queue / events | Redis Streams |
| Embeddings | BGE Small (local) |
| Vector store | Chroma — *deferred, see D2* |
| Deployment | Docker, local |

Explicitly rejected as overkill: **Kubernetes, Kafka, RabbitMQ**.

This supersedes the earlier pgvector recommendation in [`10-data-sources.md`](10-data-sources.md).

---

## Resolved: assumptions

| ID | Assumption | Outcome |
| --- | --- | --- |
| A1 | Primary actor is Operations Controller | ✅ Confirmed |
| A2 | One disrupted flight at a time | ❌ **Changed — cascading required** |
| A3 | India-focused: DGCA, AAI, INR | ✅ Confirmed |
| A4 | Weather is the primary trigger | ✅ Confirmed; others are future work |
| A5 | ₹6,000 hotel cap | ✅ Correct value, but **config, never hardcoded** |
| A6 | Bookings simulated | ✅ Confirmed |
| A7 | Local 384-dim embeddings | ✅ Confirmed — BGE Small |
| A8 | Local deployment | ✅ Confirmed — local Docker, offline-capable |

### A2 — cascading disruption (the significant change)

The MVP must model a cascade, not a single flight:

```
Storm
  ↓
8 flights delayed
  ↓
600 passengers
  ↓
22 missed connections
  ↓
11 hotels
  ↓
9 crew changes
  ↓
Ground transportation
  ↓
Executive report
```

This is a material scope increase and the right call — a single-flight demo understates the platform.
It also makes the orchestrator genuinely load-bearing, which serves the Architecture and Autonomy
weightings.

**Consequences:**

- Requires an incident-group concept: one weather event owning many flight incidents.
- Introduces **crew** and **ground transport** as first-class entities.
- Multiplies the state-management risk (#7 in [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md)):
  11 hotels across 600 passengers with finite capacity means contention is now real, not theoretical.
- Executive reporting becomes a deliverable rather than a nice-to-have.

⚠️ **Crew scope needs bounding.** "9 crew changes" can mean two very different things: displaying and
coordinating crew reassignment, or actually validating crew duty-time legality. The second is a hard
regulated domain and would consume the sprint. Recommendation: treat crew as a **coordination and
display** concern with simple duty-hour flags, and state that legality checking is out of scope.
Flagged in [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

### A4 — future triggers

Weather for MVP. Later: crew, ATC, maintenance, vendor failure, runway, security.

Per [`13-compensation-and-policy.md`](13-compensation-and-policy.md), these are not cosmetic
variations: trigger type determines whether cash compensation is legally owed.

---

## Resolved: design decisions

### D1 — airports

Ten Indian airports:

```
BLR  DEL  BOM  HYD  MAA  CCU  COK  GOI  AMD  PNQ
```

Enough network diversity to make cascading realistic without unnecessary complexity. BLR remains the
demo scenario, and is one of the four metros in DGCA's published on-time performance data.

This narrows the earlier ~40-airport sizing in [`12-synthetic-data-plan.md`](12-synthetic-data-plan.md).

### D2 — retrieval strategy: **skip vectors for the MVP**

Structured SQL retrieval instead:

```
Airport + Trigger + Severity + Weather + Flight Type
        ↓
      SQL
        ↓
Historical incidents
```

Add embeddings only if time permits.

This resolves a tension worth naming explicitly: B5 lists BGE Small and Chroma, and D6 lists "RAG" as
a must-build, yet D2 defers vectors. These are compatible — **retrieval-augmented generation does not
require embeddings.** Retrieving precedent by structured filtering and injecting it into the planner
prompt *is* RAG. At ~150 historical incidents, a `WHERE` clause on airport, trigger and severity will
retrieve better precedent than cosine similarity, and it is explainable in a way embeddings are not.

Chroma and BGE Small stay in the stack as a stretch goal, not a dependency.

### D3 — regulatory policy: architecture settled; source verification outstanding

The deterministic policy-pack architecture is complete in
[`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md). The legal content is **not**
complete: the current DGCA primary CAR, revision metadata and SME review are not yet archived.

The team supplied the **MoCA Passenger Charter (February 2019)**, now encoded as
`policy_packs/in-moca-charter-2019/2019.02/` with status `official_guidance_dated`. That unlocks real cited
figures in `POLICY_MODE=charter`. It does **not** unlock `verified`, because the charter is secondary
guidance and later CAR revisions are reported.

Do not infer force majeure from a generic weather trigger. The acquisition path for the primary CAR is
[`24-input-acquisition.md`](24-input-acquisition.md); encoded rules and open review questions are in
[`13-compensation-and-policy.md`](13-compensation-and-policy.md).

### D4 — email

Mailtrap/console during development; allowlisted Gmail/SMTP only for the demo if organisational policy
allows it. Credentials and recipients remain outside Git. All non-allowlisted bulk notifications are
simulated records.

### D5 — deployment

Local Docker. Stable, offline-capable, no cold starts mid-demo.

### D6 — scope

**Must build**

| Item | Notes |
| --- | --- |
| Bounded workflow system | Core: 1 orchestrator + 3 reasoning agents + 10 deterministic services |
| Dashboard | Ops controller surface |
| Timeline replay | Now cheap — `decision_log` already holds the chronology |
| Incident report | Executive summary output |
| RAG | Via SQL retrieval, per D2 |
| Memory | Incident outcomes feeding retrieval |
| Weather integration | Public live provider + committed fixture |
| Flight simulation | Local state machine |
| Notification system | Mailtrap → Gmail |

**Nice to have:** simulation engine, digital twin, voice, knowledge graph visualisation.

**Post-hackathon:** predictive ML, vendor negotiation, autonomous booking, real flight APIs, real-time
airport integration.

Scope is staged through the official checkpoints rather than compressed into one seven-day build. The
cut order and readiness gates are in [`14-hackathon-plan.md`](14-hackathon-plan.md) and
[`25-evaluation-readiness.md`](25-evaluation-readiness.md).

---

## Three design changes adopted

### 1. No chatbot UI

```
Signal → Event → Orchestrator → reasoning proposal/fallback → Assurance Gate → Service → Audit
```

There is no conversational surface. Already the position in
[`01-architecture.md`](01-architecture.md); now an explicit product constraint. The Golden Rule from
the Master Blueprint: *build an autonomous workflow engine, not a chatbot.*

### 2. Incident replay

Every action timestamped and replayable:

```
09:01  Weather alert
09:03  Delay predicted
09:04  Recovery generated
09:06  Passengers notified
09:08  Resolved
```

Judges reward this because it demonstrates observability, explainability and autonomous execution
simultaneously. It is also nearly free — `decision_log` in [`11-data-model.md`](11-data-model.md)
already captures exactly this.

### 3. Decision Assurance Gate, not confidence scores

**Superseded:** the original design surfaced an LLM-reported `confidence` percentage and gated execution
on it. Mentor review flagged that self-reported confidence is poorly calibrated; we agree and removed it
from the execution path.

```
Risk level      HIGH  (elevated · high · severe)
Basis           METAR VOBL, wind 32kt, historical BLR monsoon delay rate
Gate            needs_human
Blocking check  action_risk = high (cash compensation, 180 pax)
```

Execution is gated by six deterministic checks — evidence completeness, source freshness, entity
validation, policy compliance, conflict detection, action risk tier — computed in code from verifiable
facts. Full spec: [`18-decision-assurance-gate.md`](18-decision-assurance-gate.md).

The UI requirement stands and strengthens: **surface evidence, not a number.** Percentages appear only
where calibrated; elsewhere we show a risk level with its contributing factors.

---

## Phase 2 architecture decisions — **final**

Settled by the team at the close of Phase 1 planning review, binding on all four streams.

> **Label warning.** These were handed down as "D1, D2, D3". That collides with
> [D1 — airports](#d1--airports), [D2 — retrieval strategy](#d2--retrieval-strategy-skip-vectors-for-the-mvp)
> and [D3 — regulatory policy](#d3--regulatory-policy-architecture-settled-source-verification-outstanding)
> above, **and** with Stream D's dependency asks D1–D7 in their Phase 2 plan. They are recorded here as
> **P2-D1/2/3** so a reviewer can never resolve the wrong one. Cite the prefixed form.

### P2-D1 — plan-level assurance is **incident-group scoped**

A plan-level assurance summary spans the **disruption group**, not a single incident's plan. One
operator view covers the whole network event.

This does not weaken the invariant that **the gate authorises actions**. The summary aggregates per
action across every member incident; it is a reporting scope, not a new authorisation scope. Group
figures remain unions, never sums (see [A2 — cascading disruption](#a2--cascading-disruption-the-significant-change)).

### P2-D2 — what-if is **in scope**, bounded to zero-write deterministic re-evaluation

**This is the boundary, and it is the point of this entry.** What-if is:

- a **re-evaluation** of the *same recorded facts* through the *same deterministic* checks and services;
- **zero-write** — it persists nothing: no `assurance_evaluation`, no `action`, no state change, no
  `decision_log` row;
- **bounded** — it varies only declared inputs (candidate plan shape, policy cause), never world state.

It is **explicitly not a simulation engine and not a digital twin.** Those remain deferred
(*Nice to have*, above). Concretely, what-if must never:

- model or project future world state (weather, traffic, capacity) beyond what is recorded;
- emit a predicted delay, cost or outcome; or
- claim any figure not traceable to a stored fact.

The enforcement is structural, not a convention: the response contract carries
`basis: Literal["recorded_evidence"]`, so it **cannot express a projection**, and a test asserts row
counts are identical before and after. If a future request needs projected figures, that is a change
to this entry and to the deferred list — not an extension of what-if.

Why this is safe to include when the simulation engine is not: a re-evaluation adds **no new
subsystem**. It reuses the gate and the services that already exist, which is why
[`08-blueprint-backlog.md`](08-blueprint-backlog.md) could call replay "nearly free" and the twin
expensive. What-if under P2-D2 is on the cheap side of that line.

### P2-D3 — plan approval covers low/medium risk only, and never failed evidence

An operator may approve a plan, and that approval may authorise its **low and medium** risk actions.

- **High-risk actions always require their own action-level approval.** A plan approval never
  substitutes for one. This preserves the Phase 1 behaviour where the high-risk notification was held
  until a person approved that action.
- **Approval covers risk, never failed evidence.** A `FAIL` on any of the six checks —
  `evidence_complete`, `sources_fresh`, `entities_valid`, `policy_compliant`, `no_conflicts`,
  `action_risk` — is **not approvable at plan level, at any risk tier**. Fail-closed is not
  delegable: an operator may accept exposure, but may not assert a fact the evidence does not support.

Restated against [`18-decision-assurance-gate.md`](18-decision-assurance-gate.md), a plan approval may
satisfy `needs_human` **only** when the tier is low or medium **and** no check has failed. The gate's
rules are unchanged; P2-D3 narrows only *what a human's signature is allowed to stand in for*.

### Phase 2 cut order

**Open-Meteo / historical provider expansion is non-critical and is cut before any core Phase 2
feature.** Forecast retrieval (FR-2) and the historical archive described in
[`10-data-sources.md`](10-data-sources.md) are enhancements; the recorded METAR path already carries
the demo. If Phase 2 runs short, this goes first.

---

## Mentor review — resolutions

Five review comments on the submitted deck. The deck is fixed; these are resolved in the build and docs.

| # | Comment | Resolution | Doc |
| --- | --- | --- | --- |
| 1 | Ambitious, phase it, use simulators | Five phases, each ending at a demonstrable system; simulators behind provider interfaces by design | [`20-phased-delivery.md`](20-phased-delivery.md) |
| 2 | Why 9 rotations for 8 flights? | Crew are assigned to pairings, not flights — many-to-many, plus onward duties and positioning. Made traceable in the cascade view | [`22-crew-pairing-model.md`](22-crew-pairing-model.md) |
| 3 | "13 agents" is really 3 agents + tools | Retaxonomised: 1 orchestrator + 3 reasoning agents + 10 deterministic services | [`03-agent-design.md`](03-agent-design.md) |
| 4 | LLM self-reported confidence is unreliable | Removed from the contract; replaced by deterministic assurance. Model self-report may be logged as diagnostic metadata, not treated as calibration or ground truth | [`18-decision-assurance-gate.md`](18-decision-assurance-gate.md) |
| 5 | DGCA is India-specific — how does it scale? | Jurisdiction resolver → versioned policy packs → deterministic rules engine → cited explanation. RAG cites, never calculates | [`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md) |

## Open-source stack list

The tool spreadsheet supplied to the team is **optional reference material, not a mandatory checklist**.
We align where a tool earns its place: Llama 3.3 70B is our open-weight model, Chroma remains optional,
and Docling is selected for regulatory-source extraction. LangGraph, CrewAI, graph databases and SQLite
are declined with explicit engineering reasons—see [`23-stack-alignment.md`](23-stack-alignment.md).

## UI direction

The application UI is an **operations console**, not an AI landing page. Near-monochrome graphite, one
instrument-cyan accent, colour reserved for operational state, monospaced tabular numerals for all
operational data. **No purple, violet or indigo. No gradients, glows or glassmorphism** — that palette is
the default AI-demo look and reads as a template. Full token set and rules:
[`21-design-system.md`](21-design-system.md).

---

## Source artefacts

| Artefact | Status |
| --- | --- |
| `TravelOps_AI_Master_Blueprint.txt` | ✅ Recovered — [`reference/master-blueprint.md`](reference/master-blueprint.md) |
| `TravelOps_AI_Startup_Blueprint.docx` | ❌ Not recoverable — arrived as raw ZIP binary, mangled in transit |

The `.docx` is a compressed archive; pasting it as text corrupted the byte stream irrecoverably. Based
on the original conversation it was described as the *first, shorter* version of the blueprint, so the
recovered Master Blueprint plus the decisions above very likely supersede it. If anything specific from
it matters, paste that section as plain text.
