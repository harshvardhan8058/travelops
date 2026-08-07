# 8. Blueprint Backlog — What Is Still Undocumented

The source conversation proposed expanding these notes into a 150–300 page engineering design
document. That document does not exist yet, and the items below have **not** been designed — they were
listed as scope, not answered.

This file is deliberately a checklist of open work rather than invented content. Filling any row with
plausible-sounding detail that nobody has actually decided would be worse than leaving it empty,
because the team would build against fiction.

## Status legend

- ✅ Covered by an existing doc in this repo
- 🟡 Partially covered — principles exist, specifics do not
- ⬜ Not started

## Backlog

| # | Item | Status | Where it lives / should live |
| --- | --- | :---: | --- |
| 1 | Complete system architecture | 🟡 | [`01-architecture.md`](01-architecture.md) — layers done, deployment topology not |
| 2 | Every microservice and API | ⬜ | Needs an OpenAPI-style spec per service |
| 3 | Database schema (ER diagrams + tables) | ⬜ | Only an incident record shape exists, in [`05-memory-and-rag.md`](05-memory-and-rag.md) |
| 4 | Multi-agent architecture (Planner, Executor, Recovery, Learning) | 🟡 | [`03-agent-design.md`](03-agent-design.md) — contract + roster done, Recovery/Learning agents undefined |
| 5 | RAG and knowledge graph design | 🟡 | [`05-memory-and-rag.md`](05-memory-and-rag.md) — RAG role clear, knowledge graph not designed |
| 6 | Event-driven workflow diagrams | 🟡 | [`02-disruption-flow.md`](02-disruption-flow.md) — one scenario only |
| 7 | Folder structure and coding standards | ⬜ | |
| 8 | Prompt engineering for every agent | ⬜ | Principles in [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md); no actual prompts written |
| 9 | Groq integration strategy | ✅ | [`04-llm-strategy-groq.md`](04-llm-strategy-groq.md) |
| 10 | Memory architecture | ✅ | [`05-memory-and-rag.md`](05-memory-and-rag.md) |
| 11 | Synthetic data generation scripts | ⬜ | |
| 12 | Free APIs and datasets | ⬜ | Weather / flight sources named but not selected or evaluated |
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

The dependency order matters more than the page count. Roughly:

1. **Decide the scope of the demo first** (#27, #28). Everything else is over-engineering until the
   demo scenario is fixed.
2. **Data model** (#3) and **free API/dataset selection** (#12). These constrain every downstream
   choice, and #12 in particular can invalidate the plan if no suitable free source exists.
3. **Folder structure and service boundaries** (#7, #2).
4. **Prompts** (#8) — one per LLM node, versioned as files.
5. **Synthetic data** (#11), because the demo cannot depend on live API availability.
6. **UI and dashboard** (#13, #14) — last, and only as much as the demo shows.

Items #15–#17 (replay engine, digital twin, simulation engine) are genuinely interesting but are
product-scale features. They should be treated as post-hackathon roadmap (#30) unless one of them
*is* the demo.

## Missing source artefacts

Two files were generated during the original conversation and are not in this repo:

- `TravelOps_AI_Master_Blueprint.txt`
- `TravelOps_AI_Startup_Blueprint.docx`

Their contents could not be recovered from the transcript. If you still have them, drop them into
`docs/reference/` and reconcile anything they contain against the docs here.
