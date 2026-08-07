# 8. Blueprint Backlog — What Is Still Undocumented

The source conversation proposed expanding these notes into a 150–300 page engineering design
document. Items below marked ⬜ have **not** been designed — they were listed as scope, not answered.

This file is deliberately a checklist of open work rather than invented content. Filling any row with
plausible-sounding detail that nobody has actually decided would be worse than leaving it empty,
because the team would build against fiction.

**Progress since first draft:** items #3, #11 and #12 are now resolved, plus a requirements
specification ([`09-requirements.md`](09-requirements.md)) that was not on the original list but should
have been. Most remaining items are blocked on decisions rather than on effort — see
[`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

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
| 4 | Multi-agent architecture (Planner, Executor, Recovery, Learning) | 🟡 | [`03-agent-design.md`](03-agent-design.md) — contract + roster done, Recovery/Learning agents undefined |
| 5 | RAG and knowledge graph design | 🟡 | [`05-memory-and-rag.md`](05-memory-and-rag.md) — RAG role clear, knowledge graph not designed |
| 6 | Event-driven workflow diagrams | 🟡 | [`02-disruption-flow.md`](02-disruption-flow.md) — one scenario only |
| 7 | Folder structure and coding standards | ⬜ | |
| 8 | Prompt engineering for every agent | ⬜ | Principles in [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md); no actual prompts written |
| 9 | Groq integration strategy | ✅ | [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md) |
| 10 | Memory architecture | ✅ | [`05-memory-and-rag.md`](05-memory-and-rag.md) |
| 11 | Synthetic data generation scripts | ✅ | [`12-synthetic-data-plan.md`](12-synthetic-data-plan.md) — volumes, approach, seed scenario |
| 12 | Free APIs and datasets | ✅ | [`10-data-sources.md`](10-data-sources.md) — evaluated with verdicts |
| 13 | UI/UX wireframes for every screen | ⬜ | |
| 14 | Dashboard design | ⬜ | |
| 15 | Timeline replay engine | ⬜ | |
| 16 | Digital Twin architecture | ⬜ | |
| 17 | Simulation engine | ⬜ | |
| 18 | Notification system | 🟡 | Channels named in [`02-disruption-flow.md`](02-disruption-flow.md); no provider or delivery design |
| 19 | Authentication and RBAC | ⬜ | |
| 20 | Logging and observability | 🟡 | Explainability requirement in [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md); no log schema |
| 21 | Deployment architecture | ⬜ | |
| 22 | CI/CD pipeline | ⬜ | |
| 23 | Testing strategy | 🟡 | Testability rationale in [`06-ai-vs-deterministic.md`](06-ai-vs-deterministic.md); no test plan |
| 24 | Security considerations | 🟡 | LLM boundary in [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md); no threat model |
| 25 | Risk register | ✅ | [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md) |
| 26 | Cost optimisation (₹0–₹500 budget) | 🟡 | LLM cost strategy only; no infra cost plan |
| 27 | 7-day hackathon execution plan | ⬜ | |
| 28 | Demo script | ⬜ | Reproducibility constraints noted in [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md) |
| 29 | Hackathon presentation strategy | ⬜ | Tooling note in [`reference/source-conversation.md`](reference/source-conversation.md) |
| 30 | Post-hackathon roadmap to production | ⬜ | |

## Suggested order of attack

The dependency order matters more than the page count.

**Done:**

- ~~Free API/dataset selection (#12)~~ — this was the item that could have invalidated the whole plan.
  It did change it: no free hotel or live flight-status source exists, so both are simulated.
- ~~Data model (#3)~~ and ~~synthetic data plan (#11)~~ followed once the sources were known.

**Next, in order:**

1. **Answer the blocking questions** in [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md). Hackathon dates,
   judging criteria, and ops-vs-passenger scope gate everything below.
2. **Demo scope and script** (#27, #28). Still first among build tasks — everything else is
   over-engineering until the demo is fixed. Requires B1 and B2 answered.
3. **Folder structure and service boundaries** (#7, #2). Requires the stack decision (B5).
4. **Prompts** (#8) — one per LLM node, versioned as files.
5. **UI and dashboard** (#13, #14) — last, and only as much as the demo shows.

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
