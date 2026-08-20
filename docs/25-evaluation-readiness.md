# 25. Evaluation Readiness Gates

A feature is not “done” because a command exited successfully. Every checkpoint below requires visible,
repeatable evidence. Mark only what has been personally observed on the target branch and demo machine.

## Current readiness snapshot

| Area | Current state |
| --- | --- |
| Submitted idea deck | Complete and frozen |
| Architecture/requirements | Corrected after mentor review |
| Premium UI design system | Specified; not implemented |
| Application code | **Not started** |
| Runtime/demo | **Not available** |
| India policy pack | **Blocked on primary source + review** |
| Coforge internal tool integration | Unknown; not claimed |

## Evidence conventions

Each gate produces an evidence artifact in `artifacts/readiness/<stage>/` or the PR description:

- command and exit code
- relevant output/log excerpt
- screenshot or short recording
- source/provenance ledger
- known deviations and fallback used
- commit SHA and environment summary

Secrets and personal addresses are redacted. Fixture mode is acceptable only when visibly labelled.

## Stage 2 — 20–24 August: feasibility

### Build and cold start

- [ ] Clean checkout documented.
- [ ] `docker compose up --build` starts frontend, API, Postgres and Redis.
- [ ] Health endpoint confirms database/Redis/provider state.
- [ ] One command seeds/reset the fixed dataset.
- [ ] Startup works without Groq, SMTP or internet.

### Deterministic vertical slice

- [ ] Injecting `bengaluru_storm` creates exactly one incident.
- [ ] Risk output is index + level + factors + rule version—not an uncalibrated probability.
- [ ] Fallback plan completes a single flight end to end.
- [ ] Every action references one immutable assurance evaluation.
- [ ] Unknown action/entity, stale source and missing config cases fail closed.
- [ ] Idempotency test proves rerun does not double-reserve or double-notify.
- [ ] Decision timeline reconstructs the run in order.

### Cascade evidence

- [ ] Incident group contains 8 traceable flights.
- [ ] Exactly 9 crew pairings can be counted and each has an explicit affected leg/mechanism.
- [ ] Connection and hotel counts are computed from fixture records, not UI constants.
- [ ] Crew duty-time legality is not claimed.

### UI

- [ ] Zero purple/violet/indigo, gradients, glows or default AI-template styling.
- [ ] Graphite/instrument-cyan tokens match `21-design-system.md`.
- [ ] Operational numbers use tabular monospace.
- [ ] Status uses icon + label + colour and passes WCAG AA.
- [ ] Real/simulated/synthetic/fixture provenance is visible.
- [ ] 1920×1080 projector screenshot is legible.

### Stage 2 stop/go

**GO:** all deterministic vertical-slice and cold-start items pass. Cascade may be read-only/traceable.

**NO-GO:** no repeatable end-to-end run, duplicated actions, hidden provenance or missing assurance
record. Do not add LLM features until fixed.

## Stage 3 — 1–2 September: bounded reasoning

### Reasoning boundary

- [ ] Planner, Explainer and Report Generator are the only LLM-backed components.
- [ ] Every response is schema-valid or rejected/retried/falls back.
- [ ] Model cannot reference an unknown action or entity without rejection.
- [ ] `LLM_MODE=live`, `fixture` and `off` are visible and tested.
- [ ] Turning live inference off still resolves the deterministic scenario.
- [ ] Any model self-reported confidence is diagnostic metadata only.

### Assurance

- [ ] Six checks produce PASS/WARN/FAIL and reasons.
- [ ] Missing config, unknown rule/action and any FAIL block.
- [ ] High-risk action requires named/timestamped approval.
- [ ] Warning execution occurs only for an explicitly allowed low-risk action.
- [ ] Gate config version and hash are stored.

### Policy

- [ ] Generic pack loader/resolver/rules engine works in `demo` and `charter` modes.
- [ ] `charter` mode passes every case in the pack's `test_cases.yaml`, including the fail-closed and
      superseded-rule cases.
- [ ] Verified mode rejects `in-moca-charter-2019` with `PACK_NOT_VERIFIED_ELIGIBLE`.
- [ ] The 24-hour cancellation rule never evaluates and never appears in the UI.
- [ ] UI never labels a dated or fixture pack `VERIFIED`.
- [ ] If DGCA primary source + review are complete: approved India pack hash validates, rule tests pass,
      and one result links exact source clauses.
- [ ] If incomplete: authoritative amounts/citations are absent and the demo script takes the unverified
      branch.

### Stage 3 stop/go

**GO:** all reasoning and assurance items pass; policy may remain conspicuous demo mode.

**NO-GO:** model output bypasses gate, failure silently executes, or draft policy appears authoritative.

## Semi-finals — 9–10 September: complete product story

- [ ] Full 8-flight cascade executes or the single-flight/cascade-read model is explained honestly.
- [ ] 9 crew pairings are visible in the cascade graph.
- [ ] Real email reaches only allowlisted inboxes; bulk records clearly simulated.
- [ ] Replay opens evidence, actor, gate config and action result.
- [ ] Verified regulatory branch is used only if the approved pack exists.
- [ ] Seven-minute script completes three consecutive times.
- [ ] Backup video works offline.
- [ ] Clean machine setup completes in under 30 minutes.
- [ ] Q&A answers use corrected 1+3+10 terminology.
- [ ] No console errors, exposed secret, PII or broken empty/error state.

**GO:** three clean rehearsals plus backup.

## Finals — 16 September: hardening

- [ ] Feature freeze active; only blocker fixes.
- [ ] Dataset, prompts, assurance config and policy packs have hashes.
- [ ] Cold start, reset, offline and recovery procedures documented.
- [ ] Prototype performance metrics are measured across at least 10 fixture runs.
- [ ] Metrics are labelled prototype results—not airline savings.
- [ ] Threat/deployment/production gaps are stated.
- [ ] Source and licence ledger complete.
- [ ] Demo works on final laptop, projector and expected network.
- [ ] PR/branch SHA and backup video are frozen.

## Judge-criteria evidence map

| Criterion | Evidence—not a claim |
| --- | --- |
| Creativity | Cascade graph, assurance UI, model-off recovery, versioned policy packs |
| Feasibility | Cold start, deterministic E2E, fixtures, idempotency, provider fallbacks |
| Relevance | Ops-controller workflow, SME validation if obtained, India scenario |
| Technical depth | 1+3+10 architecture, typed events, assurance, immutable audit, policy DSL |
| Usability | Projector-tested Operations Room, progressive disclosure, keyboard access |
| Scalability | Provider interfaces, stage phasing, pack/version model; no unsupported throughput claim |
| Impact | Measured prototype cycle-time/action outcomes only; hypotheses labelled |
| Open source | Repository SBOM/dependency list; optional stack choices explained |
| Internal tools | Only a named, accessible, actually used Coforge tool; otherwise no claim |
| Autonomous enterprise | Context-specific plan + bounded execution + human approval + replay |

## Non-negotiable failure conditions

Any one blocks a live claim:

- unverified regulatory amount shown as law
- model confidence used to execute
- real/synthetic status hidden
- secret or PII exposed
- manual database edits required mid-demo
- duplicate side effect on retry
- a “verified” badge without source/review/hash validation
- submitted deck regenerated or silently altered
