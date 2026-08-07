# TravelOps AI

**An autonomous operating layer for airline disruption recovery.**

When a storm disrupts an airport, TravelOps AI detects it, predicts the impact, plans a recovery,
executes the deterministic parts autonomously, and records why every decision was made — with a hard
boundary between what the AI decides and what code decides.

| | |
| --- | --- |
| **Team** | SkyForge AI (Registration ID 201) |
| **Project / Use Case** | TravelOps AI |
| **Industry** | Travel Transport Hospitality (TTH) → Airlines Operations |
| **Event** | TechCon 2026 Hackathon (Coforge) |
| **Theme** | Engineering the Autonomous Enterprise |

> **Status: design documentation only.** No application code yet. The design is complete enough to
> build from — see the [sprint plan](docs/14-hackathon-plan.md).

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

The LLM (Groq) is one component inside this, not the centre of it. **Only 3 of 13 agents use it.**

## What it does

A single weather event cascades:

```
Storm at BLR → 8 flights → 600 passengers → 22 connections → 11 hotels → 9 crew rotations → report
```

...and the system handles the recovery, then explains itself.

## Documentation

Start at **[`docs/`](docs/)**. If you read three things: [DECISIONS](docs/DECISIONS.md),
the [sprint plan](docs/14-hackathon-plan.md), and the [demo script](docs/15-demo-script.md).

| | |
| --- | --- |
| [DECISIONS](docs/DECISIONS.md) | Every settled decision, with reasoning |
| [01 — Architecture](docs/01-architecture.md) | System shape and layers |
| [02 — Disruption Flow](docs/02-disruption-flow.md) | End-to-end worked example |
| [03 — Agent Design](docs/03-agent-design.md) | Agent contract and roster |
| [04 — LLM Strategy](docs/04-llm-strategy-groq.md) | Where Groq belongs, and where it doesn't |
| [05 — Memory](docs/05-memory-and-rag.md) | Incident memory and retrieval |
| [06 — AI vs Deterministic](docs/06-ai-vs-deterministic.md) | The central boundary |
| [07 — Risks](docs/07-risks-and-mitigations.md) | Twelve failure modes |
| [08 — Backlog](docs/08-blueprint-backlog.md) | What's deferred, and why |
| [09 — Requirements](docs/09-requirements.md) | FR/NFR and scope |
| [10 — Data Sources](docs/10-data-sources.md) | Free APIs and datasets, evaluated |
| [11 — Data Model](docs/11-data-model.md) | Postgres schema and DDL |
| [12 — Synthetic Data](docs/12-synthetic-data-plan.md) | What must be generated |
| [13 — DGCA Policy](docs/13-compensation-and-policy.md) | Real compensation rules |
| [14 — Sprint Plan](docs/14-hackathon-plan.md) | Seven days, four people |
| [15 — Demo Script](docs/15-demo-script.md) | The 7-minute narrative |
| [16 — Folder Structure](docs/16-folder-structure.md) | Layout and standards |
| [17 — Presentation Prompt](docs/17-presentation-prompt.md) | Gamma prompt for the 3-slide submission |
| [Open Questions](docs/OPEN-QUESTIONS.md) | Still unresolved |

## Stack

| Concern | Choice |
| --- | --- |
| Backend | FastAPI |
| Frontend | React, TypeScript, Tailwind, shadcn/ui |
| Database | Postgres |
| Events | Redis Streams |
| Reasoning LLM | Groq (`llama-3.3-70b-versatile`) |
| Prediction | Rules engine — deliberately not an LLM |
| Retrieval | SQL precedent matching; vectors deferred |
| Weather | aviationweather.gov METAR/TAF (real, live) |
| Flight status | Simulated — no usable free feed exists |
| Deployment | Docker, local |
| Budget | ₹0 – ₹500 |

Deliberately rejected as overkill: Kubernetes, Kafka, RabbitMQ.

## The five rules

1. The orchestrator is the brain, not the LLM.
2. Structured output, never prose.
3. If there is one provably correct answer, write code.
4. Build a workflow engine, not a chatbot.
5. The system must survive its own AI failing.
