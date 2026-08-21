# 14. Stage-Aligned Delivery Plan

The original seven-day plan targeted the idea-submission window and is retired. The submitted deck is
frozen; implementation continues iteratively through the official evaluation checkpoints.

## Authoritative TechCon 2026 calendar

| Checkpoint | Date | TravelOps AI exit condition |
| --- | --- | --- |
| Registrations opened | 22 July | Complete |
| Registrations closed | 9 August | Complete |
| Idea submission | 10 August | Complete — submitted deck remains unchanged |
| Stage 1 evaluations | 14–16 August | Architecture and mentor response complete |
| **Stage 2 evaluations** | **20–24 August** | Working deterministic vertical slice **with assurance gate**; traceable cascade data; premium UI shell |
| **Stage 3 evaluations** | **1–2 September** | Three reasoning agents, existing assurance gate, SQL precedent retrieval and generic policy-pack flow; verified India pack only if source/review complete |
| **Semi-finals** | **9–10 September** | Full seven-minute scenario, citations, replay, resilience, backup video |
| **Finals** | **16 September** | Hardened prototype, evidence pack, rehearsed pitch, no unresolved demo blockers |

Source: the TechCon 2026 schedule supplied by the team. If the organisers publish a revision, update
this table and [`25-evaluation-readiness.md`](25-evaluation-readiness.md) together.

## Delivery rule

Every checkpoint must end with a **working end-to-end system**, not a collection of disconnected layers.
If a gate slips, cut from the bottom of the cut list; never weaken auditability, deterministic fallback,
or honesty about simulated data.

## Immediate baseline

When this plan was first written the repository held design documentation and no application code, so the
first milestone was a vertical slice rather than more architecture.

**That milestone is delivered.** As of `main` at `2dd3833` (21 August 2026) the Wave 0 bootstrap is
merged and verified: a runnable four-service stack, 33 tables with a matching initial migration, typed
event and assurance contracts, a versioned gate config, 11 API fixtures, the UI shell, and 103 passing
backend tests. The baseline for the remaining checkpoints is therefore a working deterministic skeleton
with 18 deliberate stubs, not an empty repository — see
[`30-project-status.md`](30-project-status.md) for the verified inventory.

## Stage 2 — prove feasibility (20–24 August)

**Question to answer:** can the team run and explain one complete disruption recovery?

### Must demonstrate

1. `docker compose up` starts API, Postgres, Redis and the React application.
2. A committed fixed-seed dataset loads successfully.
3. The premium Operations Room UI renders one disrupted flight, provenance labels, and a live decision
   timeline.
4. Injecting `bengaluru_storm` creates exactly one incident.
5. The deterministic workflow completes: detect → assess → connection impact → simulated hotel/transport
   actions → notification records → resolved.
6. Every proposed action passes through the Decision Assurance Gate and records the six check outcomes.
7. The cascade dataset makes 8 affected flights and 9 crew pairings individually traceable, even if the
   first live workflow executes only one flight.
8. Real AWC weather is shown when reachable; a fixture produces the same screen when offline.

### Must not claim yet

- No legally authoritative rupee entitlement until the current DGCA primary document and rule review are
  complete.
- No real rebooking, hotel booking, crew legality, payment, SMS delivery or live flight-status feed.
- No production-scale performance, calibrated delay probability or historical error rate.

### Team split

Four Kiro accounts run four streams with exclusive file ownership. Full allocation, token-load
ranking, sequencing constraints and branch model: [`28-parallel-workstreams.md`](28-parallel-workstreams.md).
Paste-ready prompts: [`kickoff/`](kickoff/README.md).

| Stream | Stage 2 output |
| --- | --- |
| A · Core & API | Event bus, orchestrator engine, real endpoints replacing fixtures, CLI |
| B · Assurance & Policy | Six checks, fail-closed aggregation, pack loader, charter-mode evaluation |
| C · Data, Providers & Services | Loaders, generators, weather/flight/notification providers, Delay Risk, Connection, Crew Impact, Communication |
| D · Frontend | Recovery workspace, assurance panel, approval queue, policy citation |

**Contract freeze already happened.** Wave 0 is merged: schema, migration, event and task contracts,
assurance record shape and an endpoint fixture set are all on `main`, so the four streams start
simultaneously rather than waiting a day.

**Gate:** a clean checkout reaches a resolved incident without calling an LLM. If this does not work,
do not start Phase 3.

## Stage 3 — prove bounded intelligence (1–2 September)

**Question to answer:** does AI add useful reasoning without becoming the control plane?

### Must demonstrate

1. Planner, Explainer and Report Generator return typed, schema-validated output.
2. `LLM_MODE=live`, `fixture` and `off` all complete the same core recovery.
3. The assurance gate—not model self-confidence—authorises or blocks each action.
4. A model-proposed unknown action, stale source, missing entity and high-risk action are visibly blocked.
5. SQL retrieval surfaces a planted precedent and records why it matched.
6. India policy-pack flow works end to end in `charter` mode against
   `in-moca-charter-2019/2019.02`: source metadata → rule → deterministic evaluation → citation card with
   the dated-source badge. Promotion to `verified` requires the primary CAR and SME sign-off; excluded
   rules must remain excluded.

**Gate:** kill the model in front of the reviewer; the recovery still completes and the UI explains the
degraded path.

## Semi-finals — prove the complete story (9–10 September)

**Question to answer:** is this a compelling, usable autonomous-enterprise product?

- Execute the 8-flight cascade and render the 9-pairing graph.
- Run the seven-minute demo without a terminal.
- Show live email to controlled inboxes plus simulated bulk-channel records.
- Show replay, policy citation, human approval and executive report.
- Demonstrate source provenance: real, simulated, synthetic, fixture and unavailable.
- Run from a clean machine and from the backup recording.
- Present the correction cleanly: 1 orchestrator + 3 reasoning agents + 10 deterministic services.

**Gate:** three consecutive rehearsals complete with the same material outputs and under seven minutes.

## Finals — prove readiness and impact (16 September)

**Question to answer:** is this credible beyond a hackathon demo?

- No new features after the semi-finals unless they remove a blocker.
- Verify cold start, offline mode, rollback, logs, accessibility and projector legibility.
- Freeze source documents, policy-pack hashes, demo data and prompts.
- Prepare measured—not invented—impact metrics from the prototype runs.
- State the production gaps: airline APIs, certified crew legality, legal review, IAM and operational
  deployment.

**Gate:** one command starts the demo, one command resets it, and one backup video proves the entire path.

## Cut list — first to last

1. Light theme and decorative motion
2. Vector database and embeddings; keep SQL retrieval
3. EU policy-pack structural proof
4. Learning analytics beyond gate and approval metrics
5. Timeline scrubbing; retain chronological decision log
6. Gate/stand reassignment
7. Full cascade execution; retain traceable cascade data and single-flight execution

### Never cut

- Deterministic end-to-end recovery
- Decision Assurance Gate and audit log
- `LLM_MODE=fixture` and `LLM_MODE=off`
- Provenance labels for real/simulated/synthetic data
- Citation on every legal claim or entitlement; if no verified citation exists, omit the claim
- Fixed-seed demo fixture and reset command

## Integration discipline

- `main` remains runnable; short-lived branches only.
- Freeze API contracts before frontend/backend parallel work.
- Provider interfaces always include fixtures so external APIs cannot break the demo.
- Feature freeze at least one day before each live evaluation.
- Never wait for live weather, a live flight or a vendor response during judging.

## Inputs the team must obtain

The implementation can start without production airline data. The small set of team-supplied inputs and
where to obtain them is maintained in [`24-input-acquisition.md`](24-input-acquisition.md). The exact
pass/fail checklist for each stage is [`25-evaluation-readiness.md`](25-evaluation-readiness.md).
