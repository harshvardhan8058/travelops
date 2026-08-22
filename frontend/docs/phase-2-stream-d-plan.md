# Phase 2 — Stream D plan

**Status: for architecture review. No implementation until review closes.**

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
| Network Command Center | **Buildable now** | `/flights`, `/incident-groups`, `/system/mode`, `/health/ready`, `/sources` cover it. No map — see D3 |
| Cascade Explorer | **Buildable now** | `crew_pairings[].source_flight` + `mechanism` are real edges. Connections and hotels are counts only |
| Impact views | **Crew: buildable. Passenger/connection/hotel: blocked** | Only crew has per-entity records. See D1 |
| What-if simulation | **Policy-cause only** | `policy.cause_comparison` is real. Operational what-if has no endpoint. See D5 |
| Recovery-plan comparison | **Blocked** | `IncidentDetailResponse.plan` is a single object. No alternatives, no versions. See D4 |
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

## 2. Dependencies to route in this review

Ordered by how much they unblock. Each is a request, not a workaround.

| # | Ask | Owner | Unblocks | Note |
| --- | --- | --- | --- | --- |
| **D1** | `payload: dict \| None` on `ActionSummary`, or `GET /incidents/{id}/actions/{action_id}` | A | Passenger, connection and hotel impact views | The data is already persisted; this is exposure, not new computation |
| **D2** | Wire `/incident-groups/*` to `scenario_queries.cascade_rollup()` | A + C | Cascade Explorer on real data | `api/__init__.py` already flags it "needs Stream C's cascade data" |
| **D3** | `latitude`, `longitude` on `/flights.network[]` | C | A geographic Command Center | OurAirports is already loaded. **Only if we want a map at all** — recommend not |
| **D4** | A plan-alternatives contract | A | Recovery-plan comparison | Options in §7. Cheapest is `plan.supersedes` / `plan.version` |
| **D5** | A what-if contract | A + B | Operational what-if | Must be non-executing, gated and labelled. Options in §6 |
| **D6** | `incident_reference` on `/incident-groups/{id}.flights[]` | A | Cascade node → workspace navigation | One field; removes a fragile cross-endpoint join |
| **D7** | `reason_code` on `ActionSummary` | A | Refusal copy without prefix matching | Carried over from the Phase 1 review |

**Recommendation:** D1, D2 and D6 are small and unblock four of the seven features. D3 is a
"decline unless someone insists". D4 and D5 need a design decision before an endpoint.

---

## 3. Shared groundwork

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

## 4. Network Command Center

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

**Explicitly not built:** a geographic map (no coordinates — D3), and any airport not in the
configured set.

---

## 5. Interactive Disruption / Cascade Explorer

The screen that makes "8 flights → 9 rotations" countable instead of asserted. This is Phase 2's
centrepiece.

| Aspect | Detail |
| --- | --- |
| **Route** | `/cascade/:groupId` (replaces the placeholder) |
| **Files** | `src/features/cascade/CascadeExplorer.tsx`, `CascadeGraph.tsx`, `layout.ts`, `MechanismLegend.tsx`, `PairingTable.tsx`, `NodeInspector.tsx` |
| **API** | `GET /incident-groups/{id}` — real once D2 lands, fixture-shaped until then. `GET /flights` for the `id` → `incident_reference` join (D6 removes this) |
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

## 6. Impact views — passenger, crew, connection, hotel

| Aspect | Detail |
| --- | --- |
| **Route** | `/impact/:groupId` with four tabs, deep-linked (`?view=crew`) |
| **Files** | `src/features/impact/ImpactViews.tsx`, `CrewImpact.tsx`, `RollupOnly.tsx`, `ActionEvidence.tsx` |
| **API** | `GET /incident-groups/{id}` (crew per-entity + all rollups), `GET /incidents/{id}` (`affected_entities`, `actions[]`), `GET /reports/{id}` (`passengers_affected`, `connections_identified_at_risk`, `passengers_reaccommodated`, `notifications_real`, `notifications_simulated`, `total_cost_inr`). **Blocked on D1 for anything per-entity beyond crew** |
| **Data contracts** | Crew: the nine `crew_pairings[]` records — the only per-entity impact data that exists today. Passengers: `affected_entities.passengers` and `bookings` (real API returns at most these two, and `{}` when there are no bookings) plus report metrics. Connections: `rollups.connections_at_risk` + `report.connections_identified_at_risk`. Hotels: `rollups.candidate_hotels`. Action evidence: `actions[].reason` rendered **as a quoted string attributed to its actor**, never parsed for numbers |
| **Phasing** | **Ship crew impact first** — it is complete and it is the Phase 2 story. Passenger, connection and hotel tabs ship as rollup-plus-evidence views that state plainly which per-entity data the endpoint does not return, and become real lists the day D1 lands. The tab shells are built so that change is additive |
| **State** | Query cache keyed by group and incident. Client: active tab, sort, selected pairing — URL params |
| **Interaction** | Sort and filter the pairing table by mechanism, base, at-risk; select a pairing → detail with `mechanism_legend` explanation; each rollup tile has a `WhyPopover` naming the endpoint and field it came from; a missing count renders `—` with "not computed by this endpoint", never `0` |
| **Accessibility** | Tabs are a real `role="tablist"` with arrow-key movement and `aria-controls`; tables have scope-d headers and `aria-sort`; the "not returned by this endpoint" copy is text, not a tooltip-only affordance |
| **Responsive** | 1920/1440: table plus detail side by side. 1280: detail becomes a disclosure under the selected row |
| **Tests** | Unit: rollup renderer distinguishes `0` from absent; mechanism filter predicates; action-reason renderer escapes and never extracts digits. Harness: crew tab lists nine pairings, absent-data copy present on the other three tabs, contrast sweep |
| **Acceptance** | Zero fabricated entity rows; every count traceable to a named field; a `0` appears only when the API returned `0`; each tab states its own data limits |
| **Demo value** | "Nine rotations, and here is each one and why" — the concrete half of the cascade claim. The honesty of the other three tabs is itself a differentiator against a demo that invents 22 connection rows |

---

## 7. What-if simulation UI

**Scope discipline:** exactly one what-if exists in the contracts — the policy engine's cause
comparison. Building an operational simulator without an endpoint would mean the UI computing
outcomes, which `docs/21` and the steering rules forbid outright ("Never let the UI compute an
entitlement").

| Aspect | Detail |
| --- | --- |
| **Route** | `/policy/:incidentId` (screen 5) hosts cause comparison; a separate `/what-if/:incidentId` only if D5 lands |
| **Files** | `src/features/policy-citation/PolicyScreen.tsx`, `CauseComparison.tsx`, `EntitlementBreakdown.tsx`, `CitationCard.tsx`, `PackStatusBanner.tsx` |
| **API** | `GET /incidents/{id}/policy`: `pack{id, version, status, ui_label, authority, document, pack_hash, source_hash}`, `applicability[]{status, missing_facts, required_facts, resolver_version}`, `entitlements[]{type, outcome, amount_inr, cash, options, reason_codes, explanation, rules_fired, source_clause_refs, input_facts}`, `cause_assessment`, `cause_comparison{enabled, description, alternative{event_type, operational_cause, cash_inr, formula_used, rules_fired, source_clause_refs, note}}`, `excluded_rules[]`, `disclaimer` |
| **Interaction** | A two-state toggle: recorded cause versus `cause_comparison.alternative`. Switching re-renders amounts, `rules_fired` and clause refs side by side with changed rows marked. Both sides render `formula_used` verbatim, e.g. `least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000`. `outcome: "not_owed"` renders as **Not owed**, a result, never an empty cell. `missing_facts` renders as `needs_human` with the exact fact named. `excluded_rules` show a supersession notice and are visibly not evaluated |
| **Non-negotiable** | `pack.ui_label` verbatim — currently `MoCA PASSENGER CHARTER · FEB 2019 · PENDING CAR VERIFICATION`. Never upgraded, never manually overridden, never described as current law |
| **State** | Query cache; the toggle is a URL param so the compared state is shareable |
| **Accessibility** | Toggle is a `radiogroup`, not a checkbox; the diff is announced via `aria-live="polite"`; changed rows are marked with an icon and a word, never colour alone; clause text is selectable |
| **Responsive** | 1920/1440: side-by-side comparison. 1280: stacked with a sticky header naming each side |
| **Tests** | Unit: entitlement renderer for `not_owed`, missing-fact path, `excluded_rules` never evaluated, formula passthrough is byte-identical. Harness: banner text matches `ui_label` exactly, both cause columns render, amounts differ between them, contrast sweep |
| **Acceptance** | No amount computed client-side; every figure has a rule ID and a clause ref; "not owed" is displayed as a result; the pack badge is byte-identical to `ui_label` |
| **Demo value** | The strongest screen in the product. Flipping cause from weather to crew rostering changes ₹0 to ₹5,000 with different rules firing — a rules engine proving itself in one click |

**If D5 is approved**, the operational what-if must: call a dedicated endpoint, never execute,
return the same six assurance checks against the hypothetical, and be watermarked as a
simulation in every panel. A "what-if" that can be mistaken for an executed action is worse
than no feature.

---

## 8. Recovery-plan comparison

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

## 9. Plan-level assurance visualisation

Phase 1 shows the gate one task at a time. Phase 2 shows the whole plan at once — with no new
endpoint needed.

| Aspect | Detail |
| --- | --- |
| **Route** | A zone on `/incidents/:id`, plus reuse in the approval queue |
| **Files** | `src/features/assurance/PlanAssuranceMatrix.tsx`, `CheckSummaryRow.tsx`; reuses `AssurancePanel.tsx` |
| **API** | `GET /incidents/{id}/assurance` only: `evaluations[]{id, plan_task_id, action_type, decision, risk_tier, checks[], blocking[], config_version, config_hash, warn_permitted_by_config, human_decision}`, `awaiting_approval_count`, `incident_reference` |
| **Data contracts** | A tasks × six-checks matrix. Rows are the plan's tasks in `task_order`; columns are `CHECK_ORDER`. Each cell is `PASS`/`WARN`/`FAIL` as icon **and** word **and** colour. Aggregates computed from records only: counts by decision, which check blocks most often, tier distribution, approvals recorded. `config_version` and `config_hash` shown once per matrix, and per cell when an evaluation disagrees with the rest — a replay must prove which semantics applied |
| **Never** | An overall "assurance score", a percentage, or an average. The gate is fail-closed and ordered; a mean would be a fiction. `docs/18` is explicit |
| **Interaction** | Click a cell → the existing `AssurancePanel` for that task; click a column header → all tasks where that check blocked; a missing evaluation renders as "not returned" rather than a pass |
| **Accessibility** | A real `<table>` with row and column headers, `scope` attributes, and a cell `aria-label` reading "task 4, source freshness, WARN". Not colour-only. Arrow-key cell navigation |
| **Responsive** | 1920/1440: full matrix. 1280: collapses to per-task rows with a check summary chip |
| **Tests** | Unit: matrix builder with fewer than six checks returned, with a missing evaluation, with mixed `config_hash`; blocking-check tally. Harness: six columns always present, all-pass-but-blocked still legible, contrast sweep |
| **Acceptance** | Six columns always render; no aggregate score anywhere; config version and hash always visible; a task with all checks passing and a high tier still reads as blocked |
| **Demo value** | The gate at a glance: one amber column across five tasks says "your METAR is stale" faster than any sentence |

---

## 10. Replay experience

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

## 11. Sequencing

Ordered so that nothing waits on a blocked dependency, and each step ends demonstrable.

| Step | Work | Depends on | Gate |
| --- | --- | --- | --- |
| 1 | Shared groundwork (§3) | none | Layout unit tests pass; primitives render in both modes |
| 2 | Cascade Explorer on fixture-shaped data | none (D2 improves it) | Nine pairing nodes countable, labelled edges, totals from `rollups` |
| 3 | Crew impact view | none | Nine pairings listed with mechanism and detail |
| 4 | Plan-level assurance matrix | none | Six columns, no score, config hash visible |
| 5 | Replay | none | Reconstructed state equals `state_rail` |
| 6 | Network Command Center | none | Ten-second test at 1920 with zero hardcoded counts |
| 7 | Policy screen and cause comparison | none | Amounts change with cause; `ui_label` verbatim |
| 8 | Passenger/connection/hotel per-entity views | **D1** | Real rows replace rollup-only tabs |
| 9 | Plan comparison | **D4** or Phase 3 | Two plans diffed with both generators named |
| 10 | Operational what-if | **D5** | Watermarked, gated, non-executing |

Steps 2–7 need no backend change, which is the point: Stream D is never blocked.

---

## 12. Test infrastructure — a decision for this review

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

## 13. Open questions for the review

1. **Do we want a map at all?** It needs D3, and `docs/21` argues against decoration. My
   recommendation is no: a dense strip conveys more per pixel and cannot imply precision we lack.
2. **Which plan-comparison option** (§8 A/B/C)? This decides whether the feature is Phase 2 or
   Phase 3.
3. **Is operational what-if in Phase 2 scope at all?** It needs a contract, a gate story and a
   watermark. Cause comparison may be enough to make the point.
4. **Vitest: yes or no** (§12).
5. **Does the Command Center replace `/` or sit beside it?** Replacing means the Phase 1 Ops Board
   becomes a zone; keeping both means two front doors.
6. **`agent` actor colour.** It still borrows `--state-info` for identity, the same class of
   defect fixed for `human`. Decide now or when the Planner lands.
