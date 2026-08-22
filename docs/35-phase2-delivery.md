# 35. Phase 2 — what shipped, and how it was verified

Full Disruption Intelligence, delivered as one increment. This is the record of what exists, what
was found while building it, and what could not be verified from the sandbox.

Written against `main` at `76c69d6` plus this branch. Decisions cited as **P2-D1/2/3** are in
[`DECISIONS.md`](DECISIONS.md#phase-2-architecture-decisions--final); the contracts are in
[`34-phase2-contract-alignment.md`](34-phase2-contract-alignment.md).

---

## 1. The journey, verified end to end

`scripts/verify_phase2.py` drives the whole product path against a real Postgres and the real
application process, and exits non-zero if any check fails. **22/22 passing**, both from the host
and from inside the built container image:

```
Bengaluru storm
  → 8-flight disruption group          declared membership, one incident per flight
  → network cascade                    group state derived from members
  → 604 passengers / 22 connections    unions, never sums (the naive sum would be 176)
    / 11 hotels / 9 crew rotations
  → blast radius                       5 dimensions, each naming the service that measured it
  → 40-node graph, 39 edges            every edge names the recorded row behind it
  → candidate recovery plans           2 variants with LLM_MODE=off, each with a plan hash
  → plan comparison                    zero-write re-evaluation, no rank, no score
  → group plan assurance               6 checks, authorises nothing
  → human approval where required      8 high-risk notifications, each approved by name
  → execution                          4 real services per incident, none faked
  → resolved                           group state 'resolved', 8/8 members
  → replay                             27 contiguous frames, the human decision reads as human
```

The console was then driven in a headless browser at 1920×1080 against that same API:
**9/9 routes render real figures, no runtime errors, no horizontal overflow, zero fixture reads.**

## 2. What Stream A built

| Item | Module | Note |
| --- | --- | --- |
| **A1** Group lifecycle | `orchestrator/group_state.py`, `group.py` | `incident_group.state` existed and nothing drove it. Now derived from members, forward only, `resolved` **only** when every member is |
| **A2** Cascade orchestration | `orchestrator/group.py` | One incident per **declared** member flight, through the existing `open_incident`. No second incident-creation path |
| **A3** Blast radius | `api/incident_groups.py` | Types and exposes Stream C's composition. Computes nothing; a guard test asserts it |
| **A4** Candidate plans | `orchestrator/candidates.py` | Variants along declared axes from the deterministic playbook, `plan_hash` per plan, attributed immutable selection |
| **A5** What-if | `api/incident_groups.py`, `orchestrator/candidates.py` | P2-D2: bounded, zero-write, deterministic. `basis` and `wrote_rows` are `Literal`s so the contract cannot express a projection |
| **A6** Group assurance + plan approval | `orchestrator/plan_assurance.py`, `plan_approval.py` | P2-D1 and P2-D3. See §3 |
| **A7** Replay | `api/replay.py` | A read over `decision_log`. No new state, no migration |
| **A8** Endpoints | `api/incident_groups.py`, `plans.py`, `replay.py` | 13 new paths, every one with a `response_model`. Both group fixture routes deleted in the same commit |
| **A9** Stream D's asks | `api/incidents.py`, `schemas/incidents.py` | Action-detail endpoint (FE-1), `incident_reference` on group flights (FE-6), `reason_code` promoted (FE-7) |

Also: `human_decision.scope` (migration 0008), three of Stream C's services registered
(`find_hotel_options`, `reserve_hotel_block`, `rebook_passengers`), `make demo-cascade`, and
`inject --cascade`.

## 3. Plan approval: one path to execution

**The single source of truth is `human_decision`**, one row per `assurance_evaluation`,
`assurance_id` still `UNIQUE NOT NULL`. `execute()` reads only that, and **does not know plan
approvals exist** — a guard test asserts the engine never imports the model.

A plan approval partitions the evaluations **already awaiting** a person, writes one ordinary
`human_decision` per covered evaluation, and returns the excluded ones with a reason each. It never
covers an evaluation produced later in the run: forward coverage would be a blank cheque over
actions nobody had seen.

**On the storm, plan approval correctly covers nothing.** Every held action is a high-risk
notification, so all 8 are excluded with `HIGH_RISK_NEEDS_OWN_DECISION` and each needs its own
decision. That *is* P2-D3 working — told by refusal rather than by coverage. The positive path is
proved in `tests/unit/orchestrator/test_plan_approval.py` over a synthesised low-risk evaluation,
because a mechanism only ever observed refusing is not known to work.

## 4. Six defects found by real-stack verification

Every one of these passed unit tests and would have failed on a projector.

**A blocked member spawned a new incident on every run.** `uq_incident_active_per_flight` is partial
over *active* states, so a member that reached `blocked` released its slot — and `POST /run` was
calling `open_group` first. The cascade grew by one incident per blocked flight per run, and the
derived group state was dragged backwards. Fixed: opening is `POST /open`, and only that.

**A candidate plan hijacked a live incident.** `_current_plan` took the *latest* plan by id, which
was correct while an incident could only have one. Opening the comparison screen creates sibling
plans, so "latest" silently switched a running incident onto a plan whose tasks had never been
assured; the operator's approval still pointed at the old plan's task, so the incident asked for
approval again and blocked. This is why the primary flight — the demo's headline — was the one that
did not resolve. Fixed: the selected plan, else the earliest.

**The group state machine refused legal progress twice.** Derivation can legitimately skip stages: 8
members moving from `awaiting_approval` to `resolved` in one sweep takes the group from `planning`
straight to `resolved`. Fixed by keeping the rule that matters — forward only, terminal reachable
from anywhere, terminal is final — instead of one step at a time.

**CORS blanked every screen.** `allow_origins` was hardcoded to port 5173, so a console served on
any other port rendered empty with the reason only in the browser console. That is the worst failure
mode available: it reads as "the backend is down" while the backend answers perfectly. Now
`CORS_ORIGINS`, configured.

**A plan-level ceiling made the flagship scenario unapprovable.** `max_passengers_affected: 400`
against a 604-passenger network event: a breach FAILs, and a FAIL is not approvable at plan level by
anyone, so "a human must accept this aggregate" had become "nobody may accept it". The ceilings now
admit the scenario while `escalation.passengers_fraction: 0.6` still forces a person to look. Two
Stream B tests asserted the old literals and now assert the property instead.

**A contract literal was CSS-uppercased.** `recorded_evidence` rendered as `RECORDED_EVIDENCE`
because the value sat inside an uppercased container — the same defect that once put "MOCA" on
screen for a policy pack labelled "MoCA Passenger Charter". The label may be uppercase; the value
may not.

One more, found by the test fixture rather than the stack: the e2e harness opened incidents at wall
clock instead of the scenario anchor, so the fixture's observation was days stale and the
notification was blocked on **evidence as well as risk**. That masked the fact that Phase 1 let an
operator approve past a failed freshness check — exactly what P2-D3 forbids, and now refused.

## 5. Verification

| Check | Result |
| --- | --- |
| Backend suite | **1492 passed, 38 skipped** (was 1087) |
| `ruff check` · `ruff format --check` | clean, 164 files |
| Frontend `typecheck` · `lint` · `tokens:check` · `format:check` · `test` · `build` | all pass, 22 frontend tests |
| Migrations on real Postgres | `0001 → 0008`, 41 tables, `alembic current` at head |
| Deterministic seed + reset | seed inside the container, digest stable |
| Real application path | `scripts/verify_phase2.py` **22/22**, host and container |
| Real container image | `docker build` + `docker run`, `/health/ready` → `ready`, both dependencies up |
| Browser at 1920×1080 | `npm run verify:console` **9/9** |
| OpenAPI | 28 paths, **no endpoint renders as `"string"`** |
| Phase 2 critical-path stubs | none. `evaluate_entitlements` remains the one deferred action and says so in the plan rationale |

### Not verified here, and why

- **`docker compose up`.** The compose plugin is not installed in this sandbox. The compose file
  parses, and the API image was built and run against real Postgres and Redis with the same mounts
  and environment compose declares — which is a proxy for `make up`, not a substitute. **Needs one
  run on the demo machine.**
- **The Windows PowerShell path.** Same reason: it needs the demo laptop.
- **Projector contrast and keyboard sweep on real hardware.** The automated check covers rendering,
  overflow and fixture leakage at 1920×1080; it cannot judge a projector's gamma.

## 6. What a reviewer should check first

1. **No high-risk action is ever covered by a plan approval.** The most important line in Phase 2.
2. No `FAIL` is approvable at plan level, at any tier.
3. `execute()` reads only `human_decision`; the engine never imports `PlanApproval`.
4. A plan approval covers only evaluations already awaiting when it was made.
5. Group figures are unions. No `sum` over `action.payload` anywhere.
6. What-if and the group summary persist nothing.
7. `uq_incident_active_per_flight` untouched; one active incident per flight.
8. Exactly one `actor_kind` mapping.
9. No aggregate assurance score at task, plan or group level.
10. The six frozen guard tests unmodified.

Items 1–9 have guard tests in `tests/unit/orchestrator/test_phase2_guards.py` and
`test_plan_approval.py`.
