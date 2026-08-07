# TravelOps AI

An autonomous multi-agent system for travel disruption recovery: detect a disruption, predict its
impact, plan a recovery, and execute it — with a hard boundary between what the AI decides and what
deterministic code decides.

> **Status: design documentation only.** No application code has been written yet, and none should be
> inferred from these documents. Several areas are explicitly still undesigned — see the
> [blueprint backlog](docs/08-blueprint-backlog.md).

## The idea in one diagram

```
                 TravelOps Orchestrator
                          │
        ┌─────────────────┼──────────────────┐
        │                 │                  │
   Prediction         Planning         Communication
     Agent             Agent               Agent
        │                 │                  │
        ├──────────── Execution Layer ───────┤
        │        │            │              │
   Flight API  Weather     Hotel DB     Notification
        │        │            │              │
        └──────────────── Memory ────────────┘
```

The LLM (Groq) is one component inside this, not the centre of it.

## Documentation

Start at **[`docs/`](docs/)**.

| | |
| --- | --- |
| [01 — System Architecture](docs/01-architecture.md) | What the system is and how it's layered |
| [02 — Disruption Flow](docs/02-disruption-flow.md) | End-to-end worked example |
| [03 — Agent Design](docs/03-agent-design.md) | Agent contract and response schema |
| [04 — LLM Strategy (Groq)](docs/04-llm-strategy-groq.md) | Where the model belongs, and where it doesn't |
| [05 — Memory and Retrieval](docs/05-memory-and-rag.md) | Incident memory and RAG |
| [06 — AI vs Deterministic](docs/06-ai-vs-deterministic.md) | The central design boundary |
| [07 — Risks and Mitigations](docs/07-risks-and-mitigations.md) | Twelve failure modes |
| [08 — Blueprint Backlog](docs/08-blueprint-backlog.md) | Open design work, prioritised |
| [Source Conversation](docs/reference/source-conversation.md) | Original transcript, preserved |

## Tech direction

| Concern | Choice |
| --- | --- |
| Reasoning / planning LLM | Groq |
| Prediction | ML model or rule engine — deliberately not an LLM |
| Agent communication | Events, not direct calls |
| Agent output | Validated JSON, never free-form text |
| Planning temperature | 0 – 0.2, for reproducible demos |
| Budget target | ₹0 – ₹500 |
