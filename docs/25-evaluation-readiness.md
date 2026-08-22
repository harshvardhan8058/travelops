# 25. Evaluation Readiness Gates

A feature is not “done” because a command exited successfully. Every checkpoint below requires visible,
repeatable evidence. Mark only what has been personally observed on the target branch and demo machine.

## Current readiness snapshot

Re-verified against `main` on 21 August 2026. Only items with observed evidence are marked
done; anything not personally observed stays marked unconfirmed, because this table is what the
rest of the project cites when deciding what may be claimed.

| Area | Current state |
| --- | --- |
| Submitted idea deck | Complete and frozen |
| Architecture/requirements | Corrected after mentor review |
| Premium UI design system | Implemented for the demo path; remaining screens listed in `30-project-status.md` |
| Application code | Stage 2 deterministic slice complete: orchestrator, event bus, assurance gate, policy packs, four services, real endpoints, CLI |
| Runtime/demo | Recovery reaches `resolved` on the demo machine via `docker compose`, and against PostgreSQL 16 through the real Uvicorn process, with `LLM_MODE=off` |
| Full `docker compose` cold start | **Confirmed on the demo machine**, 21 August: healthy datastores, migrations, seed, injection, the gate holding a high-risk action, approval, execution, `resolved` |
| Operations console on the demo machine | **Unconfirmed.** The stack serves it; nobody has reported `:5173` rendering there |
| Reasoning agents (Stage 3) | **Not started.** `LLM_MODE=off` is the only exercised path |
| Six remaining deterministic services | **Not built.** Deferred from the plan and recorded as deferred, never faked |
| India policy pack | **Blocked on primary source + review.** `charter` mode works; `verified` unreachable by design |
| Coforge internal tool integration | Unknown; not claimed |

### Evidence for the runtime row

| Claim | Observed |
| --- | --- |
| The whole chain on the demo machine | `docker compose` on Windows / Docker Desktop 29.x (WSL2), 21 August: healthy datastores → migrations → seed → inject → run → hold → approve → run → `resolved` |
| `alembic upgrade head`, `make seed`, `make demo` | Also inside the built API image against PostgreSQL 16; seed is 2093 rows at digest `fa9564fc4afefc5d` |
| `POST /run` → `awaiting_approval` → approve → `POST /run` → `resolved` | Real Uvicorn process over HTTP, Redis deliberately unreachable, to prove the bus is optional |
| Risk 80 / `severe`, six named factors with observed values | `GET /incidents/{ref}` |
| 8 of 10 connections, 2 rotations, 0 real / 174 simulated notifications | Recorded `action` rows |
| An operator approval attributed to `human`, and the action referencing it | `GET /incidents/{ref}/timeline` and the `action` row's `human_decision_id` |
| Backend suite | 1087 passing; with `TRAVELOPS_TEST_DATABASE_URL` also 16/16 real-app PostgreSQL tests |

`scripts/verify_demo.py` re-checks thirteen of these in one command, inside the container, and
exits non-zero on any failure. Use it rather than re-deriving the sequence by hand.

The gap that remains is the console, not the backend: nobody has reported `:5173` rendering on
the demo machine.

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

Boxes below are ticked from observed evidence on `main`, in a build environment with
PostgreSQL 16 — **not** on the demo laptop. Anything requiring the demo machine, or the
console, is left unticked regardless of how likely it is to work.

Two things could not be exercised in that environment at all, so nothing below is ticked on
their account: `docker compose` is not installed there, and neither is Node, so the frontend
checks in `30-project-status.md` and every UI box in this file remain the demo machine's job.

### Build and cold start

- [x] Clean checkout documented. `README.md` carries the full sequence including `make seed`
      and `make demo`, which were previously missing from it.
- [x] `docker compose up --build` starts frontend, API, Postgres and Redis, and the data and
      recovery chain runs on it. **Confirmed on the demo machine**, Windows with Docker Desktop
      29.x (WSL2), 21 August: PostgreSQL and Redis healthy, migrations applied, seed loaded,
      injection opened the incident at risk 80, two deterministic actions executed, the
      high-risk notification was held, the operator approval persisted, the notification then
      executed, and the incident reached `resolved`.
      **Still unconfirmed on that machine: the console at `:5173`.** The API side of the stack
      is proven; nobody has yet reported the browser rendering. That belongs to the UI list
      below, which stays unticked.
- [x] Health endpoint confirms database/Redis/provider state. With Redis unreachable,
      `/health/ready` returns 503 and names `redis: down` per dependency rather than failing
      opaquely; `/system/mode` reports modes with no secret in the payload.
- [x] One command seeds/reset the fixed dataset. `make seed` → 2093 rows, digest
      `fa9564fc4afefc5d`; `make demo-reset` is repeatable and leaves no orphaned rows.
- [x] Startup works without Groq, SMTP or internet. Observed with no `GROQ_API_KEY`,
      `NOTIFICATION_MODE=console` and Redis pointed at an unreachable port.

### Deterministic vertical slice

- [x] Injecting `bengaluru_storm` creates exactly one incident. Injecting twice still yields
      one, and the suppression is recorded rather than silent.
- [x] Risk output is index + level + factors + rule version—not an uncalibrated probability.
      Index 80, band `severe`, `delay-risk-v1`, six named factors each with its observed value
      and point contribution.
- [x] Fallback plan completes a single flight end to end. `detected` → … → `resolved` with
      `LLM_MODE=off`, through the real Uvicorn process over HTTP.
- [x] Every action references one immutable assurance evaluation. The approved action also
      references its `human_decision`.
- [x] Unknown action/entity, stale source and missing config cases fail closed. Unresolvable
      entity refused by the real gate; a missing assurance config reports
      `workflow_executable: false` and blocks; `LLM_MODE=live` with no key refuses startup.
- [x] Idempotency test proves rerun does not double-reserve or double-notify. A replayed
      `Idempotency-Key` returns the original result and writes no second action; a second
      identical run changes nothing.
- [x] Decision timeline reconstructs the run in order. Around twenty records for the demo run —
      the exact count varies because an unreachable event bus adds its own
      `EVENT_PUBLICATION_FAILED` entry, which is itself part of the honest record.

### Cascade evidence

Verified by Stream C's real-app PostgreSQL suite, 16/16.

- [x] Incident group contains 8 traceable flights.
- [x] Exactly 9 crew pairings can be counted and each has an explicit affected leg/mechanism.
      All four mechanisms appear and each pairing carries exactly one.
- [x] Connection and hotel counts are computed from fixture records, not UI constants. The
      group figure of 22 is the union of distinct at-risk bookings, not a sum of per-incident
      counts — eight incidents each reporting 22 would imply 176.
- [x] Crew duty-time legality is not claimed.

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

#### Assessment, 21 August 2026

**GO on the deterministic vertical slice and cold start.** Every box in both lists is ticked from
observed evidence, and the cold-start box was closed by a run on the demo machine itself rather
than an equivalent: PostgreSQL and Redis healthy, migrations applied, seed loaded, incident opened
at risk 80, two deterministic actions executed, the high-risk notification held by the gate, the
operator approval persisted, the notification then executed, and the incident resolved.

None of the NO-GO conditions is present. The run is repeatable, actions are not duplicated,
provenance is on every datum, and no action exists without its assurance record.

**What GO does not cover.** The UI list below is separate and remains unticked — nobody has
reported the console at `:5173` rendering on the demo machine, and the projector legibility check
has not been done. So the accurate sentence in a review is:

> The deterministic recovery is verified end to end on the demo machine, including the gate
> holding a high-risk action and a human approval being recorded and honoured. The operations
> console has been built but its rendering on that machine is not yet confirmed.

One audit defect was found by that run and is fixed: an operator approval was recorded on the
Decision Timeline as `actor_kind=orchestrator`. Approvals and rejections are now both recorded at
the moment the operator acts, attributed to `human`, carrying the operator and assurance IDs, and
the executed action references the decision row. Four regression tests cover it and all four fail
if the fix is reverted.

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
