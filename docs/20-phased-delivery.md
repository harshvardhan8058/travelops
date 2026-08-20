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

Postgres schema, seeded synthetic data, real-weather provider with an offline fixture, orchestrator,
**Decision Assurance Gate**, and the deterministic services for a single flight: detect → assess →
identify connections → assure proposed actions → simulate recovery actions → notify. Ops Board plus
Decision Timeline in the UI. No model anywhere.

A policy-pack loader is included from Phase 1 so the data model never hardcodes DGCA. Phase 1 may run
either the fictional `demo` fixture or the encoded `in-moca-charter-2019` pack in `charter` mode, which
produces real cited figures behind a dated-source badge. Neither is presented as current law.

*Gate:* a disruption injected by API produces a complete recovery and a readable `decision_log`.

### Phase 2 — Cascade
**Demo claim: "disruption is never one flight."**

Multi-flight propagation, downstream connections, crew pairing impact, the cascade view. This is where
the 8-flights → 9-rotations story becomes visible rather than asserted. See
[`22-crew-pairing-model.md`](22-crew-pairing-model.md).

*Gate:* one weather event at BLR produces a traceable multi-flight, multi-pairing impact set.

### Phase 3 — Reasoning agents
**Demo claim: "the model plans and explains; it never decides alone."**

Planner, Explainer and Report Generator on Groq with `llama-3.3-70b-versatile`, behind distinct typed
contracts. Model proposals pass through the **existing Phase 1 assurance gate**. Add basic explainable SQL
precedent retrieval for Planner context. `LLM_MODE` supports live, fixture and off modes.

*Gate:* the same disruption completes in all three LLM modes. Off mode is the demo moment.

### Phase 4 — Verified regulatory intelligence and jurisdiction expansion
**Demo claim: "entitlements are computed from reviewed, cited policy—not from a model."**

Replace the demo fixture with the verified India pack, source-document hashes, deterministic rules and
citation cards. Add the jurisdiction resolver. A second-jurisdiction structural proof is optional and
only proceeds after India works end to end. See
[`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md).

*Gate:* every rupee figure traces to a clause, document and version.

### Phase 5 — Outcome learning and integrations
**Demo claim: "it records outcomes and can plug into real systems."**

Build analytics over prior gate, approval and outcome records; compare model metadata only as a
diagnostic, never as calibration without ground truth. Provider interfaces may be swapped toward real
APIs where access exists. Basic SQL precedent retrieval already shipped in Phase 3; this phase improves
ranking from observed/reviewed outcomes.

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

- Stage 2 (20–24 Aug): Phases 1–2 running; Phase 3 may be fixture-backed.
- Stage 3 (1–2 Sep): Phase 3 complete; minimum verified India policy-pack flow if the primary source is available.
- Semi-finals (9–10 Sep): Phase 4 India flow, citations, replay and full cascade; multi-jurisdiction proof remains optional.
- Finals (16 Sep): Phase 5 hardening, measured prototype outcomes and production roadmap.

Detailed stage gates are in [`14-hackathon-plan.md`](14-hackathon-plan.md) and
[`25-evaluation-readiness.md`](25-evaluation-readiness.md).

## Simulators are a design choice, not a fallback

Mentor review explicitly endorsed simulators and fake data. Our position, already settled in
[`DECISIONS.md`](DECISIONS.md): every external dependency sits behind a provider interface with a
simulator implementation. Flight status, hotels, transport and bulk notifications are simulated because
no suitable source/integration has been validated under the current access, budget and coverage
constraints—not because we ran out of time.

This means unavailable **operational integration APIs** cannot block the deterministic demo path, and
the demo is repeatable. It does not remove the separate need for primary legal sources, compliance
review, licences or venue-network planning. Fixed seed `20260807`, dataset committed, never regenerated
live. What is real, simulated, synthetic, fixture or unavailable is labelled in the UI with provenance
dots, so the honesty is visible rather than claimed.
