# 38. Phase 4 — Verified Regulatory Intelligence

**Demo claim: "entitlements are computed from reviewed, cited policy — not from a model."**

**Phase gate ([`20-phased-delivery.md`](20-phased-delivery.md)): every rupee figure traces to a clause,
document and version.**

This is the authoritative contract and dependency map for all four streams. Planning only — nothing here
is implemented yet.

## The finding that shapes this phase

Phase 4 is **mostly not a build**. The generic machinery shipped in Phase 1 and hardened through Phase 3:
the pack format, the status ladder, the tri-state engine, the resolver, the citation plumbing, and every
database column Phase 4 needs. What is missing is an **approved pack**, three small integrity/identity
producers, and one endpoint that is still a fixture.

The scalability claim in [`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md) —
"moving from charter to verified changes pack data and status only" — is now testable. Phase 4 must not
disprove it by rewriting the engine.

## Reuse inventory — do not rebuild any of this

Verified against `main`. Anything listed here is a dependency, not a deliverable.

| Already shipped | Where | Phase 4 relies on it for |
| --- | --- | --- |
| Six-file pack format + `PackRule` / `LoadedPack` schemas | `app/policy/loader.py` | the verified pack's shape |
| Status ladder, incl. `verified → {approved}` | `loader.py` `_ALLOWED_STATUSES`, `_reject_for_mode` | refusing an ineligible pack |
| Approved-pack preconditions (`approval` + `reviewer_name`; clause refs on every computational rule) | `loader.py` `_validate_rules` | **already written and already tested** |
| `compute_pack_hash` over `pack/applicability/rules/review` only | `loader.py` `_HASHED_FILES` | stable identity across replays |
| Tri-state engine, `UNKNOWN` that cannot be coerced, five blocking gates, `EntitlementResult` | `app/policy/engine.py` | every entitlement figure |
| Resolver `resolve` / `select`, `RESOLVER_VERSION`, overlap → `needs_human` | `app/policy/resolver.py` | the jurisdiction resolver |
| `calculate()` / `CitedEntitlement`, `may_be_presented_as_current_law`, `has_citation` | `app/policy/entitlements.py` | the single public entry point |
| `gate_requirements()` and the seven `policy.*` constraint ids | `app/policy/requirements.py` | policy reaching the frozen gate |
| Cohort exposure, business constraints | `cohorts.py`, `business_constraints.py` | plan-level exposure limits |
| Six frozen checks, `policy_compliant` DSL, `_refuse_vacuous`, `POLICY_BEARING_ACTIONS` | `app/assurance/` | authorisation; **unchanged in Phase 4** |
| `pack_hash`, `content_hash`, `policy_clause.text`, `source_clause_refs`, `resolver_hash`, `entitlement_evaluation` | `app/models/policy.py`, migration `0001` | persistence — **no new schema, no migration** |
| Clause refs → `evidence_refs` → `decision_log` | `app/services/compensation.py`, `orchestrator._gate_input_coverage` | the provenance chain |
| 23-case harness; verified-ladder loader tests | `tests/unit/policy/` | the regression floor |
| Rendered policy screen consuming `ui_label`, `pack_hash`, `source_hash`, `source_clause_refs`, `excluded_rules`, `disclaimer` | `frontend/.../PolicyScreen.tsx` | the citation card |

**Consequence: Phase 4 introduces no new schema, no migration, no new assurance check, no new agent and no
new UI screen.** If a proposal requires one, it is out of scope until the team agrees otherwise.

## The eight real gaps

| # | Gap | Evidence on `main` | Blocked on |
| --- | --- | --- | --- |
| G1 | No approved pack exists | only `in-moca-charter-2019/2019.02`, `verified_mode_eligible: false` | **external**: primary CAR + SME |
| G2 | `verified` refuses at startup unconditionally | `config.py:444` raises before the loader is consulted | G1 |
| G3 | Source-document integrity is never checked | `content_sha256: PENDING_ARCHIVAL`, `archived: false`, no code reads either | nothing |
| G4 | `/incidents/{id}/policy` is still a fixture | `api/fixtures_router.py:54` serves `fixtures/api/policy.json` | nothing |
| G5 | `resolver_hash` has no producer | column exists; nothing writes it | nothing |
| G6 | Pack → database ingestion absent | `policy_clause`, `entitlement_evaluation` never written | nothing |
| G7 | Pack label is hardcoded, not read from the pack | `health.py:99` `_policy_ui_label` string switch | nothing |
| G8 | `POLICY_MODE=demo` can load nothing | no pack sets `demo_fixture: true` | nothing |

Deliberately **out of scope** (recorded so they are not rediscovered as surprises): `preserves_entitlement_types`
is declared in the rule DSL but unimplemented; `conditionally_required_facts` is loaded but unused;
`ApplicabilityResult.evidence_refs` is never populated; rule `scope` is not enforced; SDR→INR conversion
does not exist, so SDR rules yield a limit and never a payout; clause refs remain bare strings with no
`Citation` schema. Each is a separate reviewed change with its own tests, not Phase 4 scope creep.

## 1 · The verified India pack

New pack directory, same six-file format: `policy_packs/in-dgca-car-3m4/<revision>/`.

Promotion is **data and review**, not code. The loader already enforces every precondition:

1. `pack.yaml`: `status: approved` **and** `verified_mode_eligible: true`.
2. `review.yaml`: non-null `approval` **and** `reviewer_name`, from an authorised SME.
3. Every computational rule carries non-empty `source_clause_refs`.
4. No `superseded_suspected` rule left un-excluded; no duplicate ids; directory matches `pack.yaml`.
5. `precedence.conflict_rules_defined` decided by the reviewer, with its source basis.

Two questions the primary source must settle before any figure is called current, both already recorded in
`source-metadata.yaml` and `review.yaml`: the reported **August 2024 Part IV revision**, and the reported
**February 2026 Part II amendment** moving free cancellation from 24 to 48 hours (currently
`excluded_from_evaluation`).

The charter pack is **not** deleted or edited. It stays loadable, `official_guidance_dated`, and remains
the fallback whenever the verified pack is unavailable. Replay of a past charter-mode decision must
continue to reproduce that decision.

## 2 · Source-document hashes

The archived primary document and its hash are the legal source; extracted text is not.

- `source-metadata.yaml` gains a real `content_sha256` and `archived: true`.
- A verified-mode load **must fail** when the hash is `PENDING_ARCHIVAL`, the file is missing, or the
  recorded hash does not match the file on disk. Reason code: `SOURCE_DOCUMENT_UNVERIFIED`.
- `content_sha256` stays outside `_HASHED_FILES`, which is deliberate: recording a retrieval date must not
  invalidate the pack hash a past evaluation was pinned to. Source integrity and pack identity are
  separate facts and stay separate.
- Redistribution is unresolved. If the PDF may not be committed, the pack references it by official URL
  and hash, and verified mode requires the operator to supply the archived file locally. That constraint
  is stated in the pack, not worked around.

## 3 · Deterministic entitlement rules

No new operators unless the primary source demands one. Adding an operator or a formula is a reviewed
engine change with tests — the `_FORMULA_INPUTS` registry is closed on purpose.

Every rule the verified pack encodes must arrive with: clause refs, a plain-language interpretation, its
required facts, the computed output and currency, edge cases, and test cases. Rules that cannot be read
unambiguously from the primary source stay `draft` and therefore compute nothing in verified mode.

The invariants the 23 existing cases already pin — most importantly that **no delay ever produces a cash
payout** — carry forward unchanged. The verified pack adds its own cases; it does not relax existing ones.

## 4 · Citation and provenance chain

The chain exists end to end today and Phase 4 completes it rather than redesigning it:

```text
rule.source_clause_refs
  → EntitlementResult / CitedEntitlement (+ pack id, version, hash, status)
  → ServiceResult.evidence_refs                     (compensation.py)
  → assurance evidence_refs + decision_log detail    (gate + _gate_input_coverage)
  → GET /incidents/{id}/policy                       (G4: becomes real)
  → PolicyScreen citation card                       (renders ui_label + hashes verbatim)
```

Phase 4 additions: persist the evaluation and the clause text (G6) so a citation survives without
re-reading YAML; produce `resolver_hash` (G5) so an applicability decision is as replayable as an
entitlement; read the badge from the pack (G7) so the UI cannot claim a standing the pack does not have.

`_reason()` in `compensation.py` remains the only place legal standing is rendered as prose — `"current
law"` only when `may_be_presented_as_current_law` is true. That single string is a safety boundary; it is
not to be duplicated.

## 5 · Jurisdiction resolver

Already implemented and jurisdiction-neutral. Phase 4 exercises it rather than extending it:

- with one pack, `select()` returns it or `needs_human`;
- with two packs, overlap without a reviewed conflict rule is `needs_human` — no "most favourable to the
  passenger" default is assumed anywhere;
- `resolver_hash` records which resolution produced a decision.

A second jurisdiction remains **optional structural proof only**, and only after India works end to end.
It is second on the cut list in `20-phased-delivery.md`. Nothing in Phase 4 may claim EU/UK/US/Montreal
coverage.

## 6 · Ownership

Exactly as `OWNERS` and [`28-parallel-workstreams.md`](28-parallel-workstreams.md) already assign it. No
new ownership rules.

| Stream | Phase 4 deliverables | Paths |
| --- | --- | --- |
| **B · Assurance & Policy** | verified pack (G1); source-hash integrity gate (G3); `resolver_hash` producer (G5); demo fixture pack (G8); new pack test cases | `backend/app/policy/**`, `policy_packs/**`, `config/**`, `backend/tests/unit/{policy,assurance}/**` |
| **A · Core & API** | real `GET /incidents/{id}/policy` (G4); `verified` mode reachable (G2); badge from pack (G7); this doc | `backend/app/api/**`, `backend/app/config.py`, `docs/**`, `scripts/**` |
| **C · Data & Services** | pack → DB ingestion, clause text, `entitlement_evaluation` (G6); keep `fixtures/api/policy.json` in step | `backend/app/{models,db,services}/**`, `data/**`, `fixtures/**`, `backend/tests/contract/**` |
| **D · Frontend** | render verified badge/hashes already returned; no new screen | `frontend/**` |

**Two seams that will otherwise cause a collision.**

- `fixtures/api/policy.json` is owned by **C**, its shape is produced by **A**'s endpoint from **B**'s
  engine, and **D** renders it verbatim. When G4 lands, A must match the committed fixture byte-for-byte
  or C must change the fixture first. It is a contract, not sample data.
- Flipping verified mode requires changing `backend/tests/unit/test_config_fail_closed.py`, which is
  **SHARED** and asserts verified is unreachable. That assertion exists because no approved pack exists.
  Changing it is a **whole-team decision made only once G1 is genuinely satisfied** — not a stream's call,
  and not a prerequisite anyone may work around.

## 7 · Dependencies and order

```text
Track 0  external, blocks G1/G2 only   primary CAR + amendments → rule sheet → SME sign-off
Wave 1   no external dependency        G3  G5  G7  G8  →  G4        (G6 anywhere in 1–2)
Wave 2   needs Track 0                 G1  →  G2
Wave 3   optional, after India works   second-jurisdiction structural proof
```

Wave 1 is the whole point of the ordering: **every gap except G1 and G2 is buildable today**, so an absent
primary source costs the verified badge and nothing else. Sequence inside Wave 1:

1. **G3, G5, G7, G8** — independent, small, no cross-stream handshake.
2. **G4** — after G3/G7, so the real endpoint reports true integrity and the true badge on day one.
3. **G6** — independent of the rest; do it before G4 if a citation must survive a pack file moving.

Track 0 is where the phase can actually stall, and it is not code. Acquisition ownership and escalation
are already specified in [`24-input-acquisition.md`](24-input-acquisition.md) (P0-1, P0-2): development
attempts public retrieval first; the team is needed only for blocked access and for nominating the
authorised reviewer.

## 8 · Definition of done and verification gates

**Per gap.**

| Gap | Done when |
| --- | --- |
| G1 | pack loads in verified mode; every computational rule cites a clause; `review.yaml` names the reviewer and records approval; its own test cases pass |
| G2 | `POLICY_MODE=verified` starts **only** with an eligible archived pack, and still refuses with a named reason code otherwise |
| G3 | a hash mismatch, a missing file or `PENDING_ARCHIVAL` each refuse a verified load with `SOURCE_DOCUMENT_UNVERIFIED` |
| G4 | endpoint is real, byte-compatible with `fixtures/api/policy.json`, and the fixture route is deleted |
| G5 | every persisted applicability row carries a `resolver_hash` that is stable for identical inputs |
| G6 | an entitlement decision is reproducible from the database alone, including clause text |
| G7 | the badge is `LoadedPack.ui_label`; no mode string is hardcoded in the API layer |
| G8 | `POLICY_MODE=demo` loads a fictional pack that cites nothing and claims nothing |

**Phase gates — all must hold.**

1. **Traceability.** Every rupee figure in the demo traces to clause → document → version → hash. This is
   the phase gate from `20-phased-delivery.md`; if one figure cannot be traced, Phase 4 is not done.
2. **No standing without review.** With the charter pack, no output says "current law" and the dated badge
   is visible. With the verified pack, "current law" appears only because an SME signed the pack.
3. **Fail closed.** Missing pack, hash mismatch, missing required fact, superseded rule, or unresolved
   overlap each yield `needs_human` with a reason code and no authoritative figure — the table in
   `19-jurisdiction-and-policy-packs.md`.
4. **No regression.** The existing suite passes; the 23 charter cases are unchanged; `LLM_MODE=off` and
   charter mode behave exactly as they do today. No new migration in the diff.
5. **Frozen boundaries intact.** Still six assurance checks; `policy_compliant` and `evidence_complete`
   unchanged; `_refuse_vacuous` not bypassed; no policy decision made by a model; retrieval never selects
   a jurisdiction, computes an amount or promotes a rule.
6. **Replay.** A decision recorded under the charter pack still replays as that decision after the
   verified pack exists.

## What Phase 4 must never do

Compute an entitlement with a model. Present a dated figure as current law. Assume a favourability rule
nobody reviewed. Promote a rule without SME sign-off. Silently degrade verified → charter — degradation is
explicit, reported, and off by default. Delete or weaken a guard test to make verified mode reachable.
