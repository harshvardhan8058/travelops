# Phase 2 — Stream D plan

**Status: architecture decisions final. Implementation blocked until cross-stream ownership and
dependency contracts are aligned — see §13.**

> **Numbering note.** The review's decisions are **D1–D3** (§0). This document's frontend
> dependency asks were previously numbered D1–D7 and are now **FE-1–FE-7** (§2), so a decision
> and a dependency can never be confused in a standup.

---

## 0. Decisions applied (final)

| # | Decision | What it changes here |
| --- | --- | --- |
| **D1** | **Plan-level assurance is incident-group scoped.** | §9 rewritten. The matrix spans every incident in a group, not one plan. Creates a real dependency: there is no group-scoped assurance endpoint and no incident list on the group payload — see FE-6 and FE-8 |
| **D2** | **What-if is in scope as a bounded, zero-write, deterministic re-evaluation. Explicitly not a simulation engine or digital twin.** Boundary to be recorded in `docs/DECISIONS.md` | §7 rewritten around that boundary, with the UI obligations it implies. Draft entry for `DECISIONS.md` in §14 — `docs/` is Stream A's path, so Stream A commits it |
| **D3** | **Plan approval may cover low and medium risk actions. High-risk actions always require their own action-level approval. Approval can cover risk, never failed evidence.** | New §10. Changes the approval surface from one control to two, with an explicit non-coverable set |

**Cut, confirmed:** Open-Meteo / historical provider expansion is non-critical and is cut before
any core Phase 2 feature. It appears in no work item below and no UI depends on it.

## 0.1 Work register and build order

`C2-n` was not defined in the repository, so this is the binding proposal — **confirm the labels
in review; the order is taken as given and is not re-litigated.**

| ID | Work item | Section |
| --- | --- | --- |
| **C2-1** | Network Command Center | §4 |
| **C2-2** | Disruption / Cascade Explorer | §5 |
| **C2-3** | Shared groundwork — types, graph primitives, deterministic layout, keyboard model | §3 |
| **C2-4** | What-if: bounded zero-write re-evaluation | §7 |
| **C2-5** | Plan-level assurance, group-scoped, incl. the D3 approval model | §9, §9.1 |
| **C2-6** | Impact views — crew now, others on FE-1 | §6 |
| **C2-7** | Replay | §10 |
| **C2-8** | Recovery-plan comparison | §8 |
| **C2-9** | Hardening — projector rehearsal, accessibility sweep, five-state audit | §12 |

**Mandated order:** `C2-3 → C2-1 / C2-2 → C2-5 / C2-6 → C2-4 → C2-7 → C2-8 → C2-9`
(items separated by `/` may run in parallel).

This order is coherent with the dependency graph: groundwork precedes everything; the two
read-only screens land next; group-scoped assurance and crew impact follow because both consume
the same group→incident join; what-if follows assurance because it re-uses the check renderer;
replay, then comparison, then hardening last.

Phase 1 is closed. This plans the seven Phase 2 frontend features against the contracts the
backend actually serves today, verified by reading `docs/openapi.json`, the Pydantic response
models under `backend/app/schemas/`, and the committed fixtures — not from memory.

Phase 2's demo claim is fixed by [`docs/20-phased-delivery.md`](../../docs/20-phased-delivery.md):
**"disruption is never one flight."** Every feature below is judged on whether it serves that
claim, and its gate is that one weather event at BLR produces a traceable multi-flight,
multi-pairing impact set.

---

## 1. What the contracts support today

Fourteen endpoints exist. Six are real (DB-backed); eight are still fixture-backed.

| Endpoint | Backing | What Phase 2 uses it for |
| --- | --- | --- |
| `GET /incidents/{id}` | real | evidence, plan, tasks, actions, state rail |
| `GET /incidents/{id}/assurance` | real | plan-level gate aggregation |
| `GET /incidents/{id}/timeline` | real | replay |
| `POST /incidents/{id}/run` | real | advancing the workflow |
| `POST /assurance/{id}/decision` | real | approvals |
| `GET /health/ready`, `GET /system/mode` | real | command centre health and modes |
| `GET /flights` | **fixture** | network strip, flight board, `incident_reference` join |
| `GET /incident-groups`, `/incident-groups/{id}` | **fixture** | cascade explorer, group rollups |
| `GET /incidents/{id}/policy` | **fixture** | cause comparison (the only real what-if) |
| `GET /reports/{id}` | **fixture** | plan-level metrics |
| `GET /sources` | **fixture** | provenance ledger |

### Feature readiness, honestly

| Feature | Verdict | Why |
| --- | --- | --- |
| Network Command Center | **Buildable now** | `/flights`, `/incident-groups`, `/system/mode`, `/health/ready`, `/sources` cover it. No map — see FE-3 |
| Cascade Explorer | **Buildable now** | `crew_pairings[].source_flight` + `mechanism` are real edges. Connections and hotels are counts only |
| Impact views | **Crew: buildable. Passenger/connection/hotel: blocked** | Only crew has per-entity records. See FE-1 |
| What-if (D2 bounded re-evaluation) | **Variant 1 buildable now; variant 2 needs FE-9** | `policy.cause_comparison` is real and zero-write. Gate re-evaluation needs an endpoint |
| Recovery-plan comparison | **Blocked** | `IncidentDetailResponse.plan` is a single object. No alternatives, no versions. See FE-4 |
| Plan-level assurance | **Buildable now** | Aggregates the six checks across the plan's evaluations |
| Replay | **Buildable now** | Timeline entries carry `occurred_at`, `stage`, `actor_kind`, `detail`, `correlation_id` |

### The invention traps, named so nobody walks into them

1. **No coordinates anywhere.** No response schema carries latitude or longitude
   (`grep latitude backend/app/schemas backend/app/api` → nothing). A geographic map cannot be
   drawn without either inventing positions or adding a field. The Command Center is therefore
   designed non-geographically, which also matches `docs/21`'s stance that decorative
   visualisation is not the product.
2. **No per-entity passenger, connection or hotel records over HTTP.** The services compute
   them — `scenario_queries.load_connection_inputs()`, `load_crew_impact_inputs()`,
   `affected_pairings_recursive()` — and the orchestrator stores structured results in
   `action.payload`, but **`ActionSummary` has no `payload` field**. All that reaches the UI is
   free text: `"8 itineraries no longer feasible across 1 flights"`. Parsing numbers out of a
   sentence would be fabrication with extra steps.
3. **`rollups` are counts, not collections.** `connections_at_risk: 22` and
   `candidate_hotels: 11` have no corresponding arrays, so they may be rendered as totals and
   never as nodes, rows or a list of 22 things.
4. **One plan per incident.** Nothing in the contract expresses "plan A versus plan B".
5. **The cascade group's flights carry no `incident_reference`.** `crew_pairings[].source_flight`
   is a flight *number*, and `flights[]` has `id`, `flight_number`, `route`, `delay_minutes`,
   `passengers`, `state` — no incident link. Navigation from a cascade node to its workspace
   works only by joining on `id` against `/flights`, which today returns 4 flights against the
   group's 8.

---

## 2. Dependencies to route

Ordered by how much they unblock. Each is a request, not a workaround.

| # | Ask | Owner | Unblocks | Note |
| --- | --- | --- | --- | --- |
| **FE-1** | `payload: dict \| None` on `ActionSummary`, or `GET /incidents/{id}/actions/{action_id}` | A | Passenger, connection and hotel impact views | The data is already persisted; this is exposure, not new computation |
| **FE-2** | Wire `/incident-groups/*` to `scenario_queries.cascade_rollup()` | A + C | Cascade Explorer on real data | `api/__init__.py` already flags it "needs Stream C's cascade data" |
| **FE-3** | `latitude`, `longitude` on `/flights.network[]` | C | A geographic Command Center | OurAirports is already loaded. **Only if we want a map at all** — recommend not |
| **FE-4** | A plan-alternatives contract | A | Recovery-plan comparison | Options in §7. Cheapest is `plan.supersedes` / `plan.version` |
| ~~FE-5~~ | ~~A what-if contract~~ | — | — | **Superseded by FE-9**, which states the D2 boundary precisely |
| **FE-6** | `incident_reference` on `/incident-groups/{id}.flights[]` | A | Cascade node → workspace navigation | One field; removes a fragile cross-endpoint join |
| **FE-7** | `reason_code` on `ActionSummary` | A | Refusal copy without prefix matching | Carried over from the Phase 1 review |
| **FE-8** | Group-scoped assurance: either `GET /incident-groups/{id}/assurance`, or `incidents[]` on the group payload so the client can fan out | A | **C2-5 under D1** | D1 made this load-bearing. Fan-out over N incidents is acceptable for a demo-scale group of 8; a single endpoint is better |
| **FE-9** | What-if contract per D2 (replaces the old speculative ask): zero-write, deterministic, returns the same six checks and the same policy result shape for altered inputs | A + B | **C2-4** | Must persist nothing and must not transition state. See §7 |
| **FE-10** | Plan-level decision contract per D3: a decision covering multiple low/medium evaluations, rejecting high-risk ones server-side | A + B | **C2-5 / §10** | The server must enforce the tier rule; the UI must not be the only thing preventing a high-risk bulk approval |

**Recommendation:** FE-1, FE-2 and FE-6 are small and unblock four of the seven features. FE-3 is a
"decline unless someone insists". FE-8, FE-9 and FE-10 are created by decisions D1, D2 and D3
and are now on the critical path.

---

## 3. Shared groundwork (C2-3)

Common to several features, built once.

**Files**

| File | Purpose |
| --- | --- |
| `src/api/types.ts` | Extend with `IncidentGroupSummary`, `CascadeNode`/`CascadeEdge` view models, `ReportResponse`, `SourceRow`. Mirror the API; never widen beyond it |
| `src/components/ui/derivation.ts` | New adapters: `rollupDerivation`, `pairingDerivation`, `mechanismDerivation`, `metricDerivation`, `checkAggregateDerivation` |
| `src/components/ui/Graph.tsx` | Presentational SVG primitives — `<GraphNode>`, `<GraphEdge>`, `<GraphLegend>`. No layout logic, no data fetching |
| `src/features/cascade/layout.ts` | Pure deterministic layered layout. No dependency, unit-testable, no randomness |
| `src/components/ui/primitives.tsx` | Add `MetricTile`, `Sparkbar` (single-hue accent ramp), `FilterChips` |
| `src/hooks/useKeyboardList.ts` | Roving-tabindex list navigation (`j`/`k`/`Enter`), reused by every list and the graph |

**Design rules inherited, not re-litigated:** graphite base, instrument cyan for brand/active
only, green/amber/red exclusively for operational state, 1px borders, `rounded-md`, 14px body,
34px rows, 100–220ms ease-out, Lucide 16px, every number through `MonoValue`, every status
through `StateBadge`, every derived figure through `WhyPopover`, every data surface with
`ProvenanceDot`.

**Contrast is now a measured gate, not a claim.** The Phase 1 rehearsal found `--fg-muted` at
3.74:1. Every new surface must report zero text below 4.5:1 from the same harness before its
acceptance criteria pass.

---

## 4. Network Command Center (C2-1)

The opening shot: one screen that answers "what is the state of the network, and is any of this
real?" without scrolling.

| Aspect | Detail |
| --- | --- |
| **Route** | `/` replaces today's Ops Board, which becomes the flight-board zone within it |
| **Files** | `src/features/command-center/CommandCenter.tsx`, `NetworkStrip.tsx` (lifted from `ops-board/`), `ActiveIncidents.tsx`, `SystemHealthStrip.tsx`, `ProvenanceSummary.tsx` |
| **API** | `GET /flights` (network[], flights[]), `GET /incident-groups` (rollups, `awaiting_approval_count`), `GET /system/mode`, `GET /health/ready`, `GET /sources` |
| **Data contracts** | Airport tiles: `airport_icao`, `iata`, `city`, `wind_speed_kt`, `visibility_m`, `ceiling_ft`, `precipitation`, `risk_index`, `risk_level`, `observation_age_minutes`, `provenance`. Group cards: `reference`, `root_cause`, `airport_icao`, `severity`, `state`, `opened_at`, `rollups{flights_affected, passengers_affected, connections_at_risk, crew_pairings_affected}`, `awaiting_approval_count`. Health: `dependencies{database,redis}.status`, `degradations[]`. Sources: 10 rows of `kind`/`provider`/`current_mode`/`last_checked`/`licence`/`health` — summarised as counts by `kind`, with the ledger itself at `/sources` |
| **State** | All server state via React Query, `staleTime` 5s, `refetchInterval` 15s for flights, 30s for mode/health. Client state: active filter chip, sort column, selected group — all URL search params so a demo can be deep-linked and reloaded mid-presentation |
| **Interaction** | Filter chips (All / At risk / Disrupted / In recovery / Resolved); sortable columns with non-normal states pinned on top by default; click a group card → Cascade Explorer; click a flight row → workspace; `g` then `o` returns here; `/` focuses filter |
| **Accessibility** | Chips are a `role="radiogroup"`; sortable headers are buttons with `aria-sort`; group cards are links, not click-handlers on divs; every tile's provenance dot has an accessible name; keyboard list navigation via `useKeyboardList` with a visible 2px focus ring |
| **Responsive** | 1920: network strip + flight board + group column + timeline rail. 1440: group column moves below the board. 1280: timeline rail hides (existing `xl:` behaviour), strip scrolls horizontally. No layout below 1024 — the product targets a projector and an OCC desk |
| **Tests** | Unit: sort comparator (non-normal first, stable), chip filter predicates, rollup formatting with a missing key. Harness: five states per zone, contrast sweep, no horizontal page overflow at 1920/1440/1280 |
| **Acceptance** | Every count traced to a response field with zero hardcoded totals; a stale observation shows amber age *before* it causes a gate failure; degraded banner names the exact provider; no zero substituted for an absent count; all five states designed |
| **Demo value** | Ten seconds to "eight flights, 604 passengers, nine rotations, two awaiting me" — with a provenance dot on every panel. Answers judge question 1 and pre-empts "is this real?" |

**Explicitly not built:** a geographic map (no coordinates — FE-3), and any airport not in the
configured set.

---

## 5. Interactive Disruption / Cascade Explorer (C2-2)

The screen that makes "8 flights → 9 rotations" countable instead of asserted. This is Phase 2's
centrepiece.

| Aspect | Detail |
| --- | --- |
| **Route** | `/cascade/:groupId` (replaces the placeholder) |
| **Files** | `src/features/cascade/CascadeExplorer.tsx`, `CascadeGraph.tsx`, `layout.ts`, `MechanismLegend.tsx`, `PairingTable.tsx`, `NodeInspector.tsx` |
| **API** | `GET /incident-groups/{id}` — real once FE-2 lands, fixture-shaped until then. `GET /flights` for the `id` → `incident_reference` join (FE-6 removes this) |
| **Data contracts** | Nodes come from exactly three arrays: one event node from `root_cause` + `airport_icao`; `flights[]` → flight nodes (`id`, `flight_number`, `route`, `delay_minutes`, `passengers`, `state`); `crew_pairings[]` → pairing nodes (`pairing_reference`, `base_icao`, `affected_leg`, `mechanism`, `detail`, `at_risk`). Edges are `pairing.source_flight` → matching `flight.flight_number`, labelled with `mechanism` and explained by `mechanism_legend[mechanism]`. Node size from `passengers`; border from `state`. Counts from `rollups`. `why_nine_not_eight` rendered verbatim |
| **Not modelled** | Connection and hotel nodes. `connections_at_risk: 22` and `candidate_hotels: 11` are totals with no arrays behind them, so they render as rollup tiles only. Fabricating 22 connection nodes would be the single worst thing this screen could do |
| **State** | Query cache for the group. Client: selected node, hovered edge, layer toggles (crew / flights), all in URL params. Layout is pure: `layout(nodes, edges) → positions`, deterministic, memoised on the payload |
| **Interaction** | Click node → inspector panel with that record's fields and a `WhyPopover`; hover or focus an edge → mechanism sentence from `mechanism_legend`; layer toggles reduce clutter; selecting a flight node filters the pairing table; `Explain this cascade` deferred to Phase 3 (Explainer) rather than faked |
| **Accessibility** | **The table is the primary representation and the graph is the enhancement.** `PairingTable` lists all nine pairings with mechanism, leg, base and detail — fully navigable, sortable, screen-reader complete. The SVG has `role="img"` plus a text summary, and its nodes are a roving-tabindex group so keyboard users traverse event → flights → pairings. Mechanism is never encoded by line style alone: every edge carries a text label. Reduced motion drops layout transitions to opacity |
| **Responsive** | Fixed `viewBox` scaled to fit, no free pan/zoom — predictable for a presenter. 1920: graph + inspector + table. 1440: table collapses to a disclosure. 1280: graph above table, single column |
| **Tests** | Unit: `layout.ts` determinism (same input → identical positions), edge derivation joins only on exact `flight_number`, unmatched pairings surface as "source flight not in this group" rather than being dropped, node count equals array length. Harness: nine pairing nodes with nine labelled edges, every label ∈ `mechanism_legend` keys, counts equal `rollups`, contrast sweep, keyboard traversal reaches every node |
| **Acceptance** | A reviewer counts nine pairing nodes unaided and reads why each is affected; every total matches `rollups`; no node exists without a backing record; the four mechanisms appear as words; `why_nine_not_eight` is quoted, not paraphrased |
| **Demo value** | Answers the mentor's original challenge visually. This is the Phase 2 gate made watchable |

---

## 6. Impact views — passenger, crew, connection, hotel (C2-6)

| Aspect | Detail |
| --- | --- |
| **Route** | `/impact/:groupId` with four tabs, deep-linked (`?view=crew`) |
| **Files** | `src/features/impact/ImpactViews.tsx`, `CrewImpact.tsx`, `RollupOnly.tsx`, `ActionEvidence.tsx` |
| **API** | `GET /incident-groups/{id}` (crew per-entity + all rollups), `GET /incidents/{id}` (`affected_entities`, `actions[]`), `GET /reports/{id}` (`passengers_affected`, `connections_identified_at_risk`, `passengers_reaccommodated`, `notifications_real`, `notifications_simulated`, `total_cost_inr`). **Blocked on FE-1 for anything per-entity beyond crew** |
| **Data contracts** | Crew: the nine `crew_pairings[]` records — the only per-entity impact data that exists today. Passengers: `affected_entities.passengers` and `bookings` (real API returns at most these two, and `{}` when there are no bookings) plus report metrics. Connections: `rollups.connections_at_risk` + `report.connections_identified_at_risk`. Hotels: `rollups.candidate_hotels`. Action evidence: `actions[].reason` rendered **as a quoted string attributed to its actor**, never parsed for numbers |
| **Phasing** | **Ship crew impact first** — it is complete and it is the Phase 2 story. Passenger, connection and hotel tabs ship as rollup-plus-evidence views that state plainly which per-entity data the endpoint does not return, and become real lists the day FE-1 lands. The tab shells are built so that change is additive |
| **State** | Query cache keyed by group and incident. Client: active tab, sort, selected pairing — URL params |
| **Interaction** | Sort and filter the pairing table by mechanism, base, at-risk; select a pairing → detail with `mechanism_legend` explanation; each rollup tile has a `WhyPopover` naming the endpoint and field it came from; a missing count renders `—` with "not computed by this endpoint", never `0` |
| **Accessibility** | Tabs are a real `role="tablist"` with arrow-key movement and `aria-controls`; tables have scope-d headers and `aria-sort`; the "not returned by this endpoint" copy is text, not a tooltip-only affordance |
| **Responsive** | 1920/1440: table plus detail side by side. 1280: detail becomes a disclosure under the selected row |
| **Tests** | Unit: rollup renderer distinguishes `0` from absent; mechanism filter predicates; action-reason renderer escapes and never extracts digits. Harness: crew tab lists nine pairings, absent-data copy present on the other three tabs, contrast sweep |
| **Acceptance** | Zero fabricated entity rows; every count traceable to a named field; a `0` appears only when the API returned `0`; each tab states its own data limits |
| **Demo value** | "Nine rotations, and here is each one and why" — the concrete half of the cascade claim. The honesty of the other three tabs is itself a differentiator against a demo that invents 22 connection rows |

---

## 7. What-if — bounded zero-write re-evaluation (C2-4)

**D2 is the whole specification.** What-if is a *re-evaluation*, not a simulation. The distinction
is not pedantry: a simulation engine would have to model outcomes the system cannot observe, which
is precisely the overreach `docs/07` and `docs/25` treat as a failure condition.

### The boundary, stated as UI obligations

| Rule | UI consequence |
| --- | --- |
| **Zero writes.** No row is created, updated or deleted | No affordance that reads as an action. No "apply", no "execute", no "save this scenario" |
| **No state transitions.** The incident's state, plan and tasks are untouched | The state rail is rendered read-only and visibly frozen while a what-if is on screen |
| **Deterministic.** Same inputs, same output, every time | No timestamps, no random ids and no "generated at" in the result view; re-running shows an identical panel |
| **Same semantics as the real gate.** The six checks in `CHECK_ORDER`, the same `config_version` and `config_hash` | Reuses `AssurancePanel` unchanged. A hypothetical that renders differently from a real evaluation would teach the wrong thing |
| **Not a digital twin.** It re-evaluates recorded facts under altered inputs; it does not predict | Copy says "re-evaluated", never "simulated", "predicted" or "forecast". No confidence, no probability |
| **Always distinguishable from the record** | Persistent watermark on every what-if panel, a distinct route, and a banner stating that nothing here was recorded. Watermark uses border and label, not a status colour |

### Two variants, one shell

1. **Policy cause re-evaluation — available now.** `policy.cause_comparison` already returns the
   same incident recomputed as a cancellation caused by crew rostering: `cash_inr: 5000`,
   `formula_used: least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000`, its own `rules_fired`
   and `source_clause_refs`. Zero writes by construction — it is a `GET`.
2. **Gate re-evaluation — needs FE-9.** Altering a bounded input set and re-running the six checks.
   Bounded means an enumerated, server-validated set (e.g. observation freshness, risk band, action
   tier), never free-form input, because an unbounded input space is a simulator by another name.

| Aspect | Detail |
| --- | --- |
| **Route** | `/policy/:incidentId` hosts variant 1. `/what-if/:incidentId` hosts variant 2 once FE-9 lands |
| **Files** | `src/features/policy-citation/PolicyScreen.tsx`, `CauseComparison.tsx`, `EntitlementBreakdown.tsx`, `CitationCard.tsx`, `PackStatusBanner.tsx`, `src/features/what-if/WhatIfShell.tsx`, `InputBounds.tsx`, `ReEvaluationResult.tsx` |
| **API** | `GET /incidents/{id}/policy` (all of `pack`, `applicability[]`, `entitlements[]`, `cause_assessment`, `cause_comparison`, `excluded_rules[]`, `disclaimer`). Variant 2: FE-9 |
| **Data contracts** | Variant 1 diffs the recorded result against `cause_comparison.alternative` on `cash_inr`, `rules_fired`, `source_clause_refs`, `formula_used`. Variant 2 renders `CheckResult[]` plus a decision, identical in shape to a real evaluation, tagged `hypothetical: true` |
| **Interaction** | A two-state `radiogroup`, not a checkbox — a what-if is a choice between named alternatives, never a toggle that might read as "on". Changed rows marked by icon **and** word. `outcome: "not_owed"` renders as **Not owed**, a result. `missing_facts` renders `needs_human` naming the exact fact |
| **Non-negotiable** | `pack.ui_label` verbatim (`MoCA PASSENGER CHARTER · FEB 2019 · PENDING CAR VERIFICATION`). No amount computed client-side. No what-if result ever offers an execute path |
| **Accessibility** | `radiogroup` with arrow-key selection; diff announced `aria-live="polite"`; watermark is text in the accessibility tree, not a background image; clause text selectable |
| **Responsive** | 1920/1440 side-by-side; 1280 stacked with a sticky header naming each side |
| **Tests** | Unit: diff builder marks only fields that differ; `not_owed` renderer; missing-fact path; formula passthrough byte-identical; hypothetical results can never produce an action payload. Harness: banner matches `ui_label` exactly; both causes render; amounts differ; watermark present on every what-if surface; contrast sweep |
| **Acceptance** | Every figure carries a rule ID and clause ref; nothing client-computed; no write request is issued from any what-if surface (asserted by intercepting the network layer in test); the word "simulation" appears nowhere in the UI |
| **Demo value** | Cause flips from weather to crew rostering and ₹0 becomes ₹5,000 with different rules firing — a rules engine proving itself in one click, with no risk of a judge thinking we built a twin |

**Additional non-negotiables carried from the policy screen:** `excluded_rules` show a supersession
notice and are visibly not evaluated; the pack badge is never upgraded, never manually overridden and
never described as current law; the selected cause is a URL param so a compared state is shareable.

---

## 8. Recovery-plan comparison (C2-8)

**Blocked.** `IncidentDetailResponse.plan` is one nullable object; nothing expresses alternatives.
Three ways forward, cheapest first:

| Option | Contract change | Comparison it enables | Verdict |
| --- | --- | --- | --- |
| **A. Plan versions** | `plan.version` + `plan.supersedes` (or `GET /incidents/{id}/plans`) | The same incident re-planned after a rejection | Cheapest, real, and rejection already exists |
| **B. Generator comparison** | none — needs Phase 3 | Fallback playbook versus Planner output on the same incident | Free once `LLM_MODE` flips; the strongest story |
| **C. Candidate plans** | a planner that emits alternatives | Plan A versus plan B before execution | Largest change; defer |

**Recommendation:** design the component now against option A's shape, ship it in Phase 3 with
option B's data, and do not build a comparison of one plan against itself in the meantime.

| Aspect | Detail (design-ready, not build-ready) |
| --- | --- |
| **Files** | `src/features/plan-compare/PlanComparison.tsx`, `TaskDiff.tsx` |
| **Data contracts** | Two `PlanSummary` objects; diff on `tasks[].action_type` and `task_order`; per-task `assurance_id` → decision; generator attribution for each side |
| **Interaction** | Side-by-side task lists, aligned by `action_type`, with added/removed/reordered marked by icon and word; generator chip per side; per-task gate decision beside each |
| **Acceptance** | Diff computed only from returned tasks; no scoring, no "better plan" claim — the gate decides, not the UI; both generators named unambiguously |
| **Demo value** | "The model proposed this; the deterministic playbook proposed that; the same gate judged both." Phase 3 material |

---

## 9. Plan-level assurance, incident-group scoped (C2-5)

**D1 sets the scope: the group, not the plan.** That is the right call for Phase 2 — "disruption is
never one flight" applies to the gate as much as to the cascade — and it changes the shape of the
work. One incident's six-by-N matrix is a component; the group view is a matrix of matrices, and it
needs a group→incident join that no endpoint currently provides (**FE-8**).

| Aspect | Detail |
| --- | --- |
| **Route** | `/assurance/:groupId`, with the single-incident matrix reused as a zone on `/incidents/:id` |
| **Files** | `src/features/assurance/GroupAssuranceMatrix.tsx`, `IncidentAssuranceRow.tsx`, `PlanAssuranceMatrix.tsx`, `CheckSummaryRow.tsx`; reuses `AssurancePanel.tsx` unchanged |
| **API** | Per incident: `GET /incidents/{id}/assurance` (`evaluations[]{id, plan_task_id, action_type, decision, risk_tier, checks[], blocking[], config_version, config_hash, warn_permitted_by_config, human_decision}`, `awaiting_approval_count`, `incident_reference`). Group membership via **FE-8**; until it lands, the join is `/incident-groups/{id}.flights[].id` against `/flights[].incident_reference`, which resolves only the flights `/flights` returns |
| **Data contracts** | Two levels. **Group level:** one row per incident — reference, state, task count, decision counts, `awaiting_approval_count`, blocking checks present. **Incident level:** tasks × `CHECK_ORDER`, each cell `PASS`/`WARN`/`FAIL` as icon **and** word **and** colour. Aggregates are counts only, computed from returned records: decisions by type, which check blocks most often, tier distribution, decisions recorded. `config_version` and `config_hash` shown per incident, and flagged when they differ **between** incidents in the group — a group whose incidents were judged under two config hashes is a fact a reviewer must see, not a detail to smooth over |
| **Never** | An aggregate "assurance score", percentage or average, at either level. The gate is fail-closed and ordered; a mean of six checks is a fiction (`docs/18`). Also never a group-level pass: a group is not "assured" because most of its incidents are |
| **Interaction** | Click an incident row → its matrix; click a cell → the existing `AssurancePanel` for that task; click a column header → every task in the group where that check blocked. A missing evaluation renders "not returned", never a pass |
| **Accessibility** | Real `<table>` at both levels with `scope`-d headers; cell `aria-label` reads "INC-…-01, task 4, source freshness, WARN"; arrow-key cell navigation; expansion is a `button` with `aria-expanded`; never colour-only |
| **Responsive** | 1920: group rows plus the selected incident's full matrix. 1440: matrix below the rows. 1280: per-task rows with a check summary chip |
| **Tests** | Unit: matrix builder with fewer than six checks returned, a missing evaluation, mixed `config_hash` across incidents, an incident with no plan; blocking tally; the group join drops nothing silently. Harness: six columns always present at incident level, all-pass-but-blocked legible, differing-config flag visible, contrast sweep |
| **Acceptance** | Six columns always render; no aggregate score at any level; config version and hash always visible and a mismatch across the group is flagged; a task with all six passing and a high tier still reads as blocked; every incident in the group is accounted for, including ones whose assurance call failed |
| **Demo value** | One amber column down eight incidents says "your METAR is stale across the whole cascade" faster than any sentence — and it is the gate answering at the same scale as the disruption |

---

## 9.1 Approval model under D3 (part of C2-5)

D3 replaces one control with two, and defines a set that no approval may ever cover.

| Level | Covers | Never covers |
| --- | --- | --- |
| **Plan-level approval** | `low` and `medium` risk evaluations awaiting a human, in one decision with one reason | Any `high` risk evaluation. Any evaluation with a `FAIL` on evidence |
| **Action-level approval** | Exactly one evaluation, including `high` risk | A `FAIL` on evidence — that is a missing fact, and a human cannot approve a fact into existence |

**The rule in one line for the UI:** approval can cover **risk**, never **failed evidence**.

| Aspect | Detail |
| --- | --- |
| **Files** | `src/features/assurance/PlanApprovalPanel.tsx`, `ApprovalCoverageList.tsx`; `AssurancePanel.tsx` keeps the single-action path |
| **API** | **FE-10.** The server must enforce the tier rule and the evidence rule; the UI must never be the only thing preventing a bulk high-risk approval |
| **Interaction** | The plan-level control lists exactly what it will cover, itemised by task and tier, and lists what it excludes with the reason (`high risk — needs its own approval`, `evidence_complete FAIL — cannot be approved`). Excluded items are visible, not hidden: a reviewer must see that the control was *unable* to cover them. One mandatory reason, written to every covered decision. No select-all across tiers, ever |
| **Accessibility** | The coverage list is a real `<ul>` read before the buttons; the excluded list has its own heading so a screen reader reaches it without exploring; reason field mandatory with `aria-invalid` and `role="alert"`, as in Phase 1 |
| **Tests** | Unit: coverage partition (low/medium in, high out, evidence-FAIL out) over every combination of tier × check state; a group where nothing is coverable disables the control with a stated reason. Harness: approving a plan leaves every high-risk evaluation still awaiting; an evidence-FAIL evaluation is never covered; server rejection surfaces as an error, not a silent success |
| **Acceptance** | No high-risk evaluation is ever covered by a plan approval; no evidence-FAIL evaluation is ever covered; the excluded set is visible with reasons; each covered evaluation gets its own immutable decision record with the shared reason; the count of approvals recorded equals the count claimed |
| **Demo value** | "Approve the routine seven, and the cash payout still needs me by name." The gate's argument, made operable rather than described |

---

## 10. Replay experience (C2-7)

| Aspect | Detail |
| --- | --- |
| **Route** | `/replay/:incidentId` (replaces the placeholder) |
| **Files** | `src/features/replay/ReplayScreen.tsx`, `Scrubber.tsx`, `EntryDetail.tsx`, `replayState.ts` |
| **API** | `GET /incidents/{id}/timeline` and `GET /incidents/{id}` only. No replay endpoint needed |
| **Data contracts** | Entries carry `id`, `occurred_at`, `stage`, `actor`, `actor_kind`, `event_type`, `summary`, `detail`, `correlation_id`. Real vocabulary observed live: stages `detect`/`assess`/`plan`/`assure`/`execute`/`resolve`/`run`; events `INCIDENT_OPENED`, `DELAY_RISK_SCORED`, `PLAN_PROPOSED`, `ASSURANCE_EVALUATED`, `STATE_CHANGED`, `ACTION_COMPLETED`, `HUMAN_DECISION_RECORDED`, `WORKFLOW_RUN_REQUESTED`. Actor kinds `orchestrator`, `service`, `human`, `provider`, `agent` |
| **State reconstruction** | `replayState.ts` is pure: `(entries, cursor) → {state, tasksKnown, decisionsKnown}` derived by folding `STATE_CHANGED` details up to the cursor. It reconstructs from records; it never re-runs logic or predicts |
| **Interaction** | Scrub by entry index (not wall-clock, which would stall on a 27-hour scenario gap); filter by `actor_kind` and `stage`; an "only decisions" toggle keeping `ASSURANCE_EVALUATED`, `HUMAN_DECISION_RECORDED` and `STATE_CHANGED`; expand for full `detail` plus `correlation_id`; `WORKFLOW_RUN_REQUESTED` bookkeeping entries hidden by default with a visible count and a toggle |
| **Accessibility** | Scrubber is `<input type="range">` with `aria-valuetext` reading the entry summary; a "position N of M" live region; arrow keys step, Home/End jump; the human entry keeps the icon-plus-solid-chip treatment from Phase 1 so an operator's act stays findable |
| **Responsive** | 1920: full-height list plus reconstructed state panel. 1280: state panel becomes a sticky header |
| **Tests** | Unit: fold is pure and monotonic; `STATE_CHANGED` details produce the same rail the API returns; filters never drop an entry silently; hidden-entry count is accurate. Harness: scrub to any position, filters, only-decisions, `correlation_id` visible, contrast sweep |
| **Acceptance** | Every rendered fact traces to an entry; reconstructed state at the final cursor equals `state_rail`; no interpolation between entries; the human decision is distinct |
| **Demo value** | "Show me what happened and prove it." A read over immutable records, not a model narrating history |

---

## 11. Sequencing — the mandated order

`C2-3 → C2-1 / C2-2 → C2-5 / C2-6 → C2-4 → C2-7 → C2-8 → C2-9`

| Order | Item | Hard dependency | Gate |
| --- | --- | --- | --- |
| 1 | **C2-3** groundwork | none | Layout determinism tests pass; primitives render in fixture and live mode |
| 2 | **C2-1** Command Center · **C2-2** Cascade Explorer | none (FE-2 upgrades C2-2 to real data) | Ten-second test at 1920 with zero hardcoded counts; nine pairing nodes countable with labelled edges and totals from `rollups` |
| 3 | **C2-5** group assurance · **C2-6** crew impact | **FE-8** for the group join (degraded join usable meanwhile); **FE-10** for plan approval | Six columns per incident, no score at any level, config mismatch flagged; nine pairings listed with mechanism and detail |
| 4 | **C2-4** what-if | variant 1 none · variant 2 **FE-9** | Cause flip changes amounts and rules; zero write requests issued from any what-if surface |
| 5 | **C2-7** replay | none | Reconstructed state at the final cursor equals `state_rail` |
| 6 | **C2-8** plan comparison | **FE-4**, or Phase 3 generator comparison | Two plans diffed, both generators named, no "better plan" claim |
| 7 | **C2-9** hardening | all of the above | Projector rehearsal clean: zero text below 4.5:1, no horizontal overflow, keyboard-complete, five states per surface |

C2-3, C2-1, C2-2, C2-7 and what-if variant 1 need no backend change at all, so the critical path
never stalls waiting on another stream.

**Cut before any of the above:** Open-Meteo / historical provider expansion (confirmed
non-critical). If time is lost, cut from the bottom of `docs/20`'s list — not from this order.

---

## 12. Test infrastructure — a decision for this review (C2-9 depends on it)

The frontend has **no test runner**. Every claim so far has been verified with headless
`agent-browser` harnesses in `/tmp`, which is fine for a rehearsal and wrong for regression
cover on ten new surfaces.

Proposal, for approval:

1. **Add Vitest + @testing-library/react** for pure logic: graph layout, replay folding, matrix
   building, diff computation, derivation adapters, sort and filter predicates. These are exactly
   the parts where a silent wrong answer is plausible.
2. **Promote the harnesses into `frontend/scripts/`** as committed, reviewable checks — the
   five-state sweep, the contrast sweep, overflow and keyboard reachability — so `make
   test-frontend` stops being a no-op.
3. **Extend `check-tokens.mjs`** to fail on a status colour used for non-status meaning, which is
   the exact defect found in the Phase 1 rehearsal (`--state-warn` on an actor chip).

Cost is roughly half a day. Without it, ten new surfaces are defended only by a human running a
script by hand.

---

## 13. Alignment gate — what must be true before C2-3 starts

Implementation is blocked until these are settled with the owning streams. Each is a yes/no, not a
discussion.

| # | Needs agreement from | Question |
| --- | --- | --- |
| 1 | Review | Are the `C2-1…C2-9` labels in §0.1 the intended binding? |
| 2 | Stream A | **FE-8** — group-scoped assurance endpoint, or `incidents[]` on the group payload? Which, and when |
| 3 | Stream A + B | **FE-10** — plan-level decision contract that server-side rejects `high` tier and evidence-`FAIL`. Confirmed as server-enforced, not UI-enforced |
| 4 | Stream A + B | **FE-9** — what-if endpoint honouring D2: zero write, deterministic, same six checks, bounded enumerated inputs |
| 5 | Stream A | **FE-1** — `payload` on `ActionSummary`. Without it C2-6 ships as crew-only, permanently |
| 6 | Stream A | **FE-6** — `incident_reference` on the group's `flights[]`, which removes the fragile join in C2-2 and C2-5 |
| 7 | Stream A | Who commits the D2 boundary to `docs/DECISIONS.md`? Draft supplied in §14; `docs/` is Stream A's path and Stream D will not write it |
| 8 | Review | Vitest — yes or no (§12). Nine work items defended by a hand-run script is the current state |
| 9 | Stream C | Confirm Open-Meteo expansion is descoped so no fixture or provenance row implies it is live |

**Ownership restated:** Stream D writes only inside `frontend/`. Every item above that touches
`backend/`, `docs/`, `fixtures/` or `config/` is a request to its owner, never a local edit.

---

## 14. Draft entry for `docs/DECISIONS.md` (D2 boundary) — for Stream A to commit

> ### What-if is a re-evaluation, not a simulation
>
> **Decision.** What-if is in scope for Phase 2 as a **bounded, zero-write, deterministic
> re-evaluation**. It is explicitly **not** a simulation engine and **not** a digital twin.
>
> **What that means concretely.** A what-if takes recorded facts, alters an enumerated and
> server-validated subset of inputs, and re-runs the existing deterministic machinery — the six
> assurance checks under the same `config_version` and `config_hash`, or the policy rules engine
> under a different cause. It writes nothing: no row created, updated or deleted, no state
> transition, no side effect, no audit record beyond the read itself. Given the same inputs it
> returns the same output, so nothing about it is time- or sample-dependent.
>
> **Why the boundary is drawn here.** Modelling what *would have happened* operationally —
> passenger re-accommodation outcomes, downstream delay propagation, cost curves — requires
> observed outcomes we do not have and cannot validate on synthetic data. Claiming it would be the
> same category of overreach as an uncalibrated confidence score, which `docs/18` already rejects.
> Re-evaluating recorded facts under a different assumption is defensible because every step is
> deterministic and every figure remains traceable to a rule.
>
> **Consequences.** The UI never uses the words "simulation", "prediction" or "forecast" for this
> feature; every what-if surface is watermarked and states that nothing was recorded; no what-if
> result offers an execution path; and the input space is an enumerated set rather than free-form,
> because an unbounded input space is a simulator by another name.
>
> **Rejected alternatives.** A digital twin (no validated model, no observed outcomes); free-form
> operational what-if (unbounded inputs, unfalsifiable results); persisting what-if runs as
> scenarios (pollutes the audit trail with things that never happened).

---

## 15. Remaining open questions

D1, D2 and D3 are settled and struck from this list. What is left:

1. **Do we want a map at all?** It needs FE-3, and `docs/21` argues against decoration. My
   recommendation stands: no. A dense strip conveys more per pixel and cannot imply precision we
   lack.
2. **Which plan-comparison option** (§8 A/B/C)? This decides whether C2-8 is Phase 2 or Phase 3.
   Recommendation: option B, free once `LLM_MODE` flips in Phase 3.
3. **Vitest: yes or no** (§12).
4. **Does the Command Center replace `/` or sit beside it?** Replacing means the Phase 1 Ops Board
   becomes a zone within C2-1; keeping both means two front doors.
5. **`agent` actor colour.** It still borrows `--state-info` for identity, the same class of defect
   fixed for `human` in Phase 1. Decide now or when the Planner lands.
