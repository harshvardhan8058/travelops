# 14. Seven-Day Sprint Plan

Resolves backlog item #27. Submission ~**14 August 2026**; idea submission was 7 August.

Four people, seven days, and a must-build list from [`DECISIONS.md`](DECISIONS.md) that is genuinely
ambitious. This plan is built around one principle:

> **A working end-to-end slice by Day 4 matters more than any individual feature.**

A demo that does one thing completely beats a demo that does nine things partially. Every day has a
**gate** — a specific, checkable outcome. Miss a gate and you cut scope, not quality.

## Candid scope assessment

Nine must-build items plus cascading disruption plus ten agents in seven days with four people is
aggressive. It is achievable **only** because most of it is deterministic code rather than research,
and because the design work is already done.

The two things most likely to sink it:

1. **Integrating everything on Day 6.** Integrate daily instead. A broken integration found on Day 2
   costs an hour; found on Day 6 it costs the demo.
2. **Burning the Groq token budget during development.** ~100K tokens/day is roughly 25–50 planner
   calls. Build the fixture/cache path on Day 1, before you need it.

---

## Day 1 — Friday 8 August: foundations

Nobody builds features today. Today is about a skeleton everyone can work against in parallel.

| Who | Task |
| --- | --- |
| **Harsh** | Repo scaffold; agent base class + response contract; Redis Streams event bus; orchestrator skeleton with recursion/iteration/timeout caps |
| **M3** | `docker-compose` (Postgres + Redis); schema migrations from [`11-data-model.md`](11-data-model.md); load OurAirports for the 10 airports + runways |
| **M2** | React + Vite + TS + Tailwind + shadcn scaffold; dashboard shell; typed API client |
| **M4** | pytest harness; `.env.example`; run instructions; **LLM fixture/replay mode**; PPT skeleton |

**Freeze the API contract today.** Agree request/response shapes so M2 can build against stubs while
the backend is still hollow. Contract drift discovered on Day 5 is the most expensive bug available.

> **Gate:** `docker compose up` yields a running API, database and UI shell. One seeded flight renders
> in the dashboard.

---

## Day 2 — Saturday 9 August: real data in, prediction out

| Who | Task |
| --- | --- |
| **Harsh** | Prediction Agent: rules engine over wind, visibility, ceiling, **crosswind vs runway heading**; risk scoring; `HIGH_RISK_DELAY` emission; dedup via the partial unique index |
| **M3** | `aviationweather.gov` METAR poller for the 10 airports; Open-Meteo 30-day backfill; AIKosh schedule load |
| **M2** | Live flight board + weather panel wired to real data |
| **M4** | Synthetic generators: 600+ passengers, bookings/segments tuned to 22 at-risk connections, 11 capacity-short hotels near BLR, crew, transport |

Crosswind is worth the extra hour: it is the difference between a rule that looks like a demo and one
an operations person would recognise as real.

> **Gate:** genuine live METAR for BLR visible in the UI. Injecting the storm fixture produces exactly
> one risk event, not one per poll.

**Timebox the AIKosh load to 3 hours.** The format is unverified. If it fights back, hand-build
schedules for 10 airports and move on — schedules are not the interesting part.

---

## Day 3 — Sunday 10 August: planner and memory

| Who | Task |
| --- | --- |
| **Harsh** | Planner Agent on Groq; structured JSON + schema validation; reject-and-retry; **deterministic fallback playbook**; prompt v1 as a versioned file |
| **M3** | SQL precedent retrieval (airport, trigger, severity, weather, flight type); compensation calculator with force-majeure logic from [`13-compensation-and-policy.md`](13-compensation-and-policy.md) |
| **M2** | Incident detail view: plan, retrieved precedent, confidence + evidence |
| **M4** | ~150 historical incidents, 70/20/10 success/partial/fail, including one planted BLR-storm precedent |

Build the fallback playbook **the same day** as the Groq path, not later. It is a demo asset (see
[`15-demo-script.md`](15-demo-script.md)), and writing it while the planner is fresh takes half the
time.

> **Gate:** storm → validated JSON plan, with the retrieved precedent visible on screen. Disable Groq
> and the fallback still produces a plan.

---

## Day 4 — Monday 11 August: end to end ⚠️ **no-slip gate**

The single most important day.

| Who | Task |
| --- | --- |
| **Harsh** | Orchestrator wiring; parallel execution of independent tasks; Hotel, Connection and Communication agents |
| **M3** | Mailtrap notification dispatch; hotel reservation writes with idempotency keys; gate reassignment |
| **M2** | Live action feed; cost panel; decision log view |
| **M4** | Full end-to-end test of `bengaluru_storm`, single flight |

> **Gate:** **a single-flight recovery completes end to end** — weather → prediction → plan → hotels
> reserved → connections flagged → notifications dispatched → incident resolved → decision log
> populated.

**If this gate is missed, cut cascading immediately** and ship the single-flight demo well. A polished
single-flight recovery scores better on Feasibility and Demo than a half-working cascade.

---

## Day 5 — Tuesday 12 August: cascading, replay, reports

| Who | Task |
| --- | --- |
| **Harsh** | `incident_group` cascade orchestration (8 flights, 600 passengers); crew + transport coordination; confidence surfacing |
| **M3** | Executive report generation via Groq; analytics aggregates |
| **M2** | **Timeline replay UI** |
| **M4** | Rehearsal #1; record a backup demo video |

Record the backup video today, not on Day 7. A recording made while things work is insurance against
everything that can go wrong on stage.

> **Gate:** cascade runs across 8 flights; timeline replay scrubs through a completed incident.

---

## Day 6 — Wednesday 13 August: feature freeze

**No new features. Bug fixes only.** This is not negotiable — it is the difference between a demo that
works and a demo that worked yesterday.

| Who | Task |
| --- | --- |
| **Harsh** | Kill-Groq fallback verification; loop-cap and timeout testing; reproducibility (run the scenario 3× and diff) |
| **M3** | Freeze the dataset as a committed SQL dump; verify cold-start from `docker compose up` |
| **M2** | Visual polish; empty and error states |
| **M4** | Two full rehearsals; PPT final; re-record backup video |

> **Gate:** clean-machine cold start works. Scenario runs 3× with materially identical output.

---

## Day 7 — Thursday 14 August: submit

| Time | Task |
| --- | --- |
| Morning | Final rehearsal; verify the submission package |
| Midday | **Submit** |
| Afternoon | Buffer for submission-portal problems |

Submit early. Never at the deadline.

---

## Cut list — in order

When you fall behind, cut from the top. Decide by looking at this list, not by arguing.

| # | Cut | Notes |
| --- | --- | --- |
| 1 | Voice, digital twin, knowledge graph viz, simulation engine | Already nice-to-have; drop without discussion |
| 2 | Chroma + BGE embeddings | D2 already defers these |
| 3 | Passenger portal, support and executive surfaces | Ops controller only |
| 4 | Crew duty-time legality | Display crew changes; never validate legality |
| 5 | Ground transport agent | Fold transfers into the Hotel Agent as a cost line |
| 6 | Gate reassignment | Lowest demo value of the execution agents |
| 7 | Cascading → single flight | Only if the Day 4 gate is missed |

### Never cut

- **Real weather integration.** Live data is the credibility anchor.
- **Planner + deterministic fallback.** The fallback is a scoring moment, not a safety net.
- **Notifications.** Something must visibly happen in the world.
- **Decision log.** Without it there is no explainability, and it powers replay for free.
- **Timeline replay.** High judge value, and nearly free given the decision log.

---

## Sprint risks

| Risk | Mitigation |
| --- | --- |
| Day 4 gate slips | Cut cascading same day; do not negotiate |
| Groq tokens exhausted mid-development | Fixture/replay mode built Day 1; cache aggressively |
| AIKosh data format fights back | 3-hour timebox, then hand-build 10 airports of schedules |
| Frontend/backend contract drift | Contract frozen Day 1; M2 works against stubs |
| Big-bang integration | Integrate daily; `main` must always run |
| Demo machine fails | Backup video recorded Day 5, re-recorded Day 6 |
| Dataset changes late and breaks the scenario | Dataset frozen as a committed dump on Day 6 |

## Mapping to judging criteria

| Criterion | Where it is earned |
| --- | --- |
| Creativity | Cascading recovery; force-majeure-aware compensation; replay |
| Feasibility | Working end-to-end demo; real weather; deterministic fallback |
| Relevance | Genuine DGCA rules; Indian airports; real operational problem |
| Use of Internal Tools | ⚠️ Unresolved — see [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) |
| Use of Open Source | FastAPI, React, Postgres, Redis, OurAirports, Open-Meteo, BGE |
| Engineering the Autonomous Enterprise | Event-driven orchestration; no chatbot; agents that decide and execute |

Five of six are covered by this plan. **"Use of Internal Tools" is not**, and cannot be until we know
what Coforge's internal tools are. That is potentially a sixth of the score.
