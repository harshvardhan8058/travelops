# 20. Phased Delivery

Scope de-risking, in response to mentor review:

> It's ambitious and carries significant risk. I'd suggest breaking it into smaller phases with clear,
> valuable deliverables... if you don't end up completing everything, the entire project won't be at
> risk.

## The principle

Every phase ends at a **demonstrable system**, not a layer. If we stop after any phase, we still have
something that runs end to end and tells a coherent story. No phase is a horizontal slice whose value
only appears when the next one lands.

Ordering is deliberately **deterministic-first**. The LLM arrives in Phase 3, by which point a full
recovery already completes without it. That ordering is the reason `LLM_MODE=off` works — resilience as
a consequence of build order, not a feature bolted on.

## Phases

### Phase 1 — Deterministic vertical slice
**Demo claim: "one disrupted flight, recovered end to end, every step logged."**

Postgres schema, seeded synthetic data, real weather ingestion, orchestrator, and the deterministic
services for a single flight: detect → assess → rebook → duty of care → notify. Ops Board plus Decision
Timeline in the UI. No model anywhere.

*Gate:* a disruption injected by API produces a complete recovery and a readable `decision_log`.

### Phase 2 — Cascade
**Demo claim: "disruption is never one flight."**

Multi-flight propagation, downstream connections, crew pairing impact, the cascade view. This is where
the 8-flights → 9-rotations story becomes visible rather than asserted. See
[`22-crew-pairing-model.md`](22-crew-pairing-model.md).

*Gate:* one weather event at BLR produces a traceable multi-flight, multi-pairing impact set.

### Phase 3 — Reasoning agents
**Demo claim: "the model plans and explains; it never decides alone."**

Planner, Explainer, Report Generator on Groq with `llama-3.3-70b-versatile`, behind typed contracts.
Decision Assurance Gate between planner output and execution. `LLM_MODE` switch with fixture and off
modes.

*Gate:* the same disruption completes in all three LLM modes. Off mode is the demo moment.

### Phase 4 — Regulatory intelligence
**Demo claim: "entitlements computed from cited law, not from a model."**

Jurisdiction resolver, India policy pack, deterministic rules engine, citation cards, and the EU 261
structural proof. See [`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md).

*Gate:* every rupee figure traces to a clause, document and version.

### Phase 5 — Learning and integrations
**Demo claim: "it gets better, and it can plug into real systems."**

Precedent retrieval, outcome recording, calibration view comparing model self-report against gate
outcomes, analytics. Provider interfaces swapped toward real APIs where any exist.

*Gate:* a second similar incident surfaces the first as precedent.

## What gets cut, in order

If we run out of time, cut from the bottom. Written down now so the decision is made calmly rather than
at midnight:

1. Vector store and embeddings — SQL retrieval is sufficient
2. EU 261 pack beyond the structural proof
3. Learning agent precedent surfacing
4. Analytics beyond the gate metrics
5. Framer Motion polish
6. Light theme

**Never cut:** the deterministic recovery path, the decision log, `LLM_MODE=off`, or citation on every
entitlement. Those four are the submission.

## Mapping to the calendar

Next formal evaluation is **20–24 August**. Phases 1 and 2 must be running by then, with Phase 3
started — that demonstrates real progress on a working system rather than a slide deck. Phases 4 and 5
land across the September evaluation rounds, which is exactly the iterative development the organisers
described.

Day-level detail stays in [`14-hackathon-plan.md`](14-hackathon-plan.md).

## Simulators are a design choice, not a fallback

Mentor review explicitly endorsed simulators and fake data. Our position, already settled in
[`DECISIONS.md`](DECISIONS.md): every external dependency sits behind a provider interface with a
simulator implementation. Flight status, hotels, transport and bulk notifications are simulated because
no free source is usable — not because we ran out of time.

This means **no missing API can block a phase gate**, and the demo is deterministic and repeatable.
Fixed seed `20260807`, dataset committed, never regenerated live. What is real, simulated and synthetic
is labelled in the UI with provenance dots, so the honesty is visible rather than claimed.
