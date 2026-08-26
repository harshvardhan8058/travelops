# 36. Integration protocol and the shared-file register

Mandatory from Phase 3. The rule itself is in
[`.kiro/steering/integration.md`](../.kiro/steering/integration.md) so it reaches every session; this
document holds the register, the seam mechanisms, and the evidence for why it exists.

---

## 1. Why directory ownership was not enough

[`28-parallel-workstreams.md`](28-parallel-workstreams.md) already partitions write access:

> A stream may only create or modify files inside the paths it owns. Anything outside is a request to
> the owning stream, not an edit.

That rule is right, and **Phase 2 broke it five times — all of them mine.** Recorded plainly, because
a protocol written without the evidence gets argued with:

| File | Owner | What Stream A did | Why the pull was real |
| --- | --- | --- | --- |
| `config/assurance.v2.yaml` | **B** | Raised the plan-level ceilings | 400 passengers against a 604-passenger event made the flagship scenario unapprovable *by anyone* |
| `backend/tests/unit/assurance/test_plan_gate.py` | **B** | Rewrote two assertions | They pinned the literals I had just changed |
| `backend/app/models/workflow.py` + `migrations/0008` | **C** | Added `human_decision.scope` | Plan approval needed to be distinguishable from per-action approval |
| `backend/app/orchestrator/service_registry.py` | **A** | Registered three of C's services | A owns the file, C owns the services — the seam has two sides |
| `frontend/src/api/{types,client}.ts`, `derivation.ts`, `App.tsx`, `AppShell.tsx` | **D** | Added types, client methods, two screens | New endpoints are useless until something calls them |

None of these was avoidable by trying harder to stay inside my directories. Each is a **seam**: a file
one stream owns whose *content* is partly another stream's. Directory ownership says who may type;
it does not say how the other stream gets what it needs. That is what §3 fixes.

## 2. The cost, measured

- The final PR was **52 files, +11,635/−867** — a phase of accumulated cross-stream change presented
  as one review.
- A follow-up integration PR (#53) then had to resolve it against `main` and chose per-hunk between
  "main's backend" and "D's UI". That choice should never have to be made by whoever merges last.
- Two Stream B tests failed in *my* branch for a config change *I* made to *their* file, which is the
  clearest possible signal that the seam was missing rather than the tests being wrong.

## 3. The seam register

For each seam: who owns the file, and **how everyone else gets what they need without editing it**.

| # | Seam | File owner | Consumption mechanism |
| --- | --- | --- | --- |
| **S1** | Service registration | **A** (`orchestrator/service_registry.py`) | C exports a service class with `execute(**kwargs) -> ServiceResult` and adds nothing to the registry. A adds exactly one `STAGE2_ADAPTERS` entry per action. **C never edits the registry; A never edits a service.** |
| **S2** | Assurance and plan config | **B** (`config/assurance.*.yaml`) | A does **not** edit thresholds. A opens a calibration request naming the scenario figure that does not fit (e.g. "604 passengers vs `max_passengers_affected: 400`"). B changes the value and its own tests. |
| **S3** | Schema and migrations | **C** (`models/`, `migrations/`) | A/B/D specify the columns and constraints in a request; C writes the model and the migration in one revision. **No stream outside C adds a migration file.** |
| **S4** | API response contracts | **A** (`app/schemas/`, `app/api/`) | D consumes the generated `docs/openapi.json`. A regenerates it in the same commit as any schema change. D's request for a field is an ask on A, not a local widening. |
| **S5** | Frontend API types and client | **D** (`frontend/src/api/`) | A publishes the contract (S4) and **does not** hand-write TypeScript. D mirrors it. Where A must prove an endpoint is consumable, A does it with a test or `verify_phase2.py`, not by editing D's client. |
| **S6** | Screens and design system | **D** (all of `frontend/`) | A/B/C never add a screen or a token. A new capability arrives as an endpoint plus an entry in the readiness list; D builds the surface. |
| **S7** | Cross-stream test files | file's owner | Any stream may **add** a test. No stream may weaken or delete another's assertion. If another stream's test fails because of your change, that is a seam conversation, not a test fix. |
| **S8** | Steering | **A**, with team agreement | Steering is shared law. A change silently changes what three other sessions build. |
| **S9** | Generated artefacts | **A** (`docs/openapi.json`) | Regenerated, never hand-edited. Regeneration belongs in the commit that changed the schema. |
| **S10** | Shared derivations | first definer, then shared | Exactly one implementation. Two plan hashes, two `actor_kind` mappings or two registries is a defect even when both work — the second one will disagree eventually. |

**How to use it:** before starting, list the files the work will touch. Any file whose owner is not
you is either a request (S1–S6, S8) or an addition-only edit (S7). If neither fits, stop and
coordinate.

## 3a. Register: Phase 3 final integration (Stream C as integration lead)

Recorded before merge, per §3. Nine files outside C's paths were edited to close the Phase 3
integration gaps. Each is listed with its seam, its owner, and why the change could not be a request
— the instruction for this increment was explicitly *"keep A's workload minimal; do not ask A to
rebuild anything already present on main"*, which converts an S4/S5/S6 request into an integration
edit. **Owners should review their own rows; nothing here is agreed until they do.**

The hard constraint held: `agents/planner.py`, `agents/reflection.py`, `llm/client.py` and
`llm/prompts/` were **not touched**. The reasoning layer is entirely Stream A's.

| File | Owner | Seam | Change | Why not a request |
| --- | --- | --- | --- | --- |
| `app/orchestrator/engine.py` | **A** | — | Thread `authorship`, `payload` and evidence refs into `gate_requirements`; add `_proposal_authorship` and `_corroboration_baseline` | This *is* the integration gap. B's #64 shipped `authorship_constraints` and nothing ever supplied the argument, so the whole mechanism returned `[]` on every call. Closing it is one call site in A's file. |
| `app/schemas/plans.py` | **A** | S4 | Add `assurance_id` to `PlanTaskOut` | `PlanTaskSummary` has carried it since Phase 1. Without it on `/plans`, Plan Comparison cannot show whether the **agent's** candidate cleared the gate — the single most important fact about a model-authored plan. |
| `app/api/plans.py` | **A** | S4 | Populate it via the existing `_latest_evaluation_ids` | Reuses A's own reader so both surfaces mean the same thing by "the evaluation that authorised this task". |
| `app/api/reasoning.py` | **A** | S4 | Add `source` (`fixture`/`live`) and `authorises_no_action` to `/explanation` and `/reports` | A model artefact must state on the contract that it grants nothing, and a replay and a live call carry different weight in a review. |
| `docs/openapi.json` | **A** | S9 | Regenerated | Compliant: regenerated, never hand-edited, in the same commit as the schema change. |
| `scripts/verify_phase3.py` | **A** | S7 | Extended with reflection, authorship, approval and execution checks | Addition-only. It previously stopped at "a candidate exists" and never selected the agent plan, so the assurance half of the chain was unverified. |
| `frontend/src/api/types.ts` | **D** | S5 | Add `source` / `authorises_no_action` to two response types | Mirrors the S4 contract change above. Normally D's to mirror — flagged for D. |
| `frontend/src/features/reports/ReportScreen.tsx` | **D** | S6 | Add `ExplanationPanel` | `api.explanation` existed with **no consumer**: the Explainer agent ran every time and nothing rendered it. Flagged for D. |
| `tests/unit/test_no_llm_in_services.py` | shared | S7 | Replaced the module-scope `app.agents` ban with `test_importing_the_engine_loads_no_model_sdk` | The only **deletion** of an existing assertion here. The old rule asserted a proxy: it forbade the orchestrator from calling the planner at all, and would still have passed had someone imported an SDK inside a function. The replacement asserts the real `LLM_MODE=off` invariant by observation. Strictly stronger, but it is a deletion and needs sign-off. |

Two new test files were **added**, not modified, which S7 permits:
`tests/unit/orchestrator/test_authorship_threading.py` (9 tests) and
`tests/e2e/test_agent_plan_selected.py` (6 tests).

### Defects this surfaced, for the record

- **B's authorship assurance was inert.** Implemented, unit-tested, never invoked.
- **The corroboration baseline was too narrow.** Once authorship was supplied, the fixture planner's
  citations — which are exactly the refs `_target_refs` hands it — were all refused as
  uncorroborated, so the agent plan could never clear the gate. A check that fires on correct
  behaviour is noise, and noise gets ignored.
- **`PlanTaskOut` lacked `assurance_id`** while its sibling `PlanTaskSummary` had it.
- **Two of my own assertions read fields that do not exist** (`blocking_reasons` instead of
  `blocking`) and passed vacuously. Caught by reverting the fix and confirming the test then fails.

## 4. Locking contracts before implementation

For every phase, before code:

1. **Response models** — the exact Pydantic classes and field names, in `app/schemas/`.
2. **Database changes** — columns, types, constraints, and which migration revision carries them.
3. **Config keys** — new keys, their defaults, and which file they live in.
4. **Service signatures** — the `execute(**kwargs)` inputs each new service needs.

Written down and agreed *first*. A contract settled after implementation is a rewrite wearing a
merge's clothes — that is what §2 cost.

## 5. Cadence

**Integrate continuously.** Land the smallest slice that works, against `main`, and repeat.

- Rebase onto `main` before starting significant work.
- Keep the integration branch continuously synchronised with `main`.
- When another stream changes a shared contract: rebase and resolve **that day**.
- A phase does not end with one enormous integration PR. The final PR should be integration work
  only.

### Definition of ready for the final phase PR

| Gate | How it is checked |
| --- | --- |
| Rebased onto latest `main` | `git rev-list --count HEAD..origin/main` is `0` |
| Zero merge conflicts | `gh api .../pulls/{n} --jq .mergeable` is `true` before requesting review |
| Full backend suite | `uv run pytest`, plus `ruff check` and `ruff format --check` |
| Full frontend suite | `typecheck`, `lint`, `tokens:check`, `format:check`, `test`, `build` |
| The product journey | `scripts/verify_phase2.py` (or its successor) against real Postgres |
| The console | `npm run verify:console` at 1920×1080 |
| No accumulated cross-stream change | the PR file list contains no file owned by another stream without a named, agreed request |

## 6. When the rule has to bend

It sometimes will: a seam is missing and the demo is two days away. Then the requirement is
**visibility, not permission**:

1. Make the smallest possible edit to the other stream's file.
2. Name the owner and the reason in the PR body, under its own heading.
3. Do not weaken anything to make it fit — least of all a test.
4. Open the seam properly in the next phase, so the exception does not become the pattern.

Phase 2's five crossings are exactly this case, and §1 is that disclosure written after the fact
rather than during. From Phase 3, it goes in the PR.
