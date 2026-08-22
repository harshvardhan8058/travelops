# 33. Phase 2 — Stream A Implementation Plan

Full Disruption Intelligence, from the Core & API side. **Planning only. No code until all four
stream plans are reviewed.**

Written against `main` at `5052388`. Phase 1 is closed: the deterministic slice runs to `resolved`
on the demo machine, and an operator approval is attributed to a person.

---

## 0. Two things to settle before any of this is built

### 0.1 One scope item contradicts a settled decision

**"What-if simulation branches" is currently out of scope by three separate records.**

| Record | Says |
| --- | --- |
| `.kiro/steering/travelops.md` | "Deferred: digital twin, **simulation engine**, voice, knowledge graph visualisation" |
| `DECISIONS.md:300` | Simulation engine listed under "Nice to have" |
| `08-blueprint-backlog.md:85` | "#15–#17 (replay engine, digital twin, simulation engine) … product-scale features … post-hackathon roadmap **unless one of them *is* the demo**. … the replay engine is now nearly free. **The digital twin and simulation engine are not.**" |

Steering is shared law across all four sessions and changes only with the team's agreement, so I
am not planning past it unilaterally. There are three ways forward and the team should pick one:

**Option A — scope it down to something that is not a simulation engine.** *Recommended.*
A simulation engine models future world state and propagates hypothetical events through a twin.
What the demo actually needs is narrower: **compare two or more candidate recovery plans against
the same recorded facts**, using the deterministic services that already exist. No world model, no
projected weather, no invented future. That is item **A2.5** below, and it delivers the "what if we
did it differently" beat without the deferred subsystem.

**Option B — amend steering deliberately.** If the team wants true what-if projection, that is a
steering change plus a new subsystem, and per `08-blueprint-backlog.md` it only earns its place if
it *is* the demo. My view: it is not, and Phase 2's demo claim is already strong.

**Option C — defer it to Phase 4/5.** Also defensible.

**Stream D reached the same boundary independently.** Their Phase 2 plan (PR #40) marks operational
what-if as having no endpoint and warns that a UI computing outcomes would break "the UI never
calculates", recommending the existing policy-cause comparison instead. Two streams, different
evidence, same limit — see A2.9 §D5.

Everything else in this plan assumes **Option A**. If the team picks B, A2.5 grows substantially
and the estimate below is wrong.

### 0.2 "Phase 2" means something narrower in `20-phased-delivery.md`

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
| Candidate plan lifecycle and comparison | 4–5 | Yes, in the reduced form of A2.5 |
| What-if projection | Deferred | **No** — see 0.1 |

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
- **The gate authorises actions, not groups and not plans.** Nothing in A2.6 may become a second
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

## A2.1 — Disruption-group lifecycle

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

## A2.2 — Network-level cascade orchestration

Phase 1 opens one incident on the worst-affected departure. Phase 2 opens the whole affected set
and drives it.

### Files
- `backend/app/orchestrator/group.py` — `open_group()`, `advance_group()`.
- `backend/app/cli.py` — `inject --cascade` to open all affected flights, default off.
- `backend/app/api/incident_groups.py` — **new** router (A2.8).

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

## A2.3 — Blast-radius calculation

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

## A2.4 — Candidate recovery-plan lifecycle

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
- **B:** none for the lifecycle. Comparison uses B via A2.6.

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
- **The migration is the critical path.** If C cannot land it early, A2.4 and A2.5 slip together.
  Raise it in the first Phase 2 sync.
- **`_current_plan` is load-bearing.** It is called from the run loop, the API and the CLI.
  Changing its meaning risks a Phase 1 regression; the fallback keeps it identical when nothing is
  selected, and the regression test above locks that.

### Demo value
**Moderate.** Valuable mainly as the substrate for A2.5.

---

## A2.5 — Plan comparison (the reduced "what-if")

**This is Option A from §0.1.** Compare candidate plans against the **same recorded facts**. No
projected world state, no simulation engine.

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
- **C:** none beyond A2.4's migration.

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

## A2.6 — Plan-level assurance invocation boundary

**The most dangerous item in this plan.** Handle with care.

The gate authorises **actions**. A plan-level pre-check must aggregate, never authorise.

### Files
- `backend/app/orchestrator/plan_assurance.py` — **new.**

### Public contracts

```python
class PlanAssuranceSummary(BaseModel):
    plan_id: int
    per_task: list[TaskGateOutcome]
    would_execute: int
    would_need_human: int
    would_be_refused: int
    authorises_nothing: Literal[True]    # structural, not a comment
```

- Calls B's gate once per task and **aggregates for display**.
- Grants no permission. `execute()` continues to require its own per-action evaluation and, where
  the gate said `needs_human`, an approved `human_decision` for **that** evaluation.
- `authorises_nothing: Literal[True]` is in the contract so no caller can misread the object, and
  so a reviewer can find the boundary by grepping for it.

### Dependencies
- **B:** existing `gate.evaluate`. No new contract. Confirm with B that repeated evaluation of the
  same task is free of side effects.
- **C:** none.

### Database
**None**, for the reason in A2.5.

### Tests — `backend/tests/unit/orchestrator/test_plan_assurance.py`
- A summary saying `would_execute: 3` does **not** let `execute()` run without a per-action
  evaluation. Asserted by calling `execute()` after a summary and expecting `AssuranceBlocked`.
- A summary persists nothing.
- Extend the frozen guard: `execute()` remains reachable only via a persisted evaluation.
- A `needs_human` task in the summary still requires an approved decision at execution.

### Acceptance criteria
1. A plan summary can be fetched before execution and grants nothing.
2. Every Phase 1 assurance invariant still holds, verified by the existing suite unchanged.
3. A reviewer can find the boundary in one grep.

### Integration risks
- **This is where a second authorisation path would be introduced by accident.** Mitigation: the
  `Literal[True]` field, the "summary then execute is still blocked" test, and an explicit line in
  the PR checklist. If a reviewer sees `execute()` consulting a `PlanAssuranceSummary`, that is a
  finding regardless of the surrounding code.

### Demo value
**Moderate directly, high defensively.** It is the answer to "so the system decides in bulk?" —
no; it *reports* in bulk and authorises one action at a time.

---

## A2.7 — Replay orchestration

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

## A2.8 — The group, plan and replay endpoints

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
**None** beyond A2.4's migration.

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

## A2.9 — Stream D's dependency asks (PR #40)

Stream D published their Phase 2 plan as **PR #40** (`frontend/docs/phase-2-stream-d-plan.md`).
Five of their seven dependency asks are Stream A's. Answering them here rather than discovering
them mid-implementation, because three are small and unblock four of D's seven features.

| D's ask | Verdict | Where in this plan |
| --- | --- | --- |
| **D1** `payload` on `ActionSummary`, or an action-detail endpoint | **Yes — the endpoint, not the field.** See below | A2.9 |
| **D2** Wire `/incident-groups/*` to `cascade_rollup()` | **Yes, already planned** | A2.3, A2.8 |
| **D4** A plan-alternatives contract | **Yes** | A2.4, A2.5 |
| **D5** A what-if contract | **Partly — and D and I independently reached the same limit** | §0.1, A2.5 |
| **D6** `incident_reference` on the group's `flights[]` | **Yes. Trivial and clearly right** | A2.9 |
| **D7** `reason_code` on `ActionSummary` | **Yes, and it is nearly free** | A2.9 |

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

Note this is a **fixture-shape change and therefore a Stream C conversation** (A2.8), and it is
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

### D5 — the what-if limit, reached twice independently

Worth flagging for the review: D's plan concluded that operational what-if has no endpoint and that
a UI computing outcomes would break "the UI never calculates", and recommended the **policy-cause
comparison** that already exists instead. My §0.1 concluded, from the steering and backlog side,
that a simulation engine is deferred and that plan comparison over recorded evidence is the honest
substitute.

Two streams reasoning from different evidence — D from the response schemas, A from the scope
records — landed on the same boundary. That is the strongest argument in this document for
**Option A**, and the team should weigh it as such.

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

Ordered by dependency, then by demo value per unit of effort.

| # | Item | Blocked by | Notes |
| --- | --- | --- | --- |
| 1 | A2.9 D's contracts (D1, D6, D7) | nothing | **Start here.** Smallest item, unblocks four of D's seven features, and D is otherwise idle-blocked on it |
| 2 | A2.7 Replay | nothing | No dependencies, no schema, and D's `/replay/:incidentId` route is a placeholder today |
| 3 | A2.1 Group lifecycle | nothing | Foundation for everything cascade |
| 4 | A2.2 Cascade orchestration | A2.1, C's flight-id helper | The Phase 2 gate |
| 5 | A2.3 Blast radius | A2.2 | Thin once the rollup is wired |
| 6 | A2.8 Group APIs | A2.1–A2.3, C's fixture shape | Unblocks D's cascade screen |
| 7 | A2.4 Candidate lifecycle | **C's migration** | Slips if the migration slips |
| 8 | A2.6 Plan assurance boundary | A2.4 | Review this one hardest |
| 9 | A2.5 Comparison | A2.4, A2.6 | The reduced what-if |

A2.9 goes first for a scheduling reason, not a technical one: it is the only item where **another
stream is blocked on Stream A right now**. Everything else in this plan blocks only me.

**If Phase 2 has to be cut**, cut from the bottom: 9, 8, 7. Items 1–6 deliver the documented
Phase 2 gate — "one weather event at BLR produces a traceable multi-flight, multi-pairing impact
set" — on their own. Losing 7–9 costs the comparison screen, not the cascade story.

## 4. What I need from the other streams before writing code

| Stream | Ask | Blocks |
| --- | --- | --- |
| **C** | `group_affected_flight_ids(session, group_id)`, or permission to derive scope in Stream A | A2.2 |
| **C** | Migration: `plan.selection_state`, `plan.selected_at`, `plan.selected_by` | A2.4, and therefore A2.5–A2.6 |
| **C** | Confirm `fixtures/api/incident_group_detail.json` shape, and whether `why_nine_not_eight` may be derived | A2.8 |
| **B** | Confirm `gate.evaluate` is side-effect free so it is safe to call in a dry run | A2.5, A2.6 |
| **C** | `flights[]` in the group fixture gains a nullable `incident_reference` (D6) | A2.9 |
| **D** | Confirm the group detail response may gain a blast-radius block | A2.8 |
| **D** | Choose D1: action-detail endpoint (recommended) or inline `payload` on `ActionSummary` | A2.9 |
| **Team** | Decide §0.1 — Option A, B or C on what-if | A2.5 scope |
| **Team** | Agree §0.2 — what "Phase 2" covers, so the readiness gates match the work | all |

## 5. Invariants a reviewer should check first

Before reading any Phase 2 code, check these. Each is a way this plan could go wrong quietly.

1. `uq_incident_active_per_flight` untouched; one active incident per flight.
2. No group figure computed by summing per-incident counts. Unions only, via C's rollup.
3. `execute()` still requires a persisted `assurance_evaluation`, and an approved `human_decision`
   where the gate demanded one. A `PlanAssuranceSummary` grants nothing.
4. Dry-run evaluation and plan comparison persist nothing.
5. No projected or forecast figure in any response. `basis` stays `recorded_evidence`.
6. Thresholds and bands come from config, never a literal.
7. The six frozen guard tests unmodified (`docs/28-parallel-workstreams.md:108`).
8. Every new endpoint has a `response_model`; no schema renders as `"string"`.
9. Exactly one `actor_kind` mapping in the codebase. Two would let replay and the timeline disagree
   about whether a human authorised something, which is the one thing Phase 1 closed.
