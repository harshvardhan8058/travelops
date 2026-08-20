# 23. Open-Source Stack Alignment

Mapping our stack onto the Coforge suggested open-source AI stack list.

## Status of that list

The organisers publish it as **suggested and free**, not mandatory. The stated reason is cost: teams fund
their own environment, so those tools are options that carry no licence spend. The evaluation criteria
mention "use of internal tools and open-source stack," so alignment matters for scoring — but adopting a
tool we do not need would be worse engineering, and judges notice bolted-on dependencies.

Our position: **our stack is already open-source almost end to end.** We adopt from the list where it
earns its place and decline the rest with a reason.

## Where we already align

| Layer | Their list | Ours | Note |
| --- | --- | --- | --- |
| Open model | Llama 3.3 70B | **`llama-3.3-70b-versatile`** | Exactly the listed model. Groq is only the inference host, free tier |
| Vector DB | Chroma, FAISS, Qdrant | **Chroma** (optional) | Already our choice, gated behind Phase 5 |
| Relational | SQLite | **PostgreSQL** | Also open-source; we need concurrent writes and recursive CTEs |
| Structured LLM output | Pydantic AI | **Pydantic** + typed contracts | Same guarantee, already core to FastAPI |

Everything else in the build is open-source by default: React, TypeScript, Vite, Tailwind, shadcn/ui,
FastAPI, SQLAlchemy, Alembic, Redis, Docker, pytest, structlog.

Phrasing that matters when presenting: say **"open-weight Llama 3.3 70B"**, not "Groq". The model is the
open-source component; Groq is a swappable inference endpoint.

## Adopted from the list

**Docling** (or MarkItDown) — legal PDF to structured, citable text.

Adopted because we need it regardless of any list. Turning a published CAR document into clause-level
chunks with structure intact is what makes the policy pack and its citations reproducible instead of
hand-typed, and re-runnable when a regulation is amended. See
[`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md).

## Optional, only with spare time

**Ollama** — local model serving as a fallback provider behind the existing LLM interface.

Cheap because the interface already exists, and it makes the demo survive a dead venue network. Skip
without hesitation if Phase 3 is behind.

## Declined, with reasons

| Declined | Why |
| --- | --- |
| **LangGraph / CrewAI / AutoGen** | Our custom orchestrator is the architectural thesis: deterministic control, inspectable state, no framework magic. Mentor review endorsed this framing. Swapping in a graph framework to match a list would weaken the strongest part of the design |
| **Neo4j / Memgraph / KuzuDB** | The crew cascade is a recursive CTE over `pairing_leg`. A second database for a query Postgres already does well is complexity for its own sake — see [`22-crew-pairing-model.md`](22-crew-pairing-model.md) |
| **SQLite** | Postgres is settled. Recursive CTEs, JSONB, and concurrent writers across four developers |
| **Qdrant / FAISS** | Chroma covers optional Phase 5 retrieval. One vector store is enough |
| **NVIDIA NIM** | Groq's free tier already serves the listed open model. No second inference provider |
| **LlamaIndex PropertyGraph** | Retrieval scope is deliberately narrow: fetch and cite one clause. A property graph over legal text is a research project, not a seven-day feature |
| **Continue.dev / Aider** | Developer tooling, individual choice, not part of the delivered system |

## Internal Coforge tools

**Unresolved and deliberately unclaimed.** We will not invent or assume an internal tool or accelerator.
Once the team confirms what we are permitted to name, it gets added here and to the relevant docs.
Accuracy over filling the box. Tracked in [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

## Net effect

One addition (Docling), one optional (Ollama), no rewrites. The stack in
[`DECISIONS.md`](DECISIONS.md) stands.
