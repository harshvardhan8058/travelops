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

### 2.2 A plan approval is an *intent*, not an authority

A plan approval is recorded **before** the tasks are evaluated — the operator approves the recovery
for the network event, then the engine runs it. So at approval time the `assurance_evaluation` rows
it would cover **do not exist yet**, and it cannot be stored as a set of decisions.

It is therefore stored as an intent, in a new table, and **consumed later**:

```
operator approves the plan set
    -> plan_approval row (intent; authorises nothing on its own)

engine evaluates a task, gate returns needs_human
    -> covers(evaluation, approval)?      <-- the ONE predicate, ONE place
         yes -> materialise human_decision(assurance_id=<this evaluation>,
                                           scope='plan',
                                           plan_approval_id=<intent>,
                                           actor_id/reason copied from the intent)
         no  -> incident stops at awaiting_approval, exactly as in Phase 1
    -> execute() then sees an ordinary human_decision and behaves identically
```

**The property that makes this safe:** the predicate is evaluated against the **real evaluation**, at
the moment it exists — never against what the operator imagined when they clicked. A plan approval
therefore *cannot* extend itself to cover a high-risk action or a failed check, because coverage is
decided after the gate has spoken. An operator approving a plan set is not approving a blank cheque;
they are approving the actions that turn out to be low/medium and fully evidenced.

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
-- new: the intent
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

The `scope_provenance` CHECK is what stops a `scope='plan'` row existing without the intent that
produced it — an unattributable authorisation is exactly the defect Phase 1 closed.

### 2.6 What Stream D renders

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
| **A6** | Group assurance summary + plan approval (P2-D1, P2-D3) | **Migration:** `plan_approval` table; `human_decision.scope` + `plan_approval_id` + both CHECKs (§2.5) | Same confirmation as A5 | A4 |
| **A7** | Replay orchestration | *Nothing.* Reads `decision_log` | — | — |
| **A8** | Group / plan / replay endpoints | **Confirm `fixtures/api/incident_group_detail.json` shape**; whether `why_nine_not_eight` may be derived | — | A1–A3 |
| **A9** | Stream D's dependency asks | **`incident_reference` on the group's `flights[]`** (D6) | — | — |

**Only three C artefacts gate Stream A:** one query helper (A2) and two migrations (A4, A6).
Everything else reuses what exists. Stated plainly so C can size it in one read.

### 3.2 The order

```
A9  →  A7  →  A1  →  A2  →  A3  →  A8  →  A4  →  A6  →  A5
```

| Slot | Item | Why here |
| ---: | --- | --- |
| 1 | **A9** | The only item where **another stream is blocked on A right now**. Unblocks four of D's seven features |
| 2 | **A7** | No dependency, no schema; D's `/replay/:incidentId` is a placeholder today |
| 3 | **A1** | Foundation for everything cascade; needs nothing from C |
| 4 | **A2** | The documented Phase 2 gate. **First C dependency** |
| 5 | **A3** | Thin once A2's rollup is wired |
| 6 | **A8** | Turns three placeholder screens into the cascade story |
| 7 | **A4** | **Waits on C's plan migration.** Slips if that slips |
| 8 | **A6** | Deliberately late: the only item that can execute something a person did not individually authorise. Needs the surrounding invariants already under test |
| 9 | **A5** | Depends on A4 and A6 |

**Cut order, from the bottom:** A5, A6, A4. Before any of them, cut the Open-Meteo / historical
provider expansion, which is already designated first out. **A9→A8 alone deliver the documented
Phase 2 gate** — "one weather event at BLR produces a traceable multi-flight, multi-pairing impact
set".

### 3.3 The `C2-N` labels — binding request

The critical order handed to Stream A was:

```
C2-3 → C2-1/C2-2 → C2-5/C2-6 → C2-4 → C2-7 → C2-8 → C2-9
```

**These labels resolve to nothing in this repository** —
`grep -rn "C2-1\|C2-3\|C2-9" --include=*.md .` returns no match, and Stream C has published no
Phase 2 plan. As instructed, this document does **not** use `C2-N` anywhere except here, and Stream
A's order in §3.2 is expressed purely in `A`-labels.

They are **not** a renumbering of A1–A9. That reading is ruled out on evidence: it would place
`C2-3` (blast radius) first, before the cascade orchestration it derives every figure from, which
cannot be built in that order. So they are Stream C's own items.

**To bind them, C fills this table.** One line each; then §3.1's C column can cite `C2-N` directly.

| C label | C's work | Which A item it gates |
| --- | --- | --- |
| `C2-1` | ? | ? |
| `C2-2` | ? | ? |
| `C2-3` | ? | ? |
| `C2-4` | ? | ? |
| `C2-5` | ? | ? |
| `C2-6` | ? | ? |
| `C2-7` | ? | ? |
| `C2-8` | ? | ? |
| `C2-9` | ? | ? |

The three rows Stream A actually needs are the ones carrying `group_affected_flight_ids`, the `plan`
selection migration, and the `plan_approval` / `human_decision.scope` migration. **If C confirms
those three land before their respective A slots (4, 7 and 8 in §3.2), the orders are compatible and
nothing else needs reconciling.**

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
| C-6 | **Fill the `C2-N` binding table** (§3.3), or confirm those three artefacts land before A slots 4, 7 and 8 | **all** | ☐ |
| C-7 | A may call `plan_digest()` from `app/db/seed.py` for `plan_approval.plan_digest` rather than adding a second hashing scheme | A6 | ☐ |

### Stream D

| # | Confirm | Blocks | ✓ |
| --- | --- | --- | --- |
| D-1 | Choice on the payload ask: **action-detail endpoint** (A's recommendation) or inline `payload` on `ActionSummary` | A9 | ☐ |
| D-2 | `GET /incident-groups/{ref}` may gain a **blast-radius block** | A8 | ☐ |
| D-3 | A5's plan-comparison contract satisfies D's what-if surface. **P2-D2 grants a plan-comparison what-if, not an operational one** | A5 | ☐ |
| D-4 | D will render `human_decision.scope` so a plan-covered action is distinguishable from a per-action approval — both as a person's act (§2.6) | A6 | ☐ |

### Team

| # | Confirm | Blocks | ✓ |
| --- | --- | --- | --- |
| T-1 | §1's scope resolution: plans stay per-incident; summary and approval are group-scoped | A6 | ☐ |
| T-2 | §2's single source of truth: `human_decision` per evaluation is the sole authority; `plan_approval` is an intent consumed by one predicate | A6 | ☐ |
| T-3 | What "Phase 2" covers, given `20-phased-delivery.md` defines it narrowly as *Cascade* — so the readiness gates match the work | all | ☐ |

---

## 5. What a reviewer checks first

1. **No high-risk action is ever covered by a plan approval.** The single most important line in
   Phase 2.
2. No `FAIL` is approvable at plan level, **at any tier**. Fail-closed is not delegable.
3. `human_decision.assurance_id` is still `UNIQUE NOT NULL`. One decision, one evaluation.
4. `execute()` consults **only** `human_decision` — it never reads `plan_approval`.
5. `covers()` exists once, and `PlanApproval` is imported by no other module.
6. `uq_incident_active_per_flight` untouched; one active incident per flight.
7. No group figure computed by summing per-incident counts. Unions only, via C's rollup.
8. What-if and the group summary persist nothing (P2-D2, zero-write).
9. No projected or forecast figure anywhere. `basis` stays `recorded_evidence`.
10. The six frozen guard tests unmodified.
