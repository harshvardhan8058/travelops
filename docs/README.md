# TravelOps AI — Engineering Documentation

**An autonomous operating layer for airline disruption recovery.**
Built by **Team SkyForge AI** · Registration ID 201 · TechCon 2026 (Coforge)

> **Status:** submitted deck frozen; corrected architecture and implementation contracts ready;
> application code not started. Stage dates are recorded in [`DECISIONS.md`](DECISIONS.md).

## Read in this order

| # | Document | Why |
| ---: | --- | --- |
| 1 | [`DECISIONS.md`](DECISIONS.md) | Canonical naming, scope, dates and settled choices |
| 2 | [`09-requirements.md`](09-requirements.md) | What the product must and must not do |
| 3 | [`14-hackathon-plan.md`](14-hackathon-plan.md) | Stage 2 through finals, with explicit gates/cuts |
| 4 | [`24-input-acquisition.md`](24-input-acquisition.md) | Exactly what only the team must arrange, where and by when |
| 5 | [`25-evaluation-readiness.md`](25-evaluation-readiness.md) | Pass/fail evidence before each checkpoint |
| 6 | [`26-implementation-contracts.md`](26-implementation-contracts.md) | API, state, auth, security and observability baseline |
| 7 | [`21-design-system.md`](21-design-system.md) | Premium Operations Room UI; no purple/template styling |

## Architecture and safety

| Document | Purpose |
| --- | --- |
| [01 — Architecture](01-architecture.md) | One orchestrator, three reasoning agents, ten services |
| [02 — Disruption Flow](02-disruption-flow.md) | Evidence → risk → plan → assurance → execution |
| [03 — Agent Design](03-agent-design.md) | Typed reasoning contract and corrected taxonomy |
| [04 — LLM Strategy](04-llm-strategy-groq.md) | Bounded use of Groq and deterministic fallback |
| [05 — Memory](05-memory-and-rag.md) | SQL precedent and outcome recording |
| [06 — AI vs Deterministic](06-ai-vs-deterministic.md) | What a model may never decide |
| [07 — Risks](07-risks-and-mitigations.md) | Failure modes and mitigations |
| [18 — Assurance Gate](18-decision-assurance-gate.md) | Deterministic execution authorisation |
| [19 — Policy Packs](19-jurisdiction-and-policy-packs.md) | Versioned regulation, resolver and citation boundary |
| [22 — Crew Pairings](22-crew-pairing-model.md) | Why 8 flights can affect 9 rotations |

## Requirements, data and policy

| Document | Purpose |
| --- | --- |
| [10 — Data Sources](10-data-sources.md) | Candidate sources, validation status and fallbacks |
| [11 — Data Model](11-data-model.md) | PostgreSQL model, assurance and policy records |
| [12 — Synthetic Data](12-synthetic-data-plan.md) | Fixed-seed scenario and provenance |
| [13 — Policy Research](13-compensation-and-policy.md) | Provisional DGCA note; not authoritative until source/review |

## Delivery and presentation

| Document | Purpose |
| --- | --- |
| [08 — Backlog](08-blueprint-backlog.md) | Remaining/deferred product work |
| [15 — Demo Script](15-demo-script.md) | Seven-minute script with verified/unverified policy branches |
| [16 — Folder Structure](16-folder-structure.md) | Canonical modules and coding standards |
| [17 — Submitted Deck Prompt](17-presentation-prompt.md) | **Frozen historical artifact; do not edit/regenerate** |
| [20 — Phased Delivery](20-phased-delivery.md) | Five demonstrable phases |
| [23 — Stack Alignment](23-stack-alignment.md) | Optional Coforge list and deliberate tool choices |
| [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) | Unresolved team-access/review inputs only |

## Non-negotiable rules

1. One orchestrator + three reasoning agents + ten deterministic services.
2. Reasoning agents propose/explain; they never directly execute.
3. Decision Assurance Gate—not LLM self-confidence—controls execution.
4. Regulation is a reviewed versioned pack; retrieval cites but never calculates.
5. Draft policy, synthetic data and simulated actions are labelled in the UI.
6. `LLM_MODE=off` completes the core recovery.
7. Graphite Operations Room UI; no purple, gradients, glows or default AI-template styling.
8. If evidence cannot verify a claim, omit or qualify it—never invent it.
