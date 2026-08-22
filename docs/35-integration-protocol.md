# 35. Integration protocol — enforcing ownership, integrating continuously

**Status: binding from Phase 3 onward.**

**The ownership rule is not new.** `docs/28-parallel-workstreams.md` has assigned exclusive write
paths per stream since Wave 0, and the steering file repeats it. Phase 2 breached it anyway, in
twelve files, and nobody noticed until the final merge.

So this document adds the part that was missing. Not another statement of the rule — a
machine-readable form of it (`OWNERS`), a check that fails (`scripts/check_ownership.py`), and a
cadence that surfaces divergence while it is still cheap.

---

## 1. What went wrong, precisely

Phase 2's streams worked in parallel for the whole phase and integrated at the end. The result,
measured rather than remembered — `python3 scripts/check_ownership.py --report-collisions`:

| Collision | Files | Consequence |
| --- | --- | --- |
| A edited D's console | `api/types.ts`, `api/client.ts`, `App.tsx`, `AppShell.tsx`, `derivation.ts`, `package.json` | Two streams shaping one contract. A added `blastDimensionDerivation` that nothing called; D was separately building the surface that needed it |
| A and C edited the ORM | `models/workflow.py` | Two views of `human_decision` scope |
| A and B edited the gate config | `config/assurance.v2.yaml`, `test_plan_gate.py` | Gate semantics changed under the stream that owns them |
| A and C edited shared docs | `README.md`, `docs/25-evaluation-readiness.md` | Textual conflicts, cheap but noisy |

The expensive failure was not in that table. **Stream A and the integration branch independently
built the same backend integration** — group orchestration, group assurance, plan approval,
what-if — including two migrations both claiming revision `0008_human_decision_scope`. One
complete implementation was discarded at merge. Nothing warned anybody, because nothing was
looking.

---

## 2. The rules

1. **Every shared file has exactly one owner.** The authority is
   [`docs/28-parallel-workstreams.md`](28-parallel-workstreams.md); [`OWNERS`](../OWNERS) is that
   table in checkable form. If they disagree, docs/28 wins and `OWNERS` is the bug.
2. **If you are not the owner, you consume the owner's interface.** You do not edit the file. If
   the interface is wrong, say so and let the owner change it — a second edit is a second seam.
3. **API and schema contracts are locked before implementation.** The lock is
   [`docs/openapi.json`](openapi.json) plus the response models in `backend/app/schemas/`. A
   change after the lock is a coordinated change, not a unilateral one.
4. **Rebase onto latest `main` before starting significant work.** Not at the end.
5. **The integration branch is continuously synchronised with `main`.** A stream branch that is
   more than one merge behind is already accumulating divergence.
6. **When another stream changes a shared contract, rebase immediately.** Divergence compounds:
   the cost of resolving is proportional to how long it was left.
7. **No duplicate implementations and no parallel seams.** Before building something that sounds
   like it might already exist, look. Two orchestrators is not a merge conflict; it is a wasted
   phase.
8. **If two streams need the same file, stop and coordinate before editing.** Record the outcome
   in `DECISIONS.md`.

### Integration cadence

Integrate **during** the phase. A stream lands small, working increments onto `main` as they
become true, and rebases after each other stream lands. The final PR of a phase contains
integration work only — wiring, verification, and the seams between streams. It does not contain
a phase's worth of unmerged cross-stream change.

A useful test: if the final PR of a phase is the first time two streams' code has met, the
protocol was not followed.

---

## 3. Ownership map

Unchanged from `docs/28`, restated in [`OWNERS`](../OWNERS) so a script can read it:

| Owner | Scope |
| --- | --- |
| **A · Core & API** | `backend/app/{orchestrator,events,api,agents,llm,observability,schemas}/`, `config.py`, `main.py`, `cli.py`, `errors.py`, `docker-compose.yml`, `Makefile`, `.kiro/`, `docs/`, `scripts/` |
| **B · Assurance & Policy** | `backend/app/{assurance,policy}/`, `policy_packs/`, `config/` |
| **C · Data, Providers & Services** | `backend/app/{models,db,providers,services,memory}/`, `backend/migrations/`, `data/`, `fixtures/` |
| **D · Frontend** | all of `frontend/` |

Tests live with the code they test, so the owner of the code owns the test.

Two refinements `OWNERS` adds, both narrowing rather than widening:

- **Unclaimed paths are `SHARED`, not free.** An unowned file is how Phase 2's conflicts started.
- **Team artefacts are `SHARED` even inside an owned tree.** `DECISIONS.md`, `OPEN-QUESTIONS.md`,
  `README.md`, `docs/28` and this file sit under paths A owns, but their content is team-agreed, so
  a unilateral edit is a breach even from the nominal owner.

### Consuming instead of editing

Concretely, from the collisions above:

- A needed a derivation adapter for the blast radius. Owner of `derivation.ts` is D, so A asks D
  for `blastDimensionDerivation`, or D builds the surface that needs it. What actually happened —
  A adding an adapter nothing called while D built the surface separately — is the cost of
  skipping that conversation.
- The integration stream needed `evaluate_entitlements` dispatchable. `service_registry.py` is
  A's. The registration is a one-line request to A, not an edit.
- Both A and C needed `human_decision.scope`. `models/workflow.py` is C's; A specifies the column
  and its constraints, C writes it and the migration.

---

## 4. Enforcement

```bash
make check-ownership STREAM=D        # what has my branch touched that I do not own?
make check-collisions                # which files have multiple streams touched historically?
make check-owners-audit              # is the OWNERS file itself coherent?
```

`scripts/check_ownership.py` diffs the branch against `origin/main` — including uncommitted work,
because the point is to catch this on the first commit rather than the last — and exits `2` if the
branch edits a file another stream owns. Run it before pushing; wire it into CI when CI exists.

It reports three categories, and the middle one matters most: **`SHARED` files are not violations
but they are not free either.** They require a recorded agreement before merge.

### Before opening a phase's final PR

```bash
git fetch origin && git rebase origin/main
make check-ownership STREAM=<x>
make test && make lint
cd backend && uv run --extra dev pytest
make verify-phase2 && make verify-console
gh api repos/<owner>/<repo>/pulls/<n> --jq '.mergeable_state'   # must be "clean"
```

A PR whose `mergeable_state` is not `clean` is not ready to review.

---

## 5. What this protocol does not fix

It catches *who edited what*. It does not catch two streams building the same thing in files they
each legitimately own — which is exactly what happened with the two backend integrations, since
A's `api/incident_groups.py` and the integration branch's `api/groups.py` are different paths and
would both pass an ownership check.

The only defence against that is the contract lock in rule 3 plus looking before building. If a
phase plan names a capability, exactly one stream implements it, and that assignment belongs in
the phase plan before implementation starts.
