# TravelOps AI — Design Documentation

Architecture and design notes for an autonomous multi-agent system that detects travel disruptions and
executes recovery plans.

**Nothing here has been built.** These are design decisions and open questions, captured so the team
can work from a shared reference instead of re-deciding architecture ad hoc.

## Read in this order

| Doc | What it answers |
| --- | --- |
| [01 — System Architecture](01-architecture.md) | What the system is, and why the orchestrator is the brain rather than the LLM |
| [02 — Disruption Flow](02-disruption-flow.md) | A storm over Bengaluru, traced end to end through every stage |
| [03 — Agent Design](03-agent-design.md) | The four properties every agent gets, and the structured response contract |
| [04 — LLM Strategy (Groq)](04-llm-strategy-groq.md) | What to send the model, what never to send it, and how to survive its failures |
| [05 — Memory and Retrieval](05-memory-and-rag.md) | Why chat history isn't memory, and what incident memory looks like |
| [06 — AI vs Deterministic](06-ai-vs-deterministic.md) | The single most important boundary in the project |
| [07 — Risks and Mitigations](07-risks-and-mitigations.md) | Twelve ways this breaks, and what to do about each |
| [08 — Blueprint Backlog](08-blueprint-backlog.md) | What is still undesigned, and the order to tackle it |

## Requirements and data

| Doc | What it answers |
| --- | --- |
| [09 — Requirements](09-requirements.md) | Functional and non-functional requirements, scope, success criteria |
| [10 — Data Sources](10-data-sources.md) | Which free APIs and datasets actually work, with verdicts |
| [11 — Data Model](11-data-model.md) | Postgres schema, ER diagram, full DDL |
| [12 — Synthetic Data Plan](12-synthetic-data-plan.md) | What must be generated, in what volume, and how |

## Start here if you are picking this up

**[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)** — the decisions still needed, what has been assumed, and
what is blocked. Read it before treating any other document as settled.

## Reference

| Doc | Contents |
| --- | --- |
| [Source Conversation](reference/source-conversation.md) | Verbatim transcript the curated docs derive from, plus the AI-presentation-tool comparison |

## The three rules that everything else follows from

1. **The orchestrator is the brain, not the LLM.** The model is one node in a workflow, invoked only
   where reasoning is genuinely required.
2. **Structured output, never prose.** Every agent returns validated JSON. The orchestrator never
   parses English.
3. **If there is one provably correct answer, write code.** Compensation maths, filtering, and sorting
   never touch a model.
