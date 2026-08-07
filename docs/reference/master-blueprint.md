# Reference: TravelOps AI Master Blueprint

Recovered content of `TravelOps_AI_Master_Blueprint.txt`, one of the two artefacts generated during the
[original conversation](source-conversation.md). Preserved as the provenance record.

The companion `TravelOps_AI_Startup_Blueprint.docx` could **not** be recovered — it is a compressed
ZIP archive and the byte stream was corrupted when pasted as text.

Where this blueprint and the curated docs disagree, [`../DECISIONS.md`](../DECISIONS.md) is
authoritative — it reflects later decisions.

---

## Vision

Build an Autonomous Travel Operations Platform that predicts, plans, coordinates, executes, and learns
from travel disruptions.

## Core principles

- Event-driven architecture
- Multi-agent orchestration
- Deterministic execution where possible
- LLM only for reasoning
- Explainable AI
- Human approval when needed
- Continuous learning

## Recommended tech stack

**Frontend:** React + TypeScript, Tailwind CSS, shadcn/ui, Framer Motion

**Backend:** FastAPI, PostgreSQL, Redis, Qdrant/ChromaDB, Docker

**AI:** Groq API (planner, reasoning, report generation), embedding model (BGE), RAG, LangGraph or a
custom workflow engine

**Data:** OpenSky, Open-Meteo, OurAirports, OpenFlights, OpenStreetMap, Kaggle flight delay datasets,
synthetic passenger/crew/hotel/vendor data

## Data model

Entities: Flights, Passengers, Crew, Airports, Weather, Hotels, Ground Transport, Vendors, Incidents,
Policies, Notifications.

## Agent architecture

```
Orchestrator
├ Prediction Agent
├ Planning Agent
├ Flight Recovery Agent
├ Hotel Agent
├ Transport Agent
├ Communication Agent
├ Finance Agent
├ Analytics Agent
├ Learning Agent
```

Each agent has: Goal, Tools, Memory, Constraints.

## Event flow

```
Weather API → Prediction → Risk Event → Planner (Groq) → Task List →
Execution Agents → Notifications → Incident Report → Memory
```

## What uses AI

- Understand disruptions
- Recovery planning
- Passenger communication
- Executive summaries
- Incident reports
- Explanations

## What uses code

- Database queries
- Flight filtering
- Hotel search
- Cost calculations
- Business rules
- Notifications
- API integrations

## Memory

Store: Incident, Decision, Outcome, Cost, Feedback. Retrieve similar incidents through RAG.

## MVP

- Weather ingestion
- Flight disruption detection
- Multi-agent planner
- Hotel recommendation (simulated)
- Passenger notification
- Operations dashboard
- Timeline replay
- Executive report

## Future features

- Digital Twin
- Knowledge Graph
- Simulation Engine
- AI Negotiation Agent
- Voice Operations
- Mobile Commander
- Autonomous Enterprise Score

## Challenges

1. Hallucinations → RAG + validation
2. API failures → retries + fallback
3. Infinite loops → max iterations
4. Rate limits → caching
5. Latency → parallel execution
6. Context limits → retrieval
7. Data quality → validation
8. State management → single source of truth
9. Explainability → logs + reasoning
10. Security → never allow raw LLM actions

## Suggested folder structure

```
backend/
  agents/
  orchestrator/
  workflows/
  services/
  api/
  models/
  rag/
  memory/
frontend/
  dashboard/
  maps/
  timeline/
  analytics/
data/
  synthetic/
  historical/
docs/
```

## Demo story

1. Storm detected.
2. Delay predicted.
3. Planner creates recovery plan.
4. Hotels and transport allocated (simulated).
5. Passengers notified.
6. Dashboard updates.
7. Executive report generated.
8. Incident stored for future learning.

## Development roadmap

| Phase | Focus |
| --- | --- |
| 1 | Architecture & data |
| 2 | APIs and event engine |
| 3 | Multi-agent orchestration |
| 4 | RAG & memory |
| 5 | Dashboard |
| 6 | Replay & reports |
| 7 | Polish & demo |

## Golden rule

> Build an autonomous workflow engine — not a chatbot.

Every feature should answer: Can the AI detect? Can it reason? Can it decide? Can it execute? Can it
learn?

---

## Divergences from this blueprint

Recorded so the differences are deliberate rather than accidental:

| Blueprint | Current decision | Why |
| --- | --- | --- |
| OpenSky for flight data | **Simulated flight state** | OpenSky returns positions, not delay status — see [`../10-data-sources.md`](../10-data-sources.md) |
| Qdrant / ChromaDB | **Deferred; SQL retrieval for MVP** | D2 — structured filtering beats similarity at ~150 incidents |
| BGE embeddings | Stretch goal only | Follows from D2 |
| LangGraph or custom engine | **Custom, Redis Streams** | Fewer dependencies, full control over loop caps |
| Kaggle delay datasets | BTS + DGCA OTP | Better provenance and Indian calibration |
| OpenStreetMap | Not needed for MVP | Hotel distances are synthetic anyway |
| Hotel data source unspecified | **Synthetic — forced** | No free hotel API covers Indian airports |
