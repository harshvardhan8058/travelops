# 33. Phase 2 — Stream A Implementation Plan

Full Disruption Intelligence, from the Core & API side. **Planning only. No code until the
ownership and dependency contracts are aligned across streams.**

Written against `main` at `5052388`. Phase 1 is closed: the deterministic slice runs to `resolved`
on the demo machine, and an operator approval is attributed to a person.

**Revised for the final Phase 2 architecture decisions P2-D1, P2-D2 and P2-D3**, recorded in
[`DECISIONS.md`](DECISIONS.md#phase-2-architecture-decisions--final). A6 changed substantially and
A5's boundary is now settled law rather than a recommendation; §0 records what moved.

**Cross-stream contracts, scope terminology, the A1–A9 order and the confirmation checklist that
gates implementation are in [`34-phase2-contract-alignment.md`](34-phase2-contract-alignment.md).**
This document is the design; that one is what B, C and D sign off.

> **Label hygiene.** Items here are **`A1`–`A9`** (renamed from `A2.1`–`A2.9`). `P2-D1/2/3` are the
> team's Phase 2 architecture decisions. `D1`–`D7` in A9 are **Stream D's dependency asks** from
> their plan (PR #40). `DECISIONS.md` separately carries design decisions `D1`–`D6` and assumptions
> `A1`–`A4`. Four namespaces, two letters — always cite the prefixed form.

---

## 0. Decisions, and what is still open

### 0.1 What-if — **settled: P2-D2**

The team has ruled. What-if is **in scope**, bounded to a **zero-write deterministic
re-evaluation**, and is **explicitly not a simulation engine or digital twin**. The boundary is
recorded canonically in
[`DECISIONS.md` → P2-D2](DECISIONS.md#p2-d2--what-if-is-in-scope-bounded-to-zero-write-deterministic-re-evaluation);
that entry, not this document, is the citable source.

This resolves the contradiction my draft raised. For the record, the three scope documents that put
a simulation engine out of scope are unchanged and still binding — P2-D2 does not amend them,
because a re-evaluation over recorded facts adds no new subsystem. What is now settled:

| | |
| --- | --- |
| **In scope** | Re-evaluating the *same recorded facts* through the *same deterministic* gate and services, varying only declared inputs |
| **Out of scope, still deferred** | Projected world state, predicted delay/cost/outcome, any figure not traceable to a stored fact, digital twin |
| **Enforcement** | `basis: Literal["recorded_evidence"]` — the contract cannot express a projection — plus a row-count-unchanged test |

Practically this ratifies the direction A5 was already written to, so **A5 does not grow**.
Stream D's plan (PR #40) reached the same boundary from the response schemas, which is why the
decision was cheap to make.

### 0.2 "Phase 2" means something narrower in `20-phased-delivery.md` — **still open**

That document defines Phase 2 as **Cascade**: "multi-flight propagation, downstream connections,
crew pairing impact, the cascade view", gated on "one weather event at BLR produces a traceable
multi-flight, multi-pairing impact set."

The scope in this request is wider — plan comparison and replay sit in Phases 4–5 there. That is
fine, but the four stream plans must agree on one definition or the readiness gates stop matching
the work. Proposed mapping, for review:

| Item | `20-phased-delivery.md` phase | Include in this Phase 2? |
| --- | --- | --- |
| Group lifecycle, cascade orchestration, blast radius | 2 | Yes — this is the Phase 2 gate |
| Replay orchestration | 4, but "nearly free" per backlog | Yes — cheap, and D already has a route stub |
| Candidate plan lifecycle and comparison | 4–5 | Yes — A5, per P2-D2 |
| What-if as zero-write re-evaluation | not enumerated | **Yes** — P2-D2 |
| What-if as projection / digital twin | Deferred | **No** — P2-D2 excludes it |

### 0.3 Phase 2 cut order — settled

**Open-Meteo and historical provider expansion is non-critical and is cut before any core Phase 2
feature.** Recorded in
[`DECISIONS.md` → Phase 2 cut order](DECISIONS.md#phase-2-cut-order). No Stream A item depends on
forecast or historical retrieval, so this costs this plan nothing — the recorded METAR path already
carries every figure A2 and A3 report.

---

## 1. The invariant this plan preserves

> **An incident is one flight. A disruption group is the network event.**

Concretely, and non-negotiably:

- `uq_incident_active_per_flight` stays. One active incident per flight, enforced by the database.
- A plan is authorised **for one incident**, and its actions report **that flight's** figures.
  `check_connections` on 6E 2134 reports 8 connections because that is what that flight breaks.
- Group figures are **derived by union**, never by summing per-incident counts. Stream C already
  established this: 22 distinct at-risk bookings across the group, not eight incidents each
  claiming 22, which would imply 176.
- **The gate authorises actions, not groups and not plans.** Nothing in A6 may become a second
  path to execution.

## 2. What already exists, and must not be rebuilt

Naming this explicitly because the last two waves lost time to two streams building the same seam.

| Capability | Owner | Where | Stream A's role |
| --- | --- | --- | --- |
| Group rollup: flights, passengers, connections union, hotels, pairings with mechanism precedence, completeness flags | **C** | `app/db/scenario_queries.cascade_rollup()` → `CascadeRollup` | **Call it.** Do not recompute any figure it returns |
| Recursive pairing SQL | C | `affected_pairings_recursive()` (Postgres only) | Call it where a group view needs it |
| The six checks, aggregation, `gate_requirements()` | **B** | `app/assurance/`, `app/policy/` | Ask, never re-derive |
| Service adapters and registration | C | `app/orchestrator/service_registry.py` (Stream A file, C's content pattern) | Register new actions here only |
| `decision_log` chronology | A | `app/orchestrator/engine.py` | Replay reads it |
| `actor` → `actor_kind` mapping (the Phase 1 human-attribution fix) | A | `app/api/incidents.py:104` | **Move and share it.** Never a second copy |
| `IncidentGroup` table with `state`, `reference`, `root_cause`, `airport_icao`, `severity` | C (schema) | `app/models/workflow.py` | Drive the lifecycle; no new table needed |

---

## A1 — Disruption-group lifecycle

The `incident_group.state` column exists and **nothing drives it**. C's seed writes `detected` and
it never moves. A group that is 8 flights deep and still says `detected` is a broken audit surface.

### Files
- `backend/app/orchestrator/group_state.py` — **new.** Legal group transitions and `assert_transition`.
- `backend/app/orchestrator/group.py` — **new.** `DisruptionGroupOrchestrator`: open, advance, close.
- `backend/app/orchestrator/engine.py` — touched only to notify the group when a member incident
  reaches a terminal state.

### Public contracts

```python
# group_state.py — deliberately REUSES IncidentState, with a documented legal subset.
GROUP_STATES: frozenset[IncidentState]   # {detected, assessing, planning, executing,
                                         #  resolved, blocked, failed}
GROUP_TRANSITIONS: dict[IncidentState, frozenset[IncidentState]]
def assert_group_transition(current, target, *, group_ref) -> None   # raises InvalidStateTransition
```

**Why reuse `IncidentState` rather than a new enum.** Steering: "no layer defines an alternate
state vocabulary." A second enum would also need a migration and a widened CHECK constraint from
Stream C. `assuring` and `awaiting_approval` are per-task concepts and are excluded from
`GROUP_STATES`; a separate transition table keeps group and incident rules independent without
splitting the vocabulary. **Zero schema change, zero Stream C dependency.**

Group state is **derived from its members**, not independently steered:

| Group state | Rule |
| --- | --- |
| `detected` | opened, no member advanced |
| `assessing` | any member past `detected` |
| `planning` | every member has a plan |
| `executing` | any member executing or beyond |
| `resolved` | **every** member `resolved` |
| `blocked` | every member terminal, at least one not `resolved` |
| `failed` | reserved; not produced in Phase 2 |

Deriving rather than steering is what stops the group claiming completion its members have not
reached. A group is `resolved` only when all eight are — anything else is `blocked` with the reason.

### Dependencies
- **C:** none. Uses the existing column.
- **B:** none.

### Database
**None.** Existing column, existing CHECK constraint (the subset is a strict subset of it).

### Tests — `backend/tests/unit/orchestrator/test_group_state.py`, `test_group.py`
- Illegal group transition raises `InvalidStateTransition` with allowed targets in `details`.
- `resolved` requires **every** member resolved; seven of eight yields `blocked`, not `resolved`.
- One member `blocked` and the rest `resolved` → group `blocked`, reason names the member.
- Group state transitions append to `decision_log` against the group.
- `GROUP_STATES ⊆ IncidentState`, asserted mechanically so the vocabularies cannot diverge.
- The frozen `test_state_machine.py` still passes untouched.

### Acceptance criteria
1. Injecting the cascade and running it to completion moves the group `detected → … → resolved`.
2. `GET /incident-groups/{ref}` reports a state derived from members, never a stored guess.
3. A partial cascade reports `blocked`, and the response says which members are not resolved.

### Integration risks
- **Group `decision_log` rows.** `decision_log.incident_id` is nullable in both the model
  (`workflow.py:281`) and the migration, so group entries can carry `incident_id = NULL`. The
  timeline endpoint filters by `incident_id`, so group entries will not leak into an incident's
  timeline — verify with a test rather than assuming.

  **But there is no `group_id` column**, so a group entry has to be *findable* some other way.
  Plan: `correlation_id = group_reference` and `detail = {"group_reference": ...}`, queried on
  `correlation_id`. This needs no migration, and it is the same correlation field the engine
  already sets per run. A test must assert group entries are retrievable by group and invisible
  per incident.
- **`_journal` is context-bound.** `Orchestrator._journal` takes a `WorkflowContext` and reads
  `ctx.incident_id` / `ctx.correlation_id` (`engine.py:1622`). Group journalling therefore needs
  its own small writer in `group.py` rather than a widened `_journal` signature — widening it
  would put a nullable incident into the hot path of every Phase 1 call site.
- Ordering: a member finishing concurrently with a group advance. Phase 2 is single-writer per
  request, so this is a documented limitation, not a fix.

### Demo value
**Moderate on its own, high as a foundation.** It makes "8 flights, one event" a state a judge can
see rather than a caption.

---

## A2 — Network-level cascade orchestration

Phase 1 opens one incident on the worst-affected departure. Phase 2 opens the whole affected set
and drives it.

### Files
- `backend/app/orchestrator/group.py` — `open_group()`, `advance_group()`.
- `backend/app/cli.py` — `inject --cascade` to open all affected flights, default off.
- `backend/app/api/incident_groups.py` — **new** router (A8).

### Public contracts

```python
async def open_group(self, *, group_reference: str, flight_ids: Sequence[int] | None = None,
                    opened_at: datetime | None = None) -> GroupContext
async def advance_group(self, ctx: GroupContext, *, max_incidents: int | None = None) -> GroupContext
```

- Member incidents are opened through the **existing** `Orchestrator.open_incident`, so
  per-flight dedupe, the partial unique index, the incident clock and `prediction_id` all behave
  exactly as in Phase 1. **No second incident-creation path.**
- `advance_group` advances each non-terminal member by calling the existing `Orchestrator.run`.
- Affected flights come from **C's data**, not a Stream A guess: the group's airport plus recorded
  delay, the same derivation `cli._select_primary_flight` already uses, widened from one to many.

### Dependencies
- **C:** a way to list a group's affected flights. `cascade_rollup` already knows `flights_affected`;
  Stream A needs the **ids**. *Ask:* expose `group_affected_flight_ids(session, group_id)` in
  `scenario_queries`, or confirm Stream A may derive it from `flight.origin_icao == group.airport_icao`
  plus a recorded delay. Prefer C's helper — it keeps scope selection in one place.
- **B:** none.

### Database
**None.**

### Tests — `backend/tests/unit/orchestrator/test_group.py`, `backend/tests/e2e/test_cascade.py`
- Opening the cascade creates exactly one incident per affected flight, and re-running creates none.
- Each member incident references the group; every member carries `demo_dataset_id`.
- Step budget is per incident and not shared, so one long member cannot starve the others.
- `advance_group` is idempotent under a replayed `Idempotency-Key`.
- A member whose service refuses does not stop the other members; the group ends `blocked` and
  names it.
- **Postgres e2e:** the full cascade reaches the verified group figures — 8 flights, 604
  passengers, 22 connections, 9 pairings — by calling C's rollup, not by counting in Stream A.

### Acceptance criteria
1. `make demo --cascade` (or the API equivalent) opens 8 incidents and no more.
2. Driving the group to completion produces the verified group figures, unchanged from C's suite.
3. Every member incident is independently inspectable at `/incidents/{ref}` with its own timeline.

### Integration risks
- **Runtime.** 8 incidents × 3 tasks × real services is materially slower than one. If a single
  request exceeds a comfortable demo pause, `advance_group` needs `max_incidents` batching — the
  parameter is in the contract for that reason. Measure before the demo script depends on it.
- **Session and transaction size.** One request advancing 8 incidents is a long transaction.
  Mitigation: commit per member incident, as `advance` already does per step.
- **Notification volume.** 8 × 174 recipients through the communication service. Real email stays
  allowlisted, but the simulated record count grows; the honesty note must scale with it.

### Demo value
**High. This is the Phase 2 gate.** "One weather event, eight flights, nine rotations, and every
one of them individually auditable."

---

## A3 — Blast-radius calculation

**Stream A computes nothing here.** C's `cascade_rollup` already derives every figure by union with
completeness flags. Stream A's job is banding and exposure.

### Files
- `backend/app/schemas/cascade.py` — **new.** Response contracts.
- `backend/app/api/incident_groups.py` — the endpoint.
- `backend/app/orchestrator/blast_radius.py` — **new**, and deliberately thin: it maps C's rollup
  onto a severity band using configured thresholds. Nothing else.

### Public contracts

```python
class BlastRadiusBand(StrEnum):        # localised | significant | network
class BlastRadius(BaseModel):
    band: BlastRadiusBand
    rollup: CascadeRollupOut           # C's figures, passed through unchanged
    contributing_factors: list[str]    # which thresholds were crossed, and by what
    is_complete: bool                  # C's flag, surfaced not recomputed
    thresholds: dict[str, int]         # from config, so the band is explainable
```

**No magic numbers.** Thresholds come from `Settings` (steering rule 6), so a band is always
explainable as "network because flights ≥ 5 and connections ≥ 20", never asserted.

A band over an **incomplete** rollup is reported with `is_complete: false` and the band suffixed
accordingly. A partial radius must not read as a total one.

### Dependencies
- **C:** `cascade_rollup` as-is. If any field is added, this passes it through.
- **B:** none.

### Database
**None.** Derived per request. If it becomes slow, cache in Redis — not a table.

### Tests — `backend/tests/unit/orchestrator/test_blast_radius.py`
- Band is derived from configured thresholds; changing a threshold changes the band.
- An incomplete rollup never reports a complete band.
- Every contributing factor names the figure and the threshold it crossed.
- **A guard test that Stream A recomputes nothing:** an AST check that `blast_radius.py` imports
  no service and performs no aggregation over `action.payload`.

### Acceptance criteria
1. `GET /incident-groups/{ref}` includes a band, its factors and the thresholds behind it.
2. Every number in the response is traceable to C's rollup.
3. A one-incident group and an eight-incident group produce different, explainable bands.

### Integration risks
- **The temptation to recompute.** The guard test exists because "just sum the counts" is the exact
  mistake that produces 176 passengers. Reviewers should check that test first.

### Demo value
**High.** It answers "how bad is this?" in one word backed by named thresholds — and it is the
line where an uncalibrated percentage would have been easy and wrong.

---

## A4 — Candidate recovery-plan lifecycle

Today one incident has one plan, and `_current_plan` takes the latest by id. Comparison needs
several plans to coexist with a recorded selection.

### Files
- `backend/app/orchestrator/candidates.py` — **new.** Generate, score, select.
- `backend/app/orchestrator/engine.py` — `_current_plan` becomes "the selected plan", falling back
  to latest when nothing is selected, so Phase 1 behaviour is unchanged.
- `backend/app/schemas/plans.py` — **new.**

### Public contracts

```python
async def propose_candidates(self, ctx, *, count: int = 2) -> list[CandidatePlan]
async def select_candidate(self, ctx, *, plan_id: int, actor_id: str, reason: str) -> CandidatePlan
```

Candidates come from the **existing** deterministic playbook, varied along declared axes in
`playbook.py` — ordering and inclusion of optional steps — not from a new planner and not from a
model. In `LLM_MODE=off` there must still be more than one candidate, or the comparison screen is
empty in the mode the demo runs in.

### Dependencies
- **C — one small schema request.** Two columns on `plan`:
  - `selection_state` (`candidate` | `selected` | `discarded`), default `candidate`
  - `selected_at`, `selected_by` (pseudonymous actor)

  I am not adding a table. This is the minimum that makes selection auditable, and `backend/migrations/`
  is C's alone. **Fallback if C declines:** infer selection from which plan's tasks were assured.
  That is fragile — a plan with no assured task yet is indistinguishable from a discarded one — so
  the columns are strongly preferred.
- **B:** none for the lifecycle. Comparison uses B via A6.

### Database
One migration, **owned by C**: two nullable columns plus one enum-ish string with a CHECK, and an
index on `(incident_id, selection_state)`.

### Tests — `backend/tests/unit/orchestrator/test_candidates.py`
- Two candidates for one incident; both persisted; exactly one selectable.
- Selecting records actor and reason, and is immutable — a second, different selection is a `409`,
  matching how `human_decision` already behaves.
- With `LLM_MODE=off`, `propose_candidates` returns ≥ 2.
- Phase 1 regression: an incident with a single unselected plan behaves exactly as before.
- A discarded plan's tasks are never assured or executed.

### Acceptance criteria
1. An incident can hold multiple candidate plans, with one selected and the choice attributed.
2. Selection appears in the timeline attributed to `human` when an operator chose it — reusing the
   attribution model fixed in Phase 1, not a new one.
3. No behaviour change for a single-plan incident.

### Integration risks
- **The migration is the critical path.** If C cannot land it early, A4 and A5 slip together.
  Raise it in the first Phase 2 sync.
- **`_current_plan` is load-bearing.** It is called from the run loop, the API and the CLI.
  Changing its meaning risks a Phase 1 regression; the fallback keeps it identical when nothing is
  selected, and the regression test above locks that.

### Demo value
**Moderate.** Valuable mainly as the substrate for A5.

---

## A5 — What-if as plan comparison

**This is what-if under [P2-D2](DECISIONS.md#p2-d2--what-if-is-in-scope-bounded-to-zero-write-deterministic-re-evaluation):
a bounded, zero-write, deterministic re-evaluation.** Compare candidate plans against the **same
recorded facts**. No projected world state, no simulation engine, nothing persisted.

P2-D2's three properties map onto this item exactly, which is worth stating so a reviewer can check
them one by one:

| P2-D2 property | How A5 satisfies it |
| --- | --- |
| **Re-evaluation** of the same recorded facts | Calls B's existing `gate.evaluate` over evidence already stored; computes no new facts |
| **Zero-write** | Persists no `assurance_evaluation`, no `action`, no state change, no `decision_log` row. Asserted by a row-count test |
| **Bounded** — varies only declared inputs | Varies candidate plan shape along axes declared in `playbook.py`, and nothing else. World state is never an input |

### Files
- `backend/app/orchestrator/candidates.py` — `compare_candidates()`.
- `backend/app/schemas/plans.py` — comparison contract.
- `backend/app/api/plans.py` — **new** router.

### Public contracts

```python
class CandidateComparison(BaseModel):
    incident_reference: str
    candidates: list[CandidateSummary]   # per plan: tasks, gate outcome per task,
                                         # actions that would need a human, evidence refs
    differences: list[str]               # the axes on which they actually differ
    basis: Literal["recorded_evidence"]  # never "projected"
    not_a_forecast: str                  # rendered verbatim by the UI
```

**What it does:** for each candidate, ask B's gate what each task *would* return, given the
evidence already recorded. Report which tasks would execute, which would need a human, and which
would be refused.

**What it must never do:** execute anything, project a future state, or claim an outcome.
`basis` is a literal for exactly that reason — the contract cannot express a projection.

### Dependencies
- **B:** `gate.evaluate` as it already exists, called in a read-only path. **No new B contract.**
  Worth confirming with B that a dry-run evaluation carries no side effect — it currently does not,
  since `AssuranceResult` is returned rather than persisted, and Stream A must not persist it.
- **C:** none beyond A4's migration.

### Database
**None.** A dry-run evaluation is deliberately **not** persisted as an `assurance_evaluation`. Only
a real authorisation gets a row, or the invariant "no action without an evaluation" inverts into
"evaluations that authorised nothing", and the audit trail stops meaning one thing.

### Tests — `backend/tests/unit/orchestrator/test_candidates.py`
- Comparing candidates writes **no** `assurance_evaluation`, **no** `action`, **no** state change.
  This is the load-bearing test.
- A high-risk task shows as "would need approval" in both candidates.
- Differences list only real differences; identical candidates report none.
- The response cannot claim a projection: `basis` is fixed and `not_a_forecast` is non-empty.

### Acceptance criteria
1. Two candidates for the storm incident, side by side, each showing per-task gate outcomes.
2. Comparing changes nothing: same row counts before and after, asserted.
3. No response field can be read as a predicted outcome.

### Integration risks
- **Scope creep into the deferred simulation engine.** The `basis` literal and the no-write test
  are the guardrails. If a reviewer asks for projected delay minutes, that is Option B and a
  steering change.
- **Gate cost.** A comparison runs 2 × N evaluations per request. Measure; batch if needed.

### Demo value
**High, and cheap.** "Here are two ways to recover this flight, and here is what the gate says
about each — before anyone touches anything." It demonstrates bounded autonomy without claiming
prediction.

---

## A6 — Group-level assurance invocation boundary, and plan approval

**The most dangerous item in this plan, and the one the team's decisions changed most.** Handle
with care.

**Rewritten for P2-D1 and P2-D3** ([`DECISIONS.md`](DECISIONS.md#phase-2-architecture-decisions--final)).
My draft said a plan-level summary authorises *nothing*, structurally. Two decisions overrode that:

- **P2-D1: the scope is the incident group**, not one incident's plan. So this is a *group* summary.
- **P2-D3: a plan approval may authorise low and medium risk actions.** So it is no longer purely a
  report. The `authorises_nothing: Literal[True]` field is **removed** — keeping it would have been
  a lie in the type system, which is worse than not having it.

That makes this a real authorisation scope, so the boundary has to be enforced by something other
than "it grants nothing". The replacement is a narrow, testable predicate.

### Files
- `backend/app/orchestrator/group_assurance.py` — **new** (renamed from `plan_assurance.py`: the
  scope is the group, and the filename should not outlive the decision).

### Public contracts

```python
class GroupAssuranceSummary(BaseModel):
    group_reference: str                      # P2-D1: group, not plan
    per_incident: list[IncidentGateOutcome]   # each with its selected plan's per-task outcomes
    would_execute: int
    would_need_human: int
    would_be_refused: int
    #: P2-D3: exactly the actions a single plan approval could authorise. Never high-risk,
    #: never a task with a failed check. Empty is a normal and common answer.
    approvable_task_ids: list[int]
    #: Every task excluded from the above, each with the reason it cannot be covered.
    not_approvable: list[NotApprovableTask]    # reason: HIGH_RISK | FAILED_CHECK
```

**The P2-D3 predicate, stated exactly.** A plan approval may satisfy `needs_human` for a task
**only** when both hold:

1. `result.risk_tier in {low, medium}` — high always needs its own action-level approval; and
2. **no** check in `result.checks` has `state == failed` — approval covers risk, never failed
   evidence, stale sources, unresolved entities or policy failure.

Everything else still requires an action-level approval against **its own** persisted
`assurance_evaluation`, exactly as in Phase 1.

> **The full mechanism lives in
> [`34-phase2-contract-alignment.md` §2](34-phase2-contract-alignment.md#2-single-source-of-truth-for-plan-approval-and-human-decision-scope),
> which is the citable source.** In short: `human_decision`, one row per evaluation, remains the
> **only** thing `execute()` consults — it never learns that plan approvals exist. A plan approval
> **partitions the evaluations already awaiting** a human, using one predicate in
> `approval_scope.py`, and writes one ordinary `human_decision` per covered evaluation; the excluded
> ones are returned with a reason and stay awaiting. It never covers an evaluation produced later in
> the run — forward coverage would be a blank cheque. That is what keeps a single path to execution.

**A finding the team should see before this is built.** Reading `gate.py:274–316`, a low or medium
tier action currently reaches `needs_human` by only two routes:

| Route | Gate rule | Approvable under P2-D3? |
| --- | --- | --- |
| A check `FAIL`ed | rule 2 | **No** — P2-D3 excludes failed evidence |
| An **unpermitted `WARN`** | rule 4 | **Yes** — a warning is not a failure |
| High tier with all checks passing | rule 3 | **No** — fires only for `high` |

So under today's gate, P2-D3's authorising domain is exactly **"an unpermitted warning on a
low/medium action"**. That is a real, non-empty domain and I am planning to it — but it is narrower
than "plan approval covers low/medium actions" sounds, and if the team meant something broader
(for example, letting a plan approval pre-authorise actions the gate would already `execute`, as a
click-reduction rather than a permission), that is a different feature and needs saying. **Listed
as an open question in §4.**

Because a materialised decision is an ordinary `human_decision`, the Phase 1 attribution model
carries through untouched: the covering id is stamped on the action exactly as an action-level
approval is, and reads as `actor_kind=human`. **No second audit model, and no change to
`execute()`.**

### Dependencies
- **B:** existing `gate.evaluate`. No new contract. Confirm repeated evaluation is side-effect free.
- **C:** none beyond A4's migration.

### Database
The summary itself persists **nothing** (P2-D2). A plan *approval* does, and it needs a Stream C
migration: a `plan_approval` table plus `human_decision.scope` and `plan_approval_id` with two CHECK
constraints. **Full DDL in
[`34-phase2-contract-alignment.md` §2.5](34-phase2-contract-alignment.md#25-database-changes-required-owned-by-stream-c).**
`human_decision.assurance_id` stays `UNIQUE NOT NULL` — Phase 2 does not relax the constraint that
makes the Phase 1 audit trail trustworthy.

### Tests — `backend/tests/unit/orchestrator/test_group_assurance.py`
- **The load-bearing test:** a high-risk task is **never** in `approvable_task_ids`, and executing
  it after a plan approval still raises `AssuranceBlocked` until its own approval exists.
- A task with any `failed` check is never approvable, **at every tier including low** — asserted per
  tier, because "low risk" is the tier most likely to tempt a shortcut.
- An unpermitted-warn low/medium task **is** approvable, so P2-D3 is proven non-vacuous.
- Fetching a summary persists nothing: row counts identical before and after.
- An action authorised by a plan approval carries that `human_decision_id` and reads as
  `actor_kind=human` in both the timeline and replay.
- Extend the frozen guard: `execute()` remains reachable only via a persisted evaluation.

### Acceptance criteria
1. One group-scoped summary covers every member incident's selected plan.
2. A plan approval executes the low/medium actions it covers, and **not one high-risk action**.
3. No `FAIL` is ever approvable at plan level, at any tier.
4. Every Phase 1 assurance invariant still holds, existing suite unchanged.

### Integration risks
- **There is still exactly one path to execution**, and that is deliberate. `execute()` reads only
  `human_decision`; a plan approval can do nothing but cause such a row to exist. If a reviewer sees
  `execute()` — or anything other than `approval_scope.py` — import `PlanApproval`, that is a
  finding regardless of the surrounding code. An AST guard test asserts it.
- **The predicate is the whole risk surface.** It lives in one function with one name, and every
  excluded task carries a reason token. If the tier check appears without the failed-check check, or
  either is inlined at a call site, that is a finding.
- **Blast radius of getting the predicate wrong.** A bug here silently executes something a person
  did not authorise — the worst failure this system can have. It deserves the hardest review in
  Phase 2 and should not be the item that gets rushed if time runs short (hence its late position
  in the order).

### Demo value
**High.** "Approve the recovery for the whole network event in one action — and watch it still stop
at the one action that needs a person." Bounded autonomy is easier to *show* than to assert, and
P2-D3 makes the boundary visible rather than theoretical.

---

## A7 — Replay orchestration

Cheapest high-value item in the plan. `decision_log` already holds the full chronology, so replay
is a read, not a subsystem — exactly as `08-blueprint-backlog.md` concluded.

### Files
- `backend/app/api/replay.py` — **new** router.
- `backend/app/schemas/replay.py` — **new.**
- `backend/app/api/actors.py` — **new**, tiny: `_actor_kind` moved here from `incidents.py` and
  imported by both. A move, not a copy.
- No orchestrator change. **No new state.**

### Public contracts

```python
GET /api/v1/incidents/{ref}/replay        -> ReplayResponse
GET /api/v1/incident-groups/{ref}/replay  -> ReplayResponse

class ReplayFrame(BaseModel):
    sequence: int
    occurred_at: datetime
    stage: str
    actor: str
    actor_kind: str          # the Phase 1 fix: a human decision reads as human
    event_type: str
    summary: str
    state_before: IncidentState | None
    state_after: IncidentState | None
    evidence_refs: list[str]
    assurance_id: int | None
    human_decision_id: int | None
    detail: dict[str, Any]
```

Read-only, ordered, complete. Frames are `decision_log` rows enriched with the assurance and human
decision each step referenced, so a reviewer can open any frame and see what authorised it.

**Every field is derived from columns that already exist.** No migration, and worth stating
precisely because three of these fields are not columns:

| Frame field | Source |
| --- | --- |
| `sequence` | **Derived**: ordinal position in the `(occurred_at, id)` ordering. `decision_log` has no sequence column, and adding one would need a C migration for no gain |
| `state_before` / `state_after` | `detail["from"]` / `detail["to"]` on `STATE_CHANGED` rows (`engine.py:1562`); `null` on every other event type |
| `assurance_id`, `human_decision_id` | `detail` keys the engine already writes on assurance and action events |
| `actor_kind` | **Reuse `_actor_kind`** from `app/api/incidents.py:104`. Do **not** re-implement the mapping — two copies would drift, and the Phase 1 human-attribution fix depends on exactly one mapping. Promote it to a shared module and have both routers import it |
| everything else | `decision_log` columns as-is |

Because `sequence` is positional, "no gaps in `sequence`" is a property of the response, not a
claim about the table. The test should assert contiguity of the emitted frames.

### Dependencies
- **C:** none. **B:** none. Pure Stream A over existing rows.

### Database
**None.**

### Tests — `backend/tests/e2e/test_replay.py`
- Replay of the completed storm incident returns frames in order with no gaps in `sequence`.
- The approval frame carries `actor_kind: human`, the operator id and the assurance id — the
  Phase 1 fix visible through replay.
- Every `ACTION_COMPLETED` frame carries a non-null `assurance_id`.
- Replay is genuinely read-only: row counts identical before and after.
- Group replay interleaves member incidents in true chronological order.
- Timeline and replay report the **same** `actor_kind` for the same row — asserted by comparing the
  two endpoints, which is what makes the shared mapper load-bearing rather than tidy.

### Acceptance criteria
1. Replay reconstructs the full run for one incident and for the group.
2. Every authorising record is reachable from the frame that used it.
3. D's existing `/replay/:incidentId` route has a real contract to bind to.

### Integration risks
- **Low.** The only real risk is group replay ordering across members with near-identical
  timestamps; tie-break on `(occurred_at, id)` and test it.

### Demo value
**Very high per unit of effort.** "Show me how you got there" answered from the immutable record,
with the human's decision visibly a human's. It is also the strongest answer to a judge asking
how any of this is auditable.

---

## A8 — The group, plan and replay endpoints

Two of the six remaining fixture routes become real, plus six new ones. The other four fixture
routes (`/flights`, `/sources`, `/incidents/{id}/policy`, `/reports/{id}`) are **not** Stream A's
in Phase 2 — `/policy` and `/reports` belong to B and D respectively.

### Files
- `backend/app/api/incident_groups.py` — **new.** Replaces both fixture group routes.
- `backend/app/api/plans.py` — **new.**
- `backend/app/api/replay.py` — **new.**
- `backend/app/api/fixtures_router.py` — delete each replaced route **in the same commit**.
- `backend/app/api/__init__.py` — register, update the status table.
- `backend/app/schemas/{cascade,plans,replay}.py` — **new.**

### Public contracts

| Method | Path | Response model | Replaces |
| --- | --- | --- | --- |
| `GET` | `/incident-groups` | `IncidentGroupListResponse` | fixture |
| `GET` | `/incident-groups/{ref}` | `IncidentGroupDetailResponse` | fixture |
| `POST` | `/incident-groups/{ref}/run` | `GroupRunResponse` | new |
| `GET` | `/incidents/{ref}/plans` | `CandidatePlansResponse` | new |
| `POST` | `/incidents/{ref}/plans/{id}/select` | `CandidatePlanResponse` | new |
| `GET` | `/incidents/{ref}/plans/comparison` | `CandidateComparison` | new |
| `GET` | `/incidents/{ref}/replay` | `ReplayResponse` | new |
| `GET` | `/incident-groups/{ref}/replay` | `ReplayResponse` | new |

Every one declares a Pydantic `response_model` — the `add-api-endpoint` skill requires it, and the
remaining fixture routes render as `"string"` precisely because they do not.

**Shape compatibility is a Stream C conversation.** `fixtures/api/incident_group_detail.json` is
C's and D renders it directly. The existing fixture carries `rollups`, `flights`, `crew_pairings`,
`mechanism_legend`, `why_nine_not_eight` and `provenance`. The real endpoint must stay
byte-compatible or C changes the fixture and D updates its type — announced, not drifted.

`why_nine_not_eight` is a fixture field carrying an explanation. Two options for review: keep it
as recorded copy, or derive it from the mechanism counts. **Recommend deriving it**, so the
sentence cannot contradict the data it explains — but that is a change to C's fixture and needs
their agreement.

### Dependencies
- **C:** fixture shape confirmation; `group_affected_flight_ids`; the `plan` migration.
- **B:** confirmation that dry-run evaluation is side-effect free.
- **D:** the new types, and a note that `/incident-groups/{ref}` gains a blast-radius block.

### Database
**None** beyond A4's migration.

### Tests
- `backend/tests/e2e/test_cascade_api.py` — every endpoint, typed response, error envelope,
  `Idempotency-Key` on both mutations.
- OpenAPI: each new path resolves to a component schema, never `"string"`.
- The replaced fixture routes are gone — no path served twice.
- `tests/contract/test_api_shapes.py` still passes **unchanged**.

### Acceptance criteria
1. Both fixture group routes become real; none is served by both implementations at any commit.
2. `docs/openapi.json` regenerated; no new endpoint renders as `"string"`.
3. D can drive the cascade, comparison and replay screens with no fixture fallback.

### Integration risks
- **Byte-compatibility with C's fixture** is the highest-churn risk here. Resolve the shape before
  writing the endpoint, not after.
- **`/incident-groups/{id}` path parameter.** The fixture accepts a reference or an alias. Keep
  accepting both, as `/incidents/{ref}` already does.

### Demo value
**High.** This is what turns three placeholder screens into the cascade story.

---

## A9 — Stream D's dependency asks (PR #40)

Stream D published their Phase 2 plan as **PR #40** (`frontend/docs/phase-2-stream-d-plan.md`).
Five of their seven dependency asks are Stream A's. Answering them here rather than discovering
them mid-implementation, because three are small and unblock four of D's seven features.

| D's ask | Verdict | Where in this plan |
| --- | --- | --- |
| **D1** `payload` on `ActionSummary`, or an action-detail endpoint | **Yes — the endpoint, not the field.** See below | A9 |
| **D2** Wire `/incident-groups/*` to `cascade_rollup()` | **Yes, already planned** | A3, A8 |
| **D4** A plan-alternatives contract | **Yes** | A4, A5 |
| **D5** A what-if contract | **Partly — and D and I independently reached the same limit** | §0.1, A5 |
| **D6** `incident_reference` on the group's `flights[]` | **Yes. Trivial and clearly right** | A9 |
| **D7** `reason_code` on `ActionSummary` | **Yes, and it is nearly free** | A9 |

### D1 — expose the recorded payload, but not on `ActionSummary`

D is right that the gap is real: the services compute per-entity impact, the engine persists it in
`action.payload` (`engine.py:958`), and `ActionSummary` sets `extra="forbid"` with no `payload`
field, so the UI genuinely cannot reach it. Parsing counts out of `reason` prose would be
fabrication, and D was right to refuse.

But I do **not** want `payload` on `ActionSummary`:

- `ActionSummary` appears in list responses. Inlining an unbounded service dict makes every incident
  detail response carry every service's internal structure.
- `action.payload` is **service-shaped and unversioned**. Exposing it verbatim silently promotes
  each service's private dict to a public API contract — and those dicts are Stream C's to change.

**Proposal:** `GET /api/v1/incidents/{ref}/actions/{id}` → `ActionDetailResponse`, which is
`ActionSummary` plus a typed `payload` block and the provenance for it. One place to shape the
payload, one place to version it, and `ActionSummary` stays lean.

```python
class ActionDetailResponse(ActionSummary):
    payload: dict[str, Any]          # recorded verbatim; documented as service-shaped
    payload_schema_version: int      # so D can branch instead of guess
    source_timestamps: list[SourceTimestamp]
```

If D prefers the inline field after reading this, that is a reasonable disagreement and cheap to
change — but it should be a decision, not a default.

### D6 — `incident_reference` on the group's `flights[]`

Agreed without reservation. D wants to click a cascade node and land on that flight's workspace;
without the reference there is no join. The group's `flights[]` currently carries
`id, flight_number, route, delay_minutes, passengers, state` and no incident reference.

Note this is a **fixture-shape change and therefore a Stream C conversation** (A8), and it is
additive, so D's existing type keeps compiling. Nullable, because a flight in the group's blast
radius may have no incident open yet — and that null is meaningful, not missing: it means
"affected, not yet being worked".

### D7 — `reason_code` on `ActionSummary`

**This one is nearly free, because the value is already recorded.** `dispatch.py:78` writes
`payload={"reason_code": SERVICE_NOT_IMPLEMENTED, ...}` and services set their own
(`service_registry.py:77` writes `SERVICE_INPUTS_UNAVAILABLE`). D is prefix-matching on `reason`
prose today only because the structured value is buried one level down.

So D7 is a **promotion of an existing field**, not a new contract: lift `reason_code` out of
`payload` onto `ActionSummary` as `str | None`. Add it to `ActionSummary` proper — unlike `payload`
it is a short bounded token, it is exactly what a list view needs, and refusal copy is a list-view
concern.

### D5 — **settled by P2-D2**

D asked for a what-if contract. P2-D2 now defines exactly what one may contain: a zero-write
deterministic re-evaluation, and nothing projected. So D5 is answered, with a caveat worth stating
plainly to D.

D's plan concluded that *operational* what-if has no endpoint and that a UI computing outcomes would
break "the UI never calculates", recommending the existing **policy-cause comparison** instead. My
§0.1 reached the same limit from the scope records. P2-D2 ratified it. Two streams reasoning from
different evidence — D from the response schemas, A from the scope records — landed where the
decision landed.

**The caveat:** P2-D2 gives D a *plan-comparison* what-if (A5), not the operational what-if their
UI sketch would need. Those are different screens. D should confirm A5's contract satisfies their
what-if surface, because "what-if is in scope" could easily be read as more than P2-D2 grants.

### Files
- `backend/app/schemas/incidents.py` — `reason_code` on `ActionSummary`; new `ActionDetailResponse`.
- `backend/app/api/incidents.py` — the action-detail route.
- `backend/app/schemas/cascade.py` — `incident_reference` on the group flight entry.

### Dependencies
- **C:** the `flights[]` fixture shape for D6. **B:** none. **D:** confirm D1's endpoint-versus-field
  choice.

### Database
**None.** All three read columns that already exist.

### Tests
- `reason_code` is populated for a refused action and matches `payload["reason_code"]` — asserted
  equal, so the promoted field cannot drift from the recorded one.
- The action-detail endpoint returns the payload for an executed action and 404s for an unknown id.
- `incident_reference` is null for an affected flight with no open incident, and set once opened.
- `ActionSummary`'s `extra="forbid"` still holds; adding a field does not loosen it.
- `tests/contract/test_api_shapes.py` passes unchanged.

### Acceptance criteria
1. D can render per-entity passenger, connection and hotel impact without parsing prose.
2. D can navigate from a cascade node to that flight's workspace.
3. D can render refusal copy from a token, not a string prefix.

### Integration risks
- **`extra="forbid"` cuts both ways.** It means an additive field is safe for D's parser, but also
  that any field D expects and A omits is a hard failure rather than a null. Coordinate the two
  schema changes in one commit and tell D the commit.
- **`payload` is Stream C's shape.** The moment it is exposed, a C service refactor becomes a UI
  break. `payload_schema_version` exists so that break is detectable rather than mysterious.

### Demo value
**High, and disproportionate to the effort.** D1, D6 and D7 are perhaps a day of Stream A work and
they unblock four of D's seven Phase 2 features. On the current contracts, D's impact views cannot
show a single real per-passenger figure.

---

## 3. Sequencing

**The order and the per-item C/B dependency mapping now live in
[`34-phase2-contract-alignment.md` §3](34-phase2-contract-alignment.md#3-a1a9--canonical-items-dependencies-and-order),
which is the single source of truth for both.** Kept there rather than here so there is one table to
confirm and no chance of two orders drifting apart.

The summary:

```
A9  →  A7  →  A1  →  A2  →  A3  →  A8  →  A4  →  A6  →  A5
```

- **A9 first** for a scheduling reason, not a technical one: it is the only item where another stream
  is blocked on Stream A right now. Everything else here blocks only me.
- **A6 deliberately late.** Under P2-D3 it is the only item that can execute something a person did
  not individually authorise, so it is built once the surrounding invariants are already under test —
  never as the rushed last item.
- **Only three Stream C artefacts gate Stream A:** one query helper (A2) and two migrations
  (A4, A6). Enumerated in §3.1 of that document.
- **Cut from the bottom:** A5, A6, A4 — and before any of them, the Open-Meteo/historical provider
  expansion (§0.3), already designated first out. **A9→A8 alone deliver the documented Phase 2
  gate.**

**The `C2-N` labels are resolved.** Stream D's merged plan (#44) binds them as shared Phase 2
*feature slots* — not Stream C's items, and not a renumbering of A1–A9. The mapping, and the
reconciliation of A's order with the mandated `C2-N` order, are at
[§3.3](34-phase2-contract-alignment.md#33-c2-n--resolved-they-are-shared-phase-2-feature-slots-published-by-stream-d)
and [§3.4](34-phase2-contract-alignment.md#34-reconciling-as-order-with-the-mandated-c2-n-order).

The consequence to know: **the mandate front-loads A6 to its third slot**, so the highest-risk item
in Phase 2 is built early rather than last. Guard-tests-first is the mitigation.

## 4. What I need from the other streams before writing code

**The full confirmation checklist — 17 rows across B, C, D and the team — is
[`34-phase2-contract-alignment.md` §4](34-phase2-contract-alignment.md#4-confirmations-required-before-implementation).**
Implementation is authorised only once those are ticked, so that list is the gate and this is a
pointer to it.

The four that block the most:

| Stream | Ask | Blocks |
| --- | --- | --- |
| **C** | Fill the `C2-N` binding table, or confirm the three A-gating artefacts land before A slots 4, 7 and 8 | **all implementation** |
| **C** | The `plan_approval` + `human_decision.scope` migration, with both CHECK constraints | A6 |
| **B** | `gate.evaluate` is side-effect free, and the P2-D3 predicate is correctly stated against the gate | A5, A6 |
| **Team** | The single source of truth in §2 of that document: `human_decision` per evaluation is the sole authority | A6 |

**Settled since the first draft:** §0.1 what-if (P2-D2), the group scope of plan-level assurance
(P2-D1), plan-approval risk coverage (P2-D3), the Open-Meteo cut (§0.3), and the incident-vs-group
scope contradiction (§1 of the alignment doc). Those are decisions now, not asks.

## 5. Invariants a reviewer should check first

Before reading any Phase 2 code, check these. Each is a way this plan could go wrong quietly.

1. `uq_incident_active_per_flight` untouched; one active incident per flight.
2. No group figure computed by summing per-incident counts. Unions only, via C's rollup.
3. `execute()` still requires a persisted `assurance_evaluation`, and an approved `human_decision`
   where the gate demanded one. Under P2-D3 that approval may be a **plan** approval — but only for
   a low/medium tier action with **no failed check**. Check both halves of that predicate.
4. **No high-risk action is ever covered by a plan approval** (P2-D3). This is the single most
   important line in Phase 2.
5. No `FAIL` is approvable at plan level, at any tier. Fail-closed is not delegable.
6. A `GroupAssuranceSummary` persists nothing; only a real approval does.
7. Dry-run evaluation and plan comparison persist nothing (P2-D2, zero-write).
8. No projected or forecast figure in any response. `basis` stays `recorded_evidence` (P2-D2).
6. Thresholds and bands come from config, never a literal.
7. The six frozen guard tests unmodified (`docs/28-parallel-workstreams.md:108`).
8. Every new endpoint has a `response_model`; no schema renders as `"string"`.
9. Exactly one `actor_kind` mapping in the codebase. Two would let replay and the timeline disagree
   about whether a human authorised something, which is the one thing Phase 1 closed.
