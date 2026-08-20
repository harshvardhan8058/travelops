# 23. Open-Source Stack Alignment

The spreadsheet supplied by the team is a **list of free/open options, not a mandatory checklist**. Tool
selection follows requirements and delivery risk. Do not claim that matching every row is an evaluation
requirement unless the organisers say so explicitly.

## Our composition

TravelOps AI combines:

- open-source application components: React, TypeScript, Vite, Tailwind, shadcn/ui, FastAPI, Pydantic,
  SQLAlchemy, Alembic, PostgreSQL, Redis, pytest and structlog
- an open-weight model: Llama 3.3 70B
- a hosted inference provider: Groq
- public data providers and deterministic fixtures

“Open-source almost end to end” is too broad because Groq and some data/provider services are hosted
services. The wording above is accurate.

## Alignment with the supplied options

| Supplied layer/options | TravelOps choice | Decision |
| --- | --- | --- |
| Open model: Llama 3.3 70B | `llama-3.3-70b-versatile` via Groq | **Use** |
| Document extraction: Docling / MarkItDown | Docling candidate for policy-source extraction | **Selected, pending integration** |
| Vector DB: Chroma / FAISS / Qdrant | SQL retrieval first; Chroma optional later | **Deferred** |
| Relational DB: SQLite | PostgreSQL | **Different open-source choice** |
| Orchestration: LangGraph / CrewAI / AutoGen / Pydantic AI etc. | Custom typed Python orchestrator + Pydantic schemas | **Declined frameworks** |
| Graph DB: Neo4j / Memgraph / KuzuDB | Recursive PostgreSQL queries for pairings | **Declined** |
| Local serving: Ollama | Possible offline enhancement only if hardware supports the chosen local model | **Optional** |
| NVIDIA NIM | No need for second hosted provider | **Declined** |
| Continue.dev / Aider | Individual developer tooling, not delivered product | **Team choice** |

## Why the custom orchestrator stays

The mentor's corrected framing is strongest when the system has one explicit control plane:

- typed state transitions
- deterministic assurance
- retries/idempotency/limits
- complete audit record
- three reasoning agents that cannot directly execute

Replacing it with an agent framework solely to match a spreadsheet would add abstraction without
improving the proof. A framework may be reconsidered only if a concrete requirement appears that the
current orchestrator cannot meet safely.

## Why no graph database

Crew-pairing impact is a graph-shaped domain, but the fixed demo scale is small and PostgreSQL recursive
CTEs can traverse ordered pairing legs. A second database would increase setup, schema and failure
surface without a demonstrated need. The UI may still render a graph.

## Why Docling earns a place

Policy onboarding needs a repeatable path from official PDF to clause-structured text. Docling can
support extraction, while the original PDF/hash remains authoritative and humans review every rule.
This solves a real requirement and is independent of the suggested-list score.

## Internal Coforge tools

Unknown and deliberately unclaimed. We need an official eligible name, documentation, team access and a
real use. Acquisition path: [`24-input-acquisition.md`](24-input-acquisition.md). No placeholder,
invented accelerator or logo-only integration.

## Presentation wording

> “The platform uses open-source application components and the open-weight Llama 3.3 70B model, served
> through a swappable Groq provider. We selected tools from the optional list only where they solve a
> real requirement—Docling for policy-source extraction—and kept the orchestration explicit.”
