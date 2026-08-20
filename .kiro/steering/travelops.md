---
inclusion: always
---

# TravelOps AI — Project Conventions

**Project / use case title: TravelOps AI. Team identity: SkyForge AI (Registration ID 201).**

Multi-component airline disruption recovery, built for the TechCon 2026 Hackathon (Coforge).
Industry: Travel Transport Hospitality (TTH) → Airlines Operations.

Authoritative checkpoints: idea submission 10 Aug (complete, deck frozen); Stage 1 14–16 Aug; Stage 2
20–24 Aug; Stage 3 1–2 Sep; semi-finals 9–10 Sep; finals 16 Sep. Use
`docs/14-hackathon-plan.md` and `docs/25-evaluation-readiness.md`; never reintroduce the retired 7-day
submission plan.

## Naming — use exactly this

- **SkyForge AI** = the team. Never the product.
- **TravelOps AI** = the project / use case title. Never rename it.
- **"TravelOps OS" is retired.** It was an earlier working title and must not appear anywhere.
- In prose, "an operating layer, not an assistant" is the approved positioning phrase.
- Team: Harshvardhan Sharma (136764), Karthikeyan D (138062), Harshvardhan Jha (136761),
  Sabyasachin Biswal (136794). Project: Arcolab. Department: CIMS.

Full decision record: `docs/DECISIONS.md`. Read it before changing architecture.

## Non-negotiable design rules

1. **The orchestrator is the brain, not the LLM.** The correct taxonomy is **1 orchestrator + 3 reasoning
   agents (Planner, Explainer, Report Generator) + 10 deterministic services**. Never say "13 agents" —
   that was corrected in mentor review as agent inflation. A stateless rules-based service is a tool, not
   an agent.
2. **Structured outputs, distinct payloads.** Planner, Explainer and Report Generator share a typed
   envelope (`status`, `reason`, `evidence_refs`, `payload_type`) but use distinct payload contracts.
   Only `PlannerResponse.tasks[]` contains action enums and enters assurance. The orchestrator never
   parses English. **`confidence` is not in any execution contract**—see rule 7.
3. **If there is one provably correct answer, write code.** Compensation, filtering, sorting and
   business rules never touch a model.
4. **Build a workflow engine, not a chatbot.** There is no conversational UI. Flow is
   `Signal → Event → Orchestrator → reasoning proposal/fallback → Assurance Gate → deterministic service → audit`.
5. **The system must survive its own AI failing.** `LLM_MODE=off` must still complete a recovery via the
   deterministic fallback playbook. This is a demo asset, not just resilience.
6. **No magic numbers.** Thresholds, budgets and limits come from config. Never hardcode ₹6000.
7. **Never gate execution on LLM self-reported confidence.** Use the deterministic **Decision Assurance
   Gate** — six verifiable checks (evidence completeness, source freshness, entity validation, policy
   compliance, conflict detection, action risk tier). See `docs/18-decision-assurance-gate.md`. Model
   self-report may be logged as `model_self_report` for calibration comparison, never for control flow.
   Show percentages only where calibrated; otherwise a risk level with contributing factors.
8. **Regulation is data, not code.** `Trip Context → Jurisdiction Resolver → versioned Policy Pack →
   deterministic Rules Engine → cited explanation`. RAG retrieves and cites legal text; it never
   calculates or authorises an entitlement. Adding a jurisdiction must not require application code
   changes. See `docs/19-jurisdiction-and-policy-packs.md`.
9. **Build in phases that each end at a demonstrable system.** Deterministic first, LLM in Phase 3. See
   `docs/20-phased-delivery.md` and cut from the bottom of its cut list.

## Parallel work

Six Kiro accounts run against this repo. **Stay inside your stream's owned paths** — see
`docs/28-parallel-workstreams.md`. Never edit `migrations/`, `models/`, the generated API client,
`policy_packs/`, compose/Makefile, or this steering file unless your stream owns it. Request the change
from the owning stream instead.

## UI rules — read `docs/21-design-system.md` and `docs/27-ui-specification.md` before writing any component

- **No purple, violet or indigo. Anywhere.** No gradients, glows, aurora blobs, glassmorphism on cards,
  gradient text, or ✨/🤖 emoji icons. That is the default AI-demo aesthetic and it reads as a template.
- This is an **airline operations console**: near-monochrome graphite base (`#0B0F14` / `#111821`), a
  single non-status accent (instrument cyan `#3FC9DE`), and green/amber/red reserved **exclusively** for
  operational state.
- **All operational data is monospaced with tabular numerals** — flight numbers, gates, timestamps,
  delays, amounts, PNRs. JetBrains Mono. This is the signature detail.
- Inter for UI text, 14px body (dense, not 16), 34px table rows, 4px spacing scale, `rounded-md`, 1px
  borders instead of shadows.
- Tokens only — no colour literals in components. Lucide icons only. One shared `<StateBadge>`.
- Never rely on colour alone: every state carries an icon and a label. WCAG AA, visible focus rings.
- Target 1920×1080 — it will be projected.

## Stack (settled — do not re-litigate)

FastAPI · React + TypeScript + Tailwind + shadcn/ui · Postgres · Redis Streams · Groq
(`llama-3.3-70b-versatile`) · Docker, local only.

Rejected as overkill: **Kubernetes, Kafka, RabbitMQ**.

Vector store and embeddings (Chroma + BGE Small) are a **stretch goal only** — MVP retrieval is
structured SQL on airport, trigger, severity, weather and flight type. SQL-retrieved precedent injected
into a prompt is still RAG.

**Docling** (or MarkItDown) is adopted for regulatory PDF → structured clause text, feeding the policy
packs. Optional if time allows: **Ollama** as a local fallback LLM provider behind the existing interface.

The open-source list supplied to the team is **optional reference material**, not a mandatory checklist.
Do not adopt LangGraph, CrewAI, AutoGen, a graph database or SQLite to tick boxes—the custom orchestrator
and Postgres are deliberate. Rationale per tool: `docs/23-stack-alignment.md`. When presenting, say
**"open-weight Llama 3.3 70B served through Groq"**; the overall system mixes open-source components,
an open-weight model and hosted APIs.

## Data rules

- **Real/public when fetched and archived:** airports/runways (OurAirports), weather (AWC/Open-Meteo).
- **Planned real, not yet validated:** AIKosh schedules. Until raw file/schema/licence are archived and a
  loader test passes, schedules are synthetic and must be labelled so.
- **Simulated:** flight status, bookings/actions, bulk channels.
- **Synthetic:** passengers, hotels, crew, transport and historical incidents. Real PII is prohibited.
- Every API/UI datum carries provenance: `real | simulated | synthetic | fixture | unavailable` plus
  source timestamp where applicable.
- Keep loaders, generators and fixtures separate. Fixed seed `20260807`; commit the demo dataset and do
  not regenerate during evaluation.

## Regulatory rules — fail closed

The current DGCA primary CAR and SME review are not yet in the repo. `docs/13-compensation-and-policy.md`
is provisional research, not executable law.

- Regulation flow: Trip Context → resolver → **approved, source-hashed policy pack** → deterministic
  engine → Assurance Gate → cited result.
- Three policy modes. `demo` = fictional fixture, no citation. `charter` = the encoded MoCA Passenger
  Charter (Feb 2019) pack, real cited figures behind the badge *pending CAR verification*. `verified` =
  current primary CAR + SME sign-off, **not reachable yet**. Only `verified` may be described as current law.
- The charter pack lives at `policy_packs/in-moca-charter-2019/2019.02/`. Delay attracts **no cash
  compensation** in that instrument; cash exists only for cancellation and denied boarding. Never describe
  a delay payout.
- The 24-hour no-charge cancellation rule is `superseded_suspected` (reported Feb 2026 amendment moved it
  to 48 hours). It must never evaluate and never appear in a demo.
- Never infer force majeure from a generic `trigger_type`; cause assessment requires pack-defined facts
  and evidence.
- Missing source, clause, required fact, pack approval or conflict rule produces `needs_human`.
- RAG may retrieve/cite text; it never selects jurisdiction, calculates or authorises.
- Exact team acquisition steps: `docs/24-input-acquisition.md`.

## Scope boundaries

- Crew: **coordination and display only.** No duty-time legality validation.
- No real bookings, payments or refunds.
- Deferred: digital twin, simulation engine, voice, knowledge graph visualisation.

## Groq budget

Provider limits are account/model-specific and can change. Read them from the team's Groq console;
never hardcode a third-party quota estimate. Cache repeated scenarios and use `LLM_MODE=fixture` for
development. Prompts live in `backend/app/llm/prompts/` as versioned files, never inline.

## Working style

- `main` must always run. Integrate daily, never big-bang at the end.
- Freeze the API contract early so frontend can build against stubs.
- Test deterministic services, assurance, idempotency and the demo path; assert schemas, not LLM prose.
- Feature freeze at least one day before each evaluation.
- Read `docs/24-input-acquisition.md` before asking the user for data/access; do not ask for anything the
  development side can source or simulate.
- Read `docs/25-evaluation-readiness.md` before claiming any stage is ready.
- `docs/17-presentation-prompt.md` is frozen historical material; never edit it to repair submitted claims.
