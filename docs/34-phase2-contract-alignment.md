# 34. Phase 2 — cross-stream contract alignment

**Purpose: one page that B, C and D confirm before any Phase 2 code is written.** Implementation is
authorised only after those confirmations, so this document is the gate.

Companion to [`33-phase2-stream-a-plan.md`](33-phase2-stream-a-plan.md), which holds the design.
This one holds the **contracts, the scope terminology, and the order**.

Written against `main` at `5052388`. Decisions cited as **P2-D1/2/3** are recorded canonically in
[`DECISIONS.md`](DECISIONS.md#phase-2-architecture-decisions--final).

---

## 1. Scope terminology — resolving the incident-vs-group contradiction

There **was** a real contradiction between two invariants, and it has to be settled in words before
it can be settled in code:

| | |
| --- | --- |
| Phase 1 invariant | "An incident is one flight. A **plan** is authorised for one incident." |
| **P2-D1** | "**Plan-level assurance** is incident-group scoped." |

Taken literally these conflict: a plan cannot be both per-incident and group-scoped.

**Resolution: they describe different objects.** The plan stays per-incident. What becomes
group-scoped is the *review surface* and the *operator's act* — not the plan.

| Term | Scope | Object | Changes in Phase 2? |
| --- | --- | --- | --- |
| **Plan** | **One incident** | `plan` row, tasks for one flight | No. `uq_incident_active_per_flight` and per-incident plans are untouched |
| **Selected plan** | One incident | The `plan` row with `selection_state = selected` | New (A4) |
| **Plan set** | **Group** | The selected plan of each member incident — one per flight | New concept, derived, never stored |
| **Group assurance summary** | **Group** | Read-only aggregate of per-task gate outcomes across the plan set | New (A6). **P2-D1's object** |
| **Plan approval** | **Group** | One operator act over the plan set | New (A6). **P2-D3's object** |
| **Action-level approval** | One action | `human_decision` for one `assurance_evaluation` | Unchanged from Phase 1 |

So "plan-level assurance is incident-group scoped" is implemented as: **the summary and the approval
span the group; the plans they describe remain per-incident.** No plan is ever authorised for more
than its own flight, and no group figure is ever a sum (unions only, via C's `cascade_rollup`).

**Naming consequence.** The table introduced below is `plan_approval` but carries a NOT NULL
`incident_group_id`, so its scope is unambiguous from the schema rather than from the name. A
reviewer who reads only the DDL still cannot mistake it for a per-incident object.

---

## 2. Single source of truth for plan approval and human-decision scope

This section is the answer to "define the single source of truth", and it is the most important
contract in Phase 2.

### 2.1 The authority is `human_decision`, and it stays 1:1 with an evaluation

`human_decision.assurance_id` is **`nullable=False, unique=True`** today
(`app/models/workflow.py:236`). One decision, one evaluation. That constraint is the reason Phase 1's
audit trail is trustworthy, and **Phase 2 does not relax it.**

> **Single source of truth: a `human_decision` row for that action's own
> `assurance_evaluation` is the only thing that satisfies `needs_human`.**
> `execute()` consults nothing else. It does not know that plan approvals exist.

That is the whole design, and it is what removes the "second authorisation path" risk I flagged in
the first draft. There is still exactly one path to execution.

### 2.2 A plan approval fans out over evaluations that already exist

**Corrected against Stream D's merged plan (#44, §9.1).** My first version modelled a plan approval
as an *intent* recorded before the tasks were evaluated, then consumed later as each `needs_human`
arrived. D's UI model rules that out, and D is right:

> D §9.1: "The plan-level control **lists exactly what it will cover**, itemised by task and tier,
> and lists what it excludes with the reason … Excluded items are visible, not hidden."
> Acceptance: "the count of approvals recorded **equals the count claimed**."

A control cannot itemise what it will cover unless the evaluations already exist. So the operator is
not approving a plan before it runs — they are on `/assurance/:groupId` looking at evaluations that
are **already `awaiting_approval`**, and approving those.

That makes the model simpler and stricter:

```
operator approves on the group assurance screen
    -> partition the group's CURRENTLY-awaiting evaluations with covers()   <- ONE predicate, ONE place
         covered  -> one human_decision per evaluation
                     (scope='plan', plan_approval_id=<parent>,
                      actor_id + reason shared from the parent)
         excluded -> returned with a reason token; stays awaiting_approval
    -> plan_approval row is the parent binding those decisions together
    -> execute() then sees ordinary human_decision rows and behaves identically
```

**A plan approval covers only the evaluations awaiting at the moment it is made.** It does **not**
extend to evaluations produced later in the run. This is deliberate: forward coverage would be a
blank cheque over actions nobody has seen, and it would contradict D's "lists exactly what it will
cover". A later `needs_human` requires a new approval. Say so in the UI copy.

Consequences worth stating, because they are what make this auditable:

- `assurance_id` stays `UNIQUE` and is satisfied naturally — each evaluation receives at most one
  decision, whichever route created it.
- **The count recorded equals the count claimed**, because both come from the same partition. D can
  assert it, and so can a test.
- The excluded set is returned, not silently dropped, so a reviewer sees that the control was
  *unable* to cover something rather than that it chose not to.

### 2.3 The predicate, in exactly one place

`backend/app/orchestrator/approval_scope.py` — **new, and the only module allowed to decide
coverage.**

```python
def covers(evaluation: AssuranceResult, approval: PlanApproval) -> Coverage:
    """Whether a plan approval may satisfy this evaluation's needs_human.

    P2-D3, both conditions required:
      1. evaluation.risk_tier in {low, medium}
      2. no check in evaluation.checks has state == failed
    """
```

`Coverage` is a small result carrying `covered: bool` and, when false, a `reason` from a fixed
enum — `HIGH_RISK`, `FAILED_CHECK` — so refusals are reportable rather than silent.

Rules that make "single place" enforceable rather than aspirational:

1. `covers()` is the **only** function that reads `plan_approval`. Nothing else imports the model.
2. Only the engine's assurance step calls it. `execute()` does not.
3. A guard test asserts both: an AST check that `PlanApproval` is imported only by
   `approval_scope.py` and the module that materialises the decision.

### 2.4 What P2-D3 forbids, stated against the six checks

P2-D3: *"Approval can cover risk; it can never override failed evidence / stale sources /
unresolved entities / policy failure."* Mapped onto
[`18-decision-assurance-gate.md`](18-decision-assurance-gate.md)'s six checks:

| Check | `FAIL` approvable at plan level? | Source |
| --- | --- | --- |
| `evidence_complete` | **No** | P2-D3, "failed evidence" |
| `sources_fresh` | **No** | P2-D3, "stale sources" |
| `entities_valid` | **No** | P2-D3, "unresolved entities" |
| `policy_compliant` | **No** | P2-D3, "policy failure" |
| `no_conflicts` | **No** | *Not enumerated by P2-D3 — see below* |
| `action_risk` | **No** | A `FAIL` here is a failure, not a classification |

**Deliberate widening, flagged for confirmation.** P2-D3 enumerates four prohibitions and does not
mention `no_conflicts`. The implemented predicate is **"no check has failed"**, which is a strict
superset and therefore refuses more than P2-D3 requires. I chose the safe side: a conflict `FAIL`
means the action collides with another action, and an operator waving that through would produce a
double booking or a duplicate payment. **If the team intends `no_conflicts` to be approvable, say so
explicitly** — I will not infer it from an omission.

Note the distinction the gate already draws (`assurance/contract.py:77`): `action_risk` may **PASS**
while its *tier* still blocks. A high **tier** with all checks passing is the case P2-D3 sends to
action-level approval; a `FAIL` on `action_risk` is a different thing and is never approvable.

### 2.5 Database changes required (**owned by Stream C**)

Two changes. Stream A writes no migration.

```sql
-- new: the parent record of one operator act
CREATE TABLE plan_approval (
  id                 SERIAL PRIMARY KEY,
  incident_group_id  INTEGER NOT NULL REFERENCES incident_group(id),
  actor_id           VARCHAR(64) NOT NULL,      -- pseudonymous, as in human_decision
  reason             TEXT       NOT NULL,
  decided_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  plan_digest        VARCHAR(64) NOT NULL       -- what was on screen when they approved
);

-- human_decision gains scope and provenance. assurance_id stays UNIQUE NOT NULL.
ALTER TABLE human_decision
  ADD COLUMN scope VARCHAR(8) NOT NULL DEFAULT 'action',   -- 'action' | 'plan'
  ADD COLUMN plan_approval_id INTEGER NULL REFERENCES plan_approval(id),
  ADD CONSTRAINT human_decision_scope_valid CHECK (scope IN ('action','plan')),
  ADD CONSTRAINT human_decision_scope_provenance CHECK (
      (scope = 'action' AND plan_approval_id IS NULL) OR
      (scope = 'plan'   AND plan_approval_id IS NOT NULL));
```

`plan_digest` matters for audit: it records **what the operator was shown**. If the plan set changed
between display and execution, the approval covered a different thing and that must be detectable.
Reuse `app/db/seed.py:plan_digest()` rather than inventing a second hashing scheme.

The `scope_provenance` CHECK is what stops a `scope='plan'` row existing without the approval that
produced it — an unattributable authorisation is exactly the defect Phase 1 closed.

### 2.6 The endpoints this creates (D's FE-8 and FE-10)

D's merged plan adds two asks that P2-D1 and P2-D3 created. Both are Stream A's, and both are
accepted:

| Ask | Endpoint | Notes |
| --- | --- | --- |
| **FE-8** | `GET /api/v1/incident-groups/{ref}/assurance` → `GroupAssuranceSummary` | D offered "or `incidents[]` on the group payload so the client can fan out" and said a single endpoint is better. **Agreed — one endpoint.** Client-side fan-out over 8 incidents would make the group view N+1 requests and would let a partial failure read as a pass |
| **FE-10** | `POST /api/v1/incident-groups/{ref}/assurance/decision` → `PlanApprovalResponse` | The plan-approval act. Server-side partition, `Idempotency-Key` required, as every mutation already is |

`PlanApprovalResponse` returns the partition explicitly, so D's "count recorded equals count claimed"
is checkable from the response alone:

```python
class PlanApprovalResponse(BaseModel):
    plan_approval_id: int
    covered: list[CoveredEvaluation]      # evaluation id, task, action_type, tier, human_decision_id
    excluded: list[ExcludedEvaluation]    # evaluation id, task, reason: HIGH_RISK | FAILED_CHECK
    covered_count: int                    # == len(covered), and == decisions actually written
    excluded_count: int
```

**Per-incident evaluations (D's §9 API block).** D also enumerates the fields they need from
`GET /incidents/{ref}/assurance`, including `warn_permitted_by_config` and `human_decision` per
evaluation. `warn_permitted_by_config` is **not currently on any response model** — it is knowable,
since `gate.py` already consults `config.warn_permitted(action_type, check_name)` for rule 4, but it
must be exposed deliberately rather than inferred by D. Added as a Stream A item under A8 and listed
in §4 as **D-5** for confirmation of the exact shape.

**Config hash divergence.** D requires `config_version` and `config_hash` per incident, flagged when
they differ *between* incidents in a group — "a group whose incidents were judged under two config
hashes is a fact a reviewer must see". Correct, and cheap: both fields are already on
`assurance_evaluation`. The group summary carries them per incident plus a
`config_hash_uniform: bool`, so D flags divergence from a field rather than by comparing strings.

### 2.7 What Stream D renders

- Every action still shows the `human_decision_id` that authorised it and reads as
  `actor_kind=human`. **No new attribution model** — the Phase 1 fix carries through unchanged.
- `scope` distinguishes the copy: "approved by operator for this action" versus "covered by the plan
  approval for GRP-2026-0820-VOBL". Both are a person's act; one covered a set.
- The group summary's `not_approvable` list gives D the copy for "this needs its own approval", with
  `HIGH_RISK` or `FAILED_CHECK` as a token rather than prose.

---

## 3. A1–A9 — canonical items, dependencies and order

Stream A's Phase 2 items. **These labels (`A1`–`A9`) are canonical from here on** and replace the
earlier `A2.1`–`A2.9`.

> **Label hygiene.** `A1`–`A9` are Stream A's Phase 2 items. `DECISIONS.md` separately has
> assumptions `A1`–`A4` (e.g. "A2 — cascading disruption"). Cite Stream A items as **"A-item A2"** or
> `33-phase2-stream-a-plan.md#a2` when the context is not obvious.

### 3.1 The mapping

| Item | Work | What it needs from **C** | What it needs from **B** | Blocked by (A) |
| --- | --- | --- | --- | --- |
| **A1** | Disruption-group lifecycle | *Nothing.* Uses existing `incident_group.state` | — | — |
| **A2** | Network-level cascade orchestration | **`group_affected_flight_ids(session, group_id)`** in `scenario_queries`, or written permission for A to derive scope | — | A1 |
| **A3** | Blast-radius banding | **`cascade_rollup()` as-is.** A computes no figure | — | A2 |
| **A4** | Candidate recovery-plan lifecycle | **Migration:** `plan.selection_state`, `plan.selected_at`, `plan.selected_by` + index on `(incident_id, selection_state)` | — | A1 |
| **A5** | What-if as plan comparison (P2-D2) | *Nothing beyond A4* | **Confirm `gate.evaluate` is side-effect free** | A4 |
| **A6** | Group assurance summary + plan approval (P2-D1, P2-D3); serves D's FE-8 and FE-10 | **Migration:** `plan_approval` table; `human_decision.scope` + `plan_approval_id` + both CHECKs (§2.5). **Needed before mandate slot 3** | Same confirmation as A5 | A4 |
| **A7** | Replay orchestration | *Nothing.* Reads `decision_log` | — | — |
| **A8** | Group / plan / replay endpoints, incl. the group assurance endpoint (FE-8) and `warn_permitted_by_config` (D-5) | **Confirm `fixtures/api/incident_group_detail.json` shape**; whether `why_nine_not_eight` may be derived | — | A1–A3 |
| **A9** | Stream D's dependency asks | **`incident_reference` on the group's `flights[]`** (D6) | — | — |

**Only three C artefacts gate Stream A:** one query helper (A2) and two migrations (A4, A6).
Everything else reuses what exists. Stated plainly so C can size it in one read.

### 3.2 The order

**Reconciled with the mandated `C2-N` order** (derivation in §3.4). This is the sequence Stream A
follows.

```
A9  →  A1  →  A2  →  A3  →  A8  →  A4  →  A6  →  A5  →  A7
```

| Slot | Item | Serves | Why here |
| ---: | --- | --- | --- |
| 1 | **A9** | C2-2, C2-6 | The only item where another stream is blocked on A right now. FE-6 is needed by C2-2 at mandate slot 2 |
| 2 | **A1** | C2-1, C2-2 | Foundation for everything cascade; needs nothing from C |
| 3 | **A2** | C2-2 | The documented Phase 2 gate. **First C dependency** (`group_affected_flight_ids`) |
| 4 | **A3** | C2-1 | Thin once A2's rollup is wired |
| 5 | **A8** | C2-1, C2-2, C2-5 | Turns three placeholder screens into the cascade story |
| 6 | **A4** | C2-5, C2-8 | **Waits on C's plan migration.** Needed by mandate slot 3, so this migration is early |
| 7 | **A6** | C2-5 | Needs A4. **Highest-risk item, and the mandate front-loads it** — see §3.4 |
| 8 | **A5** | C2-4 | Needs A4 |
| 9 | **A7** | C2-7 | No dependencies; the mandate wants it at slot 5, so it fits anywhere |

**Sequencing is internal.** Per the delivery model in §5, Phase 2 ships as **one increment**, so this
order governs the order of work, not a series of releases or approval checkpoints.

### 3.3 `C2-N` — **resolved.** They are shared Phase 2 feature slots, published by Stream D

My earlier draft could not map `C2-N` and refused to guess. **Stream D's plan, merged as #44, binds
them** (`frontend/docs/phase-2-stream-d-plan.md` §0.1). They are neither Stream C's items nor a
renumbering of A1–A9 — they are **cross-stream feature slots**, and each one is a *product
capability* that several streams contribute to.

That also explains why reading them as A-items was impossible: `C2-3` is **shared frontend
groundwork** with no Stream A content at all, which is exactly why it can be first.

| Slot | Feature | Stream A items it needs | A work? |
| --- | --- | --- | --- |
| **C2-3** | Shared groundwork — types, graph primitives, layout, keyboard model | — | **None.** Frontend-only |
| **C2-1** | Network Command Center | A1, A3, A8 | Yes |
| **C2-2** | Disruption / Cascade Explorer | A1, A2, A8, A9 (FE-6) | Yes |
| **C2-5** | Plan-level assurance, group-scoped, incl. the P2-D3 approval model | **A6**, A8 (FE-8), A4 | Yes — heaviest |
| **C2-6** | Impact views (crew now, rest on FE-1) | A9 (FE-1) | Yes |
| **C2-4** | What-if: bounded zero-write re-evaluation | **A5** | Yes |
| **C2-7** | Replay | **A7** | Yes |
| **C2-8** | Recovery-plan comparison | **A4** (FE-4) | Yes |
| **C2-9** | Hardening — rehearsal, accessibility, five-state audit | all | Verification |

D flags the binding itself as needing ratification ("Are the `C2-1…C2-9` labels the intended
binding?"), so it is **working, not final** — but it is published, coherent and consistent with the
mandated order, so Stream A plans against it rather than blocking on it.

### 3.4 Reconciling A's order with the mandated `C2-N` order

Mandated: `C2-3 → C2-1/C2-2 → C2-5/C2-6 → C2-4 → C2-7 → C2-8 → C2-9`.

Translating each slot into what Stream A must have ready **before** it:

| Slot | Feature(s) | A items that must be done |
| ---: | --- | --- |
| 1 | C2-3 | *nothing* |
| 2 | C2-1, C2-2 | A9 (FE-6), A1, A2, A3, A8 |
| 3 | C2-5, C2-6 | A9 (FE-1), **A4**, **A6**, A8 (FE-8) |
| 4 | C2-4 | **A5** |
| 5 | C2-7 | **A7** |
| 6 | C2-8 | A4 *(already done at slot 3)* |
| 7 | C2-9 | all |

**Revised Stream A order — compatible with the mandate:**

```
A9  →  A1  →  A2  →  A3  →  A8  →  A4  →  A6  →  A5  →  A7
```

Two changes from §3.2's first version, both forced by the mandate:

1. **A7 (replay) moves from second to last.** It has no dependencies so it can sit anywhere; the
   mandate needs it at slot 5, after what-if. Nothing else wants it earlier.
2. **A6 is no longer "the late, careful item".** The mandate needs it at **slot 3 of 7**. This is the
   one place where the mandated order genuinely raises risk, and it should be said plainly:

> **A6 is the only Phase 2 item that can execute an action a person did not individually authorise,
> and the mandated order front-loads it.** My preference was to build it last, once the surrounding
> invariants were already covered by tests. The mandate makes C2-5 the third slot, so that is not
> available.

Mitigation, since the order is fixed: A6's **guard tests are written before its implementation** —
specifically "no high-risk evaluation is ever covered" and "no failed check is ever covered at any
tier" — and A4's migration is requested early enough that A6 is never rushed to catch up. D
independently arrived at the same protection from the UI side ("the server must enforce the tier
rule; the UI must not be the only thing preventing a high-risk bulk approval"), so both ends of
C2-5 are defended.

**Everything Stream A needs from C therefore lands earlier than my first version assumed.** The
`plan` selection migration and the `plan_approval` migration are both required before slot 3, not
slot 7. That is the single most important scheduling consequence of the mandate, and it is the top
row of §4.

### 3.5 Stream D's `FE-N` asks — Stream A's answers

D's merged plan routes ten asks; **eight are Stream A's.** Answers, so D can plan against them:

| Ask | Answer | A item |
| --- | --- | --- |
| **FE-1** `payload` on `ActionSummary` **or** `GET /incidents/{id}/actions/{id}` | **Accepted, as the endpoint.** D offered both; the endpoint keeps list responses lean and stops C's unversioned service dicts becoming public API | A9 |
| **FE-2** Wire `/incident-groups/*` to `cascade_rollup()` | **Accepted.** Already the plan — Stream A computes no figure | A3, A8 |
| **FE-3** `latitude`/`longitude` on `/flights.network[]` | **Not Stream A's**, and A agrees with D's recommendation to decline | — |
| **FE-4** Plan-alternatives contract | **Accepted, superset.** D suggested `plan.supersedes`/`plan.version`; A4 asks C for `selection_state` + `selected_at` + `selected_by`, which also records *who chose* and *when* — needed for P2-D3 attribution. If C prefers D's cheaper pair, selection attribution has nowhere to live | A4 |
| **FE-6** `incident_reference` on group `flights[]` | **Accepted**, nullable | A9 |
| **FE-7** `reason_code` on `ActionSummary` | **Accepted**, and nearly free — already recorded inside `action.payload` | A9 |
| **FE-8** Group-scoped assurance endpoint | **Accepted as one endpoint**, not client fan-out (§2.6) | A6, A8 |
| **FE-9** What-if contract per P2-D2 | **Accepted.** Zero-write, deterministic, same six checks. A5's `basis: Literal["recorded_evidence"]` makes the boundary structural | A5 |
| **FE-10** Plan-level decision contract per P2-D3 | **Accepted**, and §2 is its design. Server enforces both rules | A6 |

**One divergence worth D's attention:** D's C2-8 note says plan comparison may slip to Phase 3
("comparing fallback playbook versus Planner output"). Under the one-increment delivery model that is
not a separate release, so A4 and A5 are in scope for Phase 2 regardless — but if the team takes D's
Phase 3 option, A5's demo surface shrinks while its contract stays the same.

---

## 4. Confirmations required before implementation

Implementation is authorised only when every row below is confirmed. Tick in review.

### Stream B

| # | Confirm | Blocks | ✓ |
| --- | --- | --- | --- |
| B-1 | `gate.evaluate(...)` is **side-effect free** and safe to call repeatedly in a read-only path (it returns `AssuranceResult` and persists nothing — A must not persist it either) | A5, A6 | ☐ |
| B-2 | The P2-D3 predicate is correctly stated against the gate: coverage requires `risk_tier ∈ {low, medium}` **and** no check `state == failed` (§2.3) | A6 | ☐ |
| B-3 | **`no_conflicts` FAIL is not approvable** — the deliberate widening in §2.4 is correct, or say otherwise | A6 | ☐ |

### Stream C

| # | Confirm | Blocks | ✓ |
| --- | --- | --- | --- |
| C-1 | `group_affected_flight_ids(session, group_id)` will be exposed in `scenario_queries` — **or** A may derive group scope from `flight.origin_icao == group.airport_icao` plus a recorded delay | A2 | ☐ |
| C-2 | Migration for `plan.selection_state` / `selected_at` / `selected_by` + index | A4 | ☐ |
| C-3 | Migration for `plan_approval` and `human_decision.scope` / `plan_approval_id`, **with both CHECK constraints** as specified in §2.5 | A6 | ☐ |
| C-4 | `fixtures/api/incident_group_detail.json` shape is frozen for A8, and whether `why_nine_not_eight` may be **derived** from mechanism counts | A8 | ☐ |
| C-5 | `flights[]` in the group fixture gains a **nullable** `incident_reference` | A9 | ☐ |
| C-6 | **`selection_state` versus D's cheaper `plan.supersedes`/`plan.version` (FE-4).** A prefers `selection_state` + `selected_at` + `selected_by`, because P2-D3 attribution needs somewhere to record *who* selected and *when*. Confirm which | A4 | ☐ |
| C-7 | A may call `plan_digest()` from `app/db/seed.py` for `plan_approval.plan_digest` rather than adding a second hashing scheme | A6 | ☐ |
| C-8 | **Both migrations are needed earlier than first planned** — before mandate slot 3, not slot 7 (§3.4). Confirm the timing is achievable | A4, A6 | ☐ |

### Stream D

Four of D's rows are **already answered by their merged plan (#44)** and are recorded as resolved
rather than pending:

| # | Confirm | Blocks | ✓ |
| --- | --- | --- | --- |
| ~~D-1~~ | ~~Payload ask shape~~ — **resolved.** D's FE-1 offers "or `GET /incidents/{id}/actions/{action_id}`"; A takes the endpoint | A9 | ✅ |
| ~~D-2~~ | ~~Blast-radius block on group detail~~ — **resolved.** D's C2-1 consumes rollup figures directly | A8 | ✅ |
| ~~D-4~~ | ~~Rendering plan-covered versus per-action approval~~ — **resolved.** D's §9.1 renders both, with the excluded set visible | A6 | ✅ |
| ~~D-6~~ | ~~`incident_reference` on group `flights[]`~~ — **resolved.** D's FE-6, agreed both ways | A9 | ✅ |
| D-3 | A5's plan-comparison contract satisfies D's what-if surface. **P2-D2 grants a plan-comparison what-if, not an operational one**, and D's C2-8 note contemplates deferring comparison to Phase 3 — which the one-increment model does not allow | A5 | ☐ |
| D-5 | Exact shape for `warn_permitted_by_config` on the per-evaluation response (§2.6). It is **not on any model today** and must be exposed deliberately, not inferred | A8 | ☐ |
| D-7 | One group assurance endpoint rather than client fan-out (§2.6) — A read D's "a single endpoint is better" as agreement; confirm | A6, A8 | ☐ |

### Team

| # | Confirm | Blocks | ✓ |
| --- | --- | --- | --- |
| T-1 | §1's scope resolution: plans stay per-incident; summary and approval are group-scoped | A6 | ☐ |
| T-2 | §2's single source of truth: `human_decision` per evaluation is the sole authority; a `plan_approval` fans out over evaluations that already exist and never covers future ones | A6 | ☐ |
| T-3 | What "Phase 2" covers, given `20-phased-delivery.md` defines it narrowly as *Cascade* — so the readiness gates match the work | all | ☐ |
| T-4 | Ratify D's `C2-1…C2-9` binding (§3.3). D raised it as a question; A is planning against it | all | ☐ |
| T-5 | Accept that the mandated order front-loads A6, the highest-risk item, to slot 3 — with guard-tests-first as the mitigation (§3.4) | A6 | ☐ |

**Resolved since the first version of this document:** the `C2-N` mapping (§3.3, from D's merged
plan), the approval fan-out model (§2.2, corrected against D's §9.1), and four of D's confirmations.
**What actually blocks now is Stream C**, which has published no Phase 2 plan: C-1 through C-8 are
all outstanding, and two of them are migrations needed earlier than first planned.

---

## 5. Delivery model and definition of done

**Phase 2 is one complete product increment, not a chain of small approval checkpoints.** Internal
work is sequenced however dependencies require (§3.2), but once the contracts in §4 are aligned, the
whole approved scope is built in **one coordinated cycle**.

What that changes in practice:

- The A1–A9 order is a **work order, not a release plan**. There is no per-item sign-off gate.
- Nothing on the Phase 2 critical path ships as a stub. If an item cannot be completed, it is
  **cut explicitly** (§3.2's cut order) rather than shipped hollow — a refusal is honest, a stub is
  not, and Phase 1 established that distinction.
- The cross-stream integration is part of the increment, not a follow-up. A's endpoints are not
  "done" when they return a typed response; they are done when D renders them.

### Definition of done

Phase 2 is complete only when **all** of the following hold. This is the acceptance list, and no
subset of it counts as done.

| # | Criterion | Evidence |
| ---: | --- | --- |
| 1 | All approved backend capabilities delivered | A1–A9 complete, or explicitly cut and recorded |
| 2 | All approved UI capabilities delivered | C2-1…C2-9 per Stream D's plan |
| 3 | **A, B, C, D integrated** | No stream's work sitting behind a fixture or a fan-out workaround |
| 4 | Full regression suite green | `cd backend && uv run pytest` — currently 1087 passed, 38 skipped, and Phase 2 must not reduce it |
| 5 | Real Postgres verification | `TRAVELOPS_TEST_DATABASE_URL=…` suite, plus the seed → inject → run → approve → run → resolved journey on real Postgres. **Remember `alembic upgrade head` first** — C's fixture owns rows, not schema |
| 6 | Clean Docker startup | `docker compose up` to healthy Postgres + Redis with no manual intervention. **Not verifiable in this sandbox** (no `docker compose`), so this is a demo-machine check |
| 7 | Full Windows demo path | The documented PowerShell equivalents, end to end, on the demo laptop |
| 8 | Browser / projector verification | The console renders and is legible at projector contrast. **Still unconfirmed from Phase 1** — see below |
| 9 | **No Phase 2 critical-path stubs** | No fixture route serving a path that has a real implementation; no registered service that fabricates success |

Plus the standing checks: `uv run ruff check .`, `uv run ruff format --check .`,
`python3 scripts/verify_docs.py`, `scripts/verify_demo.py` (13/13), and `git diff --check`.

**Carried-over gap that criterion 8 inherits.** Phase 1 closed with the console at `:5173` never
confirmed as rendering on the demo machine, and the UI readiness items unticked. Phase 2 cannot
satisfy criterion 8 without closing that first, and it needs a person at the demo laptop — no
sandbox can do it.

**Criteria 5–8 need the demo machine**, so they are the ones most likely to be discovered late.
They should be exercised once mid-cycle rather than only at the end.

---

## 6. What a reviewer checks first

1. **No high-risk action is ever covered by a plan approval.** The single most important line in
   Phase 2.
2. No `FAIL` is approvable at plan level, **at any tier**. Fail-closed is not delegable.
3. `human_decision.assurance_id` is still `UNIQUE NOT NULL`. One decision, one evaluation.
4. `execute()` consults **only** `human_decision` — it never reads `plan_approval`.
5. `covers()` exists once, and `PlanApproval` is imported by no other module.
6. A plan approval covers only evaluations **already awaiting** when it was made — never a future
   one. Forward coverage is a blank cheque.
7. `covered_count` equals the number of `human_decision` rows actually written, and the excluded set
   is returned with reasons rather than dropped.
8. `uq_incident_active_per_flight` untouched; one active incident per flight.
9. No group figure computed by summing per-incident counts. Unions only, via C's rollup.
10. What-if and the group summary persist nothing (P2-D2, zero-write).
11. No projected or forecast figure anywhere. `basis` stays `recorded_evidence`.
12. The six frozen guard tests unmodified.
13. No aggregate assurance score at any level, group included. A group is not "assured" because most
    of its incidents are — D's rule, and it belongs to the API too.
