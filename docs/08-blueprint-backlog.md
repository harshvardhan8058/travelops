# 8. Blueprint Backlog — What Is Still Undocumented

The source conversation proposed expanding these notes into a 150–300 page engineering design
document. Items below marked ⬜ have **not** been designed — they were listed as scope, not answered.

This file is deliberately a checklist of open work rather than invented content. Filling any row with
plausible-sounding detail that nobody has actually decided would be worse than leaving it empty,
because the team would build against fiction.

**Progress:** items #3, #4, #7, #11, #12, #18, #21, #25, #27 and #28 are resolved, plus a requirements
specification ([`09-requirements.md`](09-requirements.md)) and a DGCA policy document
([`13-compensation-and-policy.md`](13-compensation-and-policy.md)) that were not on the original list
but should have been.

All decisions are recorded in [`DECISIONS.md`](DECISIONS.md). What genuinely remains open is in
[`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) — notably the "Use of Internal Tools" judging criterion, which
is roughly a sixth of the score and cannot be designed for yet.

**The design phase is complete enough to start building.** Remaining ⬜ items are either deliberately
deferred (digital twin, simulation engine, CI/CD) or genuinely not worth doing for a 7-day local demo.

## Status legend

- ✅ Covered by an existing doc in this repo
- 🟡 Partially covered — principles exist, specifics do not
- ⬜ Not started

## Backlog

| # | Item | Status | Where it lives / should live |
| --- | --- | :---: | --- |
| 1 | Complete system architecture | 🟡 | [`01-architecture.md`](01-architecture.md) — layers done, deployment topology not |
| 2 | Every microservice and API | ⬜ | Needs an OpenAPI-style spec per service; blocked on stack choice (B5) |
| 3 | Database schema (ER diagrams + tables) | ✅ | [`11-data-model.md`](11-data-model.md) — full DDL + ER diagram |
| 4 | Multi-agent architecture (Planner, Executor, Recovery, Learning) | ✅ | [`03-agent-design.md`](03-agent-design.md) — full 13-agent roster; only 3 use the LLM |
| 5 | RAG and knowledge graph design | 🟡 | [`05-memory-and-rag.md`](05-memory-and-rag.md) — RAG via SQL retrieval (D2); knowledge graph deferred to nice-to-have |
| 6 | Event-driven workflow diagrams | 🟡 | [`02-disruption-flow.md`](02-disruption-flow.md) — one scenario only |
| 7 | Folder structure and coding standards | ✅ | [`16-folder-structure.md`](16-folder-structure.md) |
| 8 | Prompt engineering for every agent | ⬜ | Principles in [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md); no actual prompts written |
| 9 | Groq integration strategy | ✅ | [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md) |
| 10 | Memory architecture | ✅ | [`05-memory-and-rag.md`](05-memory-and-rag.md) |
| 11 | Synthetic data generation scripts | ✅ | [`12-synthetic-data-plan.md`](12-synthetic-data-plan.md) — volumes, approach, seed scenario |
| 12 | Free APIs and datasets | ✅ | [`10-data-sources.md`](10-data-sources.md) — evaluated with verdicts |
| 13 | UI/UX wireframes for every screen | ⬜ | |
| 14 | Dashboard design | ⬜ | |
| 15 | Timeline replay engine | 🟡 | **Must-build.** Backed by `decision_log`; UI is Day 5 work |
| 16 | Digital Twin architecture | ⬜ | Nice-to-have; deferred by D6 |
| 17 | Simulation engine | ⬜ | Nice-to-have; deferred by D6 |
| 18 | Notification system | ✅ | Mailtrap dev / Gmail demo per D4; channel interface in [`16-folder-structure.md`](16-folder-structure.md) |
| 19 | Authentication and RBAC | ⬜ | |
| 20 | Logging and observability | 🟡 | Explainability requirement in [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md); no log schema |
| 21 | Deployment architecture | ✅ | Local Docker per D5; compose layout in [`16-folder-structure.md`](16-folder-structure.md) |
| 22 | CI/CD pipeline | ⬜ | Not worth building for a 7-day local-only sprint |
| 23 | Testing strategy | ✅ | Prioritised test plan in [`16-folder-structure.md`](16-folder-structure.md) — test the deterministic half, not LLM output |
| 24 | Security considerations | 🟡 | LLM boundary in [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md); no threat model |
| 25 | Risk register | ✅ | [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md) |
| 26 | Cost optimisation (₹0–₹500 budget) | 🟡 | LLM cost strategy only; no infra cost plan |
| 27 | 7-day hackathon execution plan | ✅ | [`14-hackathon-plan.md`](14-hackathon-plan.md) — day-by-day with gates and a cut list |
| 28 | Demo script | ✅ | [`15-demo-script.md`](15-demo-script.md) — 7-minute script with Q&A prep |
| 29 | Hackathon presentation strategy | 🟡 | Positioning + criteria mapping in [`DECISIONS.md`](DECISIONS.md) and [`14-hackathon-plan.md`](14-hackathon-plan.md); PPT is M4's Day 1–6 task |
| 30 | Post-hackathon roadmap to production | ⬜ | |

## Suggested order of attack

The dependency order matters more than the page count.

**Done:**

- ~~Free API/dataset selection (#12)~~ — this was the item that could have invalidated the whole plan.
  It did change it: no free hotel or live flight-status source exists, so both are simulated.
- ~~Data model (#3)~~ and ~~synthetic data plan (#11)~~ followed once the sources were known.

- ~~Blocking questions~~ — answered; see [`DECISIONS.md`](DECISIONS.md).
- ~~Demo scope and script (#27, #28)~~ — [`14-hackathon-plan.md`](14-hackathon-plan.md),
  [`15-demo-script.md`](15-demo-script.md).
- ~~Folder structure (#7)~~ — [`16-folder-structure.md`](16-folder-structure.md).

**Next: stop designing and start building.** Follow the day-by-day plan. The only design work left
inside the sprint:

1. **Prompts** (#8) — one per LLM node, versioned as files. Day 3 work, written alongside the planner.
2. **API contract** (#2) — frozen on Day 1 so the frontend can build against stubs. A formal OpenAPI
   document is generated by FastAPI rather than hand-written.
3. **UI screens** (#13, #14) — designed in the browser, not on paper. Only as much as the demo shows.

Items #15–#17 (replay engine, digital twin, simulation engine) are genuinely interesting but are
product-scale features. They should be treated as post-hackathon roadmap (#30) unless one of them
*is* the demo.

One revision to that judgement: **the replay engine (#15) is now nearly free.** The `decision_log`
table in [`11-data-model.md`](11-data-model.md) already captures the full chronology, so replay becomes
a read over existing data rather than a subsystem. It is worth keeping. The digital twin and simulation
engine are not.

## Missing source artefacts

Two files were generated during the original conversation and are not in this repo:

- `TravelOps_AI_Master_Blueprint.txt`
- `TravelOps_AI_Startup_Blueprint.docx`

Their contents could not be recovered from the transcript. If you still have them, drop them into
`docs/reference/` and reconcile anything they contain against the docs here.
