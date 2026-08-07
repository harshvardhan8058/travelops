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
| **Idea submission** | 7 August 2026 |
| **Final submission** | ~14 August 2026 |
| **Working time** | Treat as a 7-day engineering sprint |

### Team

| Member | Responsibility |
| --- | --- |
| Harsh | AI, backend, orchestration, architecture |
| Member 2 | Frontend dashboard |
| Member 3 | APIs, database, integrations |
| Member 4 | Testing, PPT, demo, documentation |

See [`14-hackathon-plan.md`](14-hackathon-plan.md) for the day-by-day plan.

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
| B1 | Hackathon and timeline | TechCon 2026 (Coforge); ~14 Aug; 7-day sprint; 4 members |
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

### D3 — DGCA rules: researched, not invented

✅ Complete — [`13-compensation-and-policy.md`](13-compensation-and-policy.md).

Headline finding: **weather is force majeure, so no cash compensation is owed — but duty of care still
applies.** The original transcript's example computed cash for a storm delay, which is wrong under the
real regulation.

### D4 — email

Mailtrap during development; real Gmail only for the demo. Replaces the earlier Brevo recommendation
for the dev path — Mailtrap is the better choice because it captures mail without delivering it, so
600 synthetic passengers cannot generate real sends by accident.

### D5 — deployment

Local Docker. Stable, offline-capable, no cold starts mid-demo.

### D6 — scope

**Must build**

| Item | Notes |
| --- | --- |
| Multi-agent system | Core |
| Dashboard | Ops controller surface |
| Timeline replay | Now cheap — `decision_log` already holds the chronology |
| Incident report | Executive summary output |
| RAG | Via SQL retrieval, per D2 |
| Memory | Incident outcomes feeding retrieval |
| Weather integration | Real, live |
| Flight simulation | Local state machine |
| Notification system | Mailtrap → Gmail |

**Nice to have:** simulation engine, digital twin, voice, knowledge graph visualisation.

**Post-hackathon:** predictive ML, vendor negotiation, autonomous booking, real flight APIs, real-time
airport integration.

⚠️ This must-build list is ambitious for 7 days with 4 people, especially alongside cascading. A
prioritised cut list is in [`14-hackathon-plan.md`](14-hackathon-plan.md).

---

## Three design changes adopted

### 1. No chatbot UI

```
Event  →  Planner  →  Workflow  →  Agents  →  Execution
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

### 3. Confidence scores on every decision

```
Prediction  94%
Reason      Heavy rainfall
Confidence  High
Evidence    METAR, historical, wind speed
```

The `confidence` field already exists in the agent response contract
([`03-agent-design.md`](03-agent-design.md)) and in the `action` and `prediction` tables. What is new
is the requirement to **surface evidence in the UI**, not merely store a number — a bare percentage
invites the question "based on what?"

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
