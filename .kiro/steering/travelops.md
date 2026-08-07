---
inclusion: always
---

# TravelOps AI — Project Conventions

**Project / use case title: TravelOps AI. Team identity: SkyForge AI (Registration ID 201).**

Multi-agent airline disruption recovery, built for the TechCon 2026 Hackathon (Coforge).
Industry: Travel Transport Hospitality (TTH) → Airlines Operations. Submission ~14 August 2026.

## Naming — use exactly this

- **SkyForge AI** = the team. Never the product.
- **TravelOps AI** = the project / use case title. Never rename it.
- **"TravelOps OS" is retired.** It was an earlier working title and must not appear anywhere.
- In prose, "an operating layer, not an assistant" is the approved positioning phrase.
- Team: Harshvardhan Sharma (136764), Karthikeyan D (138062), Harshvardhan Jha (136761),
  Sabyasachin Biswal (136794). Project: Arcolab. Department: CIMS.

Full decision record: `docs/DECISIONS.md`. Read it before changing architecture.

## Non-negotiable design rules

1. **The orchestrator is the brain, not the LLM.** Only 3 of 13 agents use a model: Planner, Explainer,
   Report generator.
2. **Structured output, never prose.** Every agent returns validated JSON matching `AgentResponse`
   (`status`, `confidence`, `action`, `reason`). The orchestrator never parses English.
3. **If there is one provably correct answer, write code.** Compensation, filtering, sorting and
   business rules never touch a model.
4. **Build a workflow engine, not a chatbot.** There is no conversational UI. Flow is
   `Event → Planner → Workflow → Agents → Execution`.
5. **The system must survive its own AI failing.** `LLM_MODE=off` must still complete a recovery via the
   deterministic fallback playbook. This is a demo asset, not just resilience.
6. **No magic numbers.** Thresholds, budgets and limits come from config. Never hardcode ₹6000.

## Stack (settled — do not re-litigate)

FastAPI · React + TypeScript + Tailwind + shadcn/ui · Postgres · Redis Streams · Groq
(`llama-3.3-70b-versatile`) · Docker, local only.

Rejected as overkill: **Kubernetes, Kafka, RabbitMQ**.

Vector store and embeddings (Chroma + BGE Small) are a **stretch goal only** — MVP retrieval is
structured SQL on airport, trigger, severity, weather and flight type. SQL-retrieved precedent injected
into a prompt is still RAG.

## Data rules

- **Real:** airports and runways (OurAirports, public domain), weather (aviationweather.gov METAR/TAF,
  no API key), schedules (AIKosh).
- **Simulated:** flight status. No free live feed is usable — AviationStack allows 100 requests/month,
  OpenSky returns positions not delay status.
- **Synthetic:** passengers, hotels, crew, transport. No free hotel API covers Indian airports; real PII
  is never used.
- Keep `data/loaders/` (real) separate from `data/generators/` (synthetic) so "which data is real?" is
  always answerable.
- Fixed generation seed (`20260807`). Commit the dataset dump; never regenerate during a demo.

## Regulatory rules — get these right

Compensation follows **DGCA CAR Section 3, Series M, Part IV**. See `docs/13-compensation-and-policy.md`.

- **Weather, ATC and security are force majeure: no cash compensation owed.**
- **Duty of care still applies regardless of cause** — meals after 2 hours, hotel and transfers after 6
  hours or when crossing nighttime.
- **Crew rostering failures are NOT force majeure** — regulators settled this. Cash is owed.
- Always return `regulation_refs` alongside any amount.
- Never invent a regulation or a rupee figure. Cite or leave blank.

## Scope boundaries

- Crew: **coordination and display only.** No duty-time legality validation.
- No real bookings, payments or refunds.
- Deferred: digital twin, simulation engine, voice, knowledge graph visualisation.

## Groq budget

~100K tokens/day on the free tier — roughly 25–50 planner calls. Cache aggressively and use
`LLM_MODE=fixture` for development. Prompts live in `backend/app/llm/prompts/` as versioned files, never
as inline strings.

## Working style

- `main` must always run. Integrate daily, never big-bang at the end.
- Freeze the API contract early so frontend can build against stubs.
- Test the deterministic half properly; assert schemas, not LLM content.
- Feature freeze the day before submission. No exceptions.
