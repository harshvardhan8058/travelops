# TravelOps AI — Design Documentation

**An autonomous operating layer for airline disruption recovery.**
Built by **Team SkyForge AI** · Registration ID 201 · TechCon 2026 (Coforge)

Design documentation for a multi-agent system that detects travel disruptions, plans recovery, executes
it, and explains every decision.

> **Naming:** *SkyForge AI* is the team. *TravelOps AI* is the project. See
> [`DECISIONS.md`](DECISIONS.md) for the canonical registration details.

**No application code exists yet.** The design phase is complete enough to start building — see
[`14-hackathon-plan.md`](14-hackathon-plan.md).

## Start here

| Doc | Why |
| --- | --- |
| **[DECISIONS](DECISIONS.md)** | Every settled decision, with reasoning. Read before changing anything |
| **[Sprint plan](14-hackathon-plan.md)** | Day-by-day plan to ~14 August, with gates and a cut list |
| **[Demo script](15-demo-script.md)** | The 7-minute narrative the build serves |
| **[Open questions](OPEN-QUESTIONS.md)** | What's still unresolved — one item costs real score |

## Architecture

| Doc | What it answers |
| --- | --- |
| [01 — System Architecture](01-architecture.md) | Why the orchestrator is the brain rather than the LLM |
| [02 — Disruption Flow](02-disruption-flow.md) | A storm over Bengaluru, traced end to end |
| [03 — Agent Design](03-agent-design.md) | The agent contract, and the 3 agents + 10 services roster |
| [18 — Decision Assurance Gate](18-decision-assurance-gate.md) | Six deterministic checks that replace confidence scores |
| [19 — Jurisdiction and Policy Packs](19-jurisdiction-and-policy-packs.md) | How regulatory intelligence scales past India |
| [22 — Crew Pairing Model](22-crew-pairing-model.md) | Why 8 delayed flights disrupt 9 rotations |
| [04 — LLM Strategy (Groq)](04-llm-strategy-groq.md) | What to send the model, and how to survive its failure |
| [05 — Memory and Retrieval](05-memory-and-rag.md) | Why chat history isn't memory |
| [06 — AI vs Deterministic](06-ai-vs-deterministic.md) | The most important boundary in the project |
| [07 — Risks and Mitigations](07-risks-and-mitigations.md) | Twelve failure modes, each with a mitigation |

## Requirements and data

| Doc | What it answers |
| --- | --- |
| [09 — Requirements](09-requirements.md) | FR/NFR, scope, success criteria |
| [10 — Data Sources](10-data-sources.md) | Which free APIs and datasets actually work |
| [11 — Data Model](11-data-model.md) | Postgres schema, ER diagram, full DDL |
| [12 — Synthetic Data Plan](12-synthetic-data-plan.md) | What must be generated, and how |
| [13 — Compensation and Policy](13-compensation-and-policy.md) | Real DGCA rules, researched not invented |

## Build

| Doc | What it answers |
| --- | --- |
| [20 — Phased Delivery](20-phased-delivery.md) | Five phases, each a demonstrable system, with a cut list |
| [21 — Design System](21-design-system.md) | The UI direction. Read before writing a component |
| [23 — Stack Alignment](23-stack-alignment.md) | Our stack vs the Coforge open-source list |
| [14 — Sprint Plan](14-hackathon-plan.md) | Seven days, four people, daily gates |
| [15 — Demo Script](15-demo-script.md) | 7-minute script, timings, Q&A prep |
| [16 — Folder Structure](16-folder-structure.md) | Layout, coding standards, `.env` |
| [17 — Presentation Prompt](17-presentation-prompt.md) | Gamma prompt for the submitted 3-slide deck — **frozen** |
| [08 — Backlog](08-blueprint-backlog.md) | What remains undesigned, and what was deliberately deferred |

## Reference

| Doc | Contents |
| --- | --- |
| [Master Blueprint](reference/master-blueprint.md) | Recovered original blueprint, plus deliberate divergences |
| [Source Conversation](reference/source-conversation.md) | Original transcript that started this |

## The rules everything else follows from

1. **The orchestrator is the brain, not the LLM.** 1 orchestrator, 3 reasoning agents, 10 deterministic
   services. Only 3 components touch a model, and none of them can execute.
2. **Structured output, never prose.** Every agent returns validated JSON.
3. **If there is one provably correct answer, write code.** Compensation, filtering and sorting never
   touch a model.
4. **Build a workflow engine, not a chatbot.** There is no conversational surface.
5. **The system must survive its own AI failing.** `LLM_MODE=off` still completes a recovery.
6. **Execution is gated deterministically.** Six verifiable checks, never a self-reported confidence
   score.
7. **No purple.** The UI is an operations console — graphite, one accent, colour means state.
