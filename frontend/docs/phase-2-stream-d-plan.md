# Phase 2 — Stream D plan

**Status: architecture decisions final. Implementation blocked until backend contracts are
aligned — see §13.**

**Phase 2 UI is a flagship deliverable, not a graph bolted onto Phase 1.** The target is an
airline operations console a controller could be sat in front of: dense, calm, monochrome, where
every number can be interrogated and nothing on screen is decorative. The nine surfaces in §0.1
are one product with one information hierarchy (§0.3), not nine pages that happen to share a
rail.

The quality bar in §0.2 is written as **verification mechanisms rather than aspirations**, because
"dense but readable" and "accessibility complete" are unfalsifiable as prose. Each line of the bar
maps to something that fails a build, fails a test, or fails a harness assertion.

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

**Two flagship surfaces named separately in the brief map onto this frozen order rather than
extending it**, so the sequencing is not reopened:

| Brief item | Ships as | Section |
| --- | --- | --- |
| Blast-radius explanation | **C2-2b**, with the Cascade Explorer | §5.1 |
| Human approval semantics | **C2-5b**, with group assurance | §9.2 |

---

## 0.2 Quality bar, as verification mechanisms

| Bar | How it is enforced | Fails at |
| --- | --- | --- |
| **Projector-first 1920×1080** | Harness asserts, per surface: zero text below 4.5:1, no horizontal page overflow, smallest rendered font ≥ 11px, and every primary action inside the fold. Screenshot review at 1:1 | Harness (`scripts/verify-surface.mjs`) |
| **Dense but readable hierarchy** | Every surface declares its zone budget in §0.3 and the harness asserts the row height, panel padding and heading scale actually rendered match the declared tier | Harness |
| **Zero business calculations in frontend** | Metrics may only be rendered through `<Metric>`, which takes a value **and** a required `derivation`. No arithmetic on API values anywhere outside `formatDuration`-style presentation helpers, which are unit-tested and cannot touch money, entitlements or counts. A scripted check flags arithmetic operators applied to API-typed identifiers in `src/features/**` | Unit test + `scripts/check-no-client-math.mjs` |
| **Every metric traceable to backend data** | `<Metric>`'s `derivation` prop is **type-required**. A metric without a derivation does not compile. Each adapter names the endpoint and field it read | TypeScript |
| **Keyboard / accessibility complete** | Per-surface a11y matrix in §12: roving tabindex, `aria-sort`, `role` correctness, focus return, live regions. Harness walks every focusable element asserting an accessible name and a visible ≥2px focus ring | Harness + unit |
| **No generic "AI dashboard" visuals** | `tokens:check` already bans the hues and effects; extended to fail on a status colour used for identity (the Phase 1 `--state-warn`-on-actor defect) and on chart libraries in `package.json` | `npm run tokens:check` |
| **No invented coordinates, scores, edges or aggregates** | §0.4 whitelist. Graph edges may only be built by the join in §5; aggregates may only be counts of returned arrays, and only where the API does not already return the count | Unit test per builder + review |
| **Restrained, functional animation** | Motion whitelist in §0.5. `tokens:check` fails on `animate-pulse`, `animate-bounce`, `transition-all`, and any duration above 220ms | `npm run tokens:check` |
| **Every major number has a derivation/provenance path** | Same as traceability: type-required. Plus the harness asserts each surface's metric tiles expose a popover trigger with an accessible name | TypeScript + harness |

## 0.3 Information hierarchy

One hierarchy across all nine surfaces, so a controller learns it once. Three tiers, and a
surface states which tier each zone belongs to.

| Tier | Question it answers | Treatment | Budget at 1920×1080 |
| --- | --- | --- | --- |
| **T1 situational** | What is broken, how bad, what needs me? | `title`/`subtitle` scale, `StateBadge`, `RiskChip`, 34px rows, always above the fold, never behind a click | ≤ 30% of vertical space |
| **T2 diagnostic** | Why does it say that? | `body`/`mono-sm`, tables and matrices, one interaction to reach detail | ≈ 50% |
| **T3 forensic** | Prove it | `caption`, evidence refs, config hashes, correlation ids, raw `detail` — reachable, never competing for attention | ≤ 20%, collapsible |

Rules that follow from it: T3 never sits above T1 on a surface (the Phase 1 rehearsal defect where
six evidence refs pushed Approve below the fold was exactly this); a T1 zone never scrolls; and any
surface where T2 exceeds its budget gets a filter, not a smaller font.

## 0.4 Aggregate and relationship whitelist

Exhaustive. Anything not on this list is invented and may not ship.

| Allowed | Source |
| --- | --- |
| Counts already returned | `rollups.*`, `awaiting_approval_count`, report `metrics.*` |
| `array.length` of a returned array | `flights[]`, `crew_pairings[]`, `evaluations[]`, `tasks[]`, `actions[]`, `entries[]` |
| Partition counts over a returned enum field | evaluations by `decision`, checks by `state`, pairings by `mechanism`, entries by `actor_kind` |
| Set membership and equality joins on a shared key | `crew_pairings[].source_flight` = `flights[].flight_number`; group `flights[].id` = `/flights[].id` |
| Interval between two returned timestamps | `formatDuration(first_record, last_record)` — never against the wall clock (Phase 1 defect) |

| Forbidden | Why |
| --- | --- |
| Any mean, median, rate, percentage or score | Not returned, and a fail-closed gate has no meaningful average (`docs/18`) |
| Any money or entitlement arithmetic | The rules engine owns it; the UI renders `formula_used` verbatim |
| Any edge not in the join above | Connections and hotels are counts with no arrays behind them |
| Any coordinate | No schema carries latitude or longitude |
| Any trend, delta-over-time or sparkline | No endpoint returns a series. A two-point line is not a trend |

## 0.5 Motion whitelist

| Motion | Trigger | Duration | Why it earns its place |
| --- | --- | --- | --- |
| Opacity + 4px rise | A popover or panel opening | 180ms `ease-out` | Establishes the relationship between trigger and panel |
| Opacity | A new timeline entry arriving | 220ms | Marks arrival without dragging the eye |
| Colour/border | Hover and focus | 100ms | Feedback |
| Edge and node emphasis | Selecting a cascade node | 150ms, opacity and stroke width only | Shows the traversal, which is the explanation |

Nothing loops, pulses, floats, springs or auto-plays. No layout animation: a node-link graph that
re-flows while being read is unreadable. `prefers-reduced-motion` collapses all of the above to
instant, which `tokens.css` already enforces globally.

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
| **FE-10** | Plan-level decision contract per D3: a decision covering multiple low/medium evaluations, rejecting high-risk ones server-side | A + B | **C2-5 / §9.1 / §9.2** | The server must enforce the tier rule; the UI must not be the only thing preventing a high-risk bulk approval |
| **FE-11** | A yes/no on time series ever existing | A | Charting policy (§3) | If no, recorded once and no surface attempts a trend. Current assumption: no |
| **FE-12** | A yes/no on connections and hotels becoming per-entity records | A + C | Blast radius terminals (§5.1), C2-6 | If no, they stay counts and the UI says so permanently |

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
| `src/components/ui/Metric.tsx` | **`<Metric>` — the traceability guarantee.** Takes `value`, `label` and a **required** `derivation`. Renders through `MonoValue`, wraps in `WhyPopover`, and shows `ProvenanceDot` when the derivation carries provenance. A metric without a derivation does not compile, which is what turns "every number is traceable" from a review promise into a type error |
| `src/components/ui/primitives.tsx` | Add `MetricTile` (a `<Metric>` in a bordered tile), `CountBar` (single-hue accent ramp over **counts only**, never a trend), `FilterChips` |
| `src/hooks/useKeyboardList.ts` | Roving-tabindex list navigation (`j`/`k`/`Enter`), reused by every list, table and the graph |
| `src/hooks/useSurfaceShortcuts.ts` | Registers a surface's shortcuts in one place so the global model (§12.1) cannot be shadowed silently |
| `scripts/check-no-client-math.mjs` | Fails the build on arithmetic applied to API-typed identifiers under `src/features/**`. Presentation helpers live in one audited module and may not touch money, entitlements or counts |
| `scripts/verify-surface.mjs` | The Phase 1 `/tmp` harness, promoted and parameterised per surface: five states, contrast sweep, overflow, fold check, focus-ring and accessible-name walk |

**Design rules inherited, not re-litigated:** graphite base, instrument cyan for brand/active
only, green/amber/red exclusively for operational state, 1px borders, `rounded-md`, 14px body,
34px rows, 100–220ms ease-out, Lucide 16px, every number through `MonoValue`, every status
through `StateBadge`, every derived figure through `WhyPopover`, every data surface with
`ProvenanceDot`.

**Contrast is now a measured gate, not a claim.** The Phase 1 rehearsal found `--fg-muted` at
3.74:1 while the token comment claimed 4.6:1. Every new surface must report zero text below 4.5:1
from `verify-surface.mjs` before its acceptance criteria pass.

**Charts: there is almost nothing legitimate to chart.** No endpoint returns a time series, so a
line or an area chart would be interpolation between two points — invention. What the data supports
is counts, and counts are best read as numbers and bars: `CountBar` renders a single-hue accent ramp
over a partition that already exists in the payload (evaluations by decision, pairings by mechanism,
sources by kind). No gauges, no donuts, no radials, no rainbow palettes, no chart library — the
absence of one in `package.json` is itself a check.

**Reuse over novelty.** Phase 1's `AssurancePanel`, `StateRail`, `WhyPopover`, `StateBadge`,
`MonoValue`, `ProvenanceDot` and `AgeIndicator` are the vocabulary of the console. A Phase 2 surface
that needs a new way to show a status or a number is a design smell to resolve in review, not in a
feature folder.

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

## 5.1 Blast-radius explanation (C2-2b)

The graph shows *that* nine rotations are affected. Blast radius answers *how far the damage
reaches and by what mechanism at each hop* — in words, from records, without a model. It is the
single most defensible thing this product can put on a projector, because every hop is a join a
reviewer can check.

**What the data actually supports.** Two traversable hops and two terminal counts:

| Hop | From → to | Edge evidence | Traversable? |
| --- | --- | --- | --- |
| 1 | trigger → flights | group `root_cause` + `airport_icao`, `flights[]` | Yes — 8 flights |
| 2 | flight → crew pairing | `crew_pairings[].source_flight` = `flights[].flight_number`, labelled `mechanism` | Yes — 9 pairings |
| — | flights → connections | `rollups.connections_at_risk: 22` | **No. A count with no array. Terminal tile** |
| — | flights → hotel demand | `rollups.candidate_hotels: 11` | **No. Terminal tile** |

So the radius is stated as: *one weather trigger at VOBL → 8 flights → 9 crew pairings, by four
named mechanisms; 22 connections and 11 candidate hotels are counted but not individually
returned.* That last clause is not a weakness to hide — it is the difference between this and a
demo that draws 22 fictional connection nodes.

| Aspect | Detail |
| --- | --- |
| **Route** | A panel on `/cascade/:groupId`, with a deep link per hop (`?hop=2&mechanism=positioning`) |
| **Files** | `src/features/cascade/BlastRadius.tsx`, `RadiusHop.tsx`, `MechanismBreakdown.tsx`, `radius.ts` (pure) |
| **API** | `GET /incident-groups/{id}` only. FE-6 (`incident_reference` on the group's flights) makes each hop's endpoint navigable |
| **Data contracts** | `radius.ts` exports `buildRadius(group) → {hops: Hop[], terminals: Terminal[]}`. A `Hop` carries `from`, `to`, `count` (= `array.length`), `mechanismCounts` (partition of `crew_pairings[].mechanism`) and `records[]`. A `Terminal` carries a label, a count from `rollups`, and the reason it is not traversable. Nothing else. `why_nine_not_eight` is rendered verbatim as the summary |
| **Interaction** | Selecting a hop highlights exactly those nodes and edges in the graph (opacity and stroke only, 150ms); selecting a mechanism filters to pairings with that `mechanism` and shows `mechanism_legend[mechanism]` as the explanation; every count is a `<Metric>` whose popover names `rollups` or the array it counted; expanding a pairing shows its `detail` sentence verbatim |
| **Never** | A "blast radius score", a severity index, a monetary impact total, or a hop the join does not support. Terminal counts never render as nodes, and never as a percentage of anything |
| **Accessibility** | The radius is a `<ol>` of hops — an ordered, screen-reader-complete narrative that stands alone with the graph switched off. Each hop is a `button` with `aria-expanded`; the mechanism partition is a `<dl>`; graph highlight is mirrored by `aria-current` on the hop |
| **Responsive** | 1920: radius panel beside the graph. 1440: below it. 1280: radius first, graph collapsed behind a disclosure — the words survive; the picture is the enhancement |
| **Tests** | Unit: `buildRadius` over the real payload yields 8 and 9 with four mechanisms; a pairing whose `source_flight` matches no flight becomes a named unmatched record, never a silent drop; terminals always carry a reason; no hop is produced for connections or hotels. Harness: hop selection highlights the same node set the hop lists, contrast sweep, keyboard traversal of all hops |
| **Acceptance** | A reviewer reads the whole radius without touching the graph; every count matches `rollups` or an array length; the four mechanisms appear as words with their legend text; the two terminal counts are visibly labelled as counts, not paths |
| **Demo value** | This is the Phase 2 claim in one paragraph a judge can verify line by line: *"disruption is never one flight"*, with the mechanism named at every hop and the limits of our data stated out loud |

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

## 9.2 Human approval semantics (C2-5b)

D3 gives the rules. This section gives the *semantics* the UI must convey, because an approval is
the one place a person's accountability enters an otherwise deterministic system, and the interface
is what makes that accountability legible or deniable.

**Five semantics, each with a visible consequence.**

| Semantic | What the UI must make unmistakable |
| --- | --- |
| **An approval authorises; it does not execute** | Approving never runs anything. Execution is a separate, explicit `Run`. After approving, the UI states that the action is now authorised and awaiting execution — Phase 1 verified the backend behaves this way (`human_decision_id: 1` appears on the action only after the next run) |
| **A decision is immutable and append-only** | No edit, no delete, no undo. A conflicting decision is refused by the server with 409 and the UI shows the recorded decision plus *"a corrected decision requires a new evaluation"* — the server's own words |
| **The record is the operator's, not the system's** | Actor, timestamp and the operator's verbatim reason, rendered with the `human` actor treatment fixed in Phase 1 (solid chip, person icon, off the status ramp). The API's persisted `human_decision` always wins over any session copy |
| **Approval covers risk, never absent evidence** | An evaluation with an evidence `FAIL` is not offered an approval control at all, and says why: a human cannot approve a fact into existence |
| **Coverage is explicit, and so is exclusion** | A plan-level approval lists every evaluation it will cover with its tier, and every one it excludes with the reason. Silence about exclusions is how a bulk approval becomes a trap |

| Aspect | Detail |
| --- | --- |
| **Files** | `src/features/assurance/PlanApprovalPanel.tsx`, `ApprovalCoverageList.tsx`, `DecisionRecord.tsx`; `AssurancePanel.tsx` keeps the single-action path |
| **API** | `POST /assurance/{id}/decision` (`decision`, `reason` 1–2000, `actor_id`, → `replayed`), plus **FE-10** for the plan-level decision. `evaluations[].human_decision` is the source of truth on read |
| **State** | Server state only after a write succeeds. The optimistic path is deliberately absent: an approval that appears to succeed and did not is the worst failure mode this screen has. Pending state is explicit; failure surfaces the API code and correlation id |
| **Interaction** | Mandatory reason with inline validation (`aria-invalid`, `role="alert"`); coverage and exclusion lists before the buttons; no select-all across tiers; after success the panel becomes the immutable record and the reason field is gone, not merely disabled |
| **Accessibility** | Coverage and exclusion are `<ul>`s read before the controls; the record uses a `<dl>`; the 409 conflict is announced `aria-live="assertive"` because it changes what the operator believes happened |
| **Tests** | Unit: coverage partition over every tier × check-state combination; a nothing-coverable group disables the control with a stated reason; conflict response renders the recorded decision. Harness: high-risk still awaiting after a plan approval; evidence-FAIL never covered; reload shows the API's record, not the session's; approval count claimed equals decisions recorded |
| **Acceptance** | Every covered evaluation gets its own immutable record carrying the shared reason; no high-risk or evidence-FAIL evaluation is ever covered; exclusions visible with reasons; the words "authorised" and "executed" are never used interchangeably anywhere in the UI |
| **Demo value** | *"Approve the routine seven; the cash payout still needs me by name, and the missing fact cannot be approved at all."* The gate's argument made operable — and the moment a judge sees the product take accountability seriously |

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

## 11.1 Global keyboard model

Defined once, in `docs/27`, and completed in Phase 2. A surface may add shortcuts; it may not
redefine these. `useSurfaceShortcuts` registers them centrally so a shadowed binding is a test
failure rather than a surprise on stage.

| Key | Action | Scope |
| --- | --- | --- |
| `j` / `k` | Next / previous row or node | Every list, table, matrix and the graph |
| `Enter` | Open the selected item | Global |
| `Esc` | Close the topmost overlay, return focus to its trigger | Global (Phase 1 behaviour) |
| `/` | Focus the surface's filter | Global |
| `g` then `o` `c` `a` `p` | Ops Board / Cascade / Approvals / Policy | Global |
| `a` / `r` | Approve / reject the focused blocked action, opening the reason field | Approval surfaces only |
| `←` / `→` | Step the replay cursor; step a hop in blast radius | Replay, Cascade |
| `1`–`6` | Jump to a check column | Assurance matrix |

Focus rules, non-negotiable: focus never leaves the document; every overlay returns focus to its
trigger; a 2px accent ring at 2px offset is always visible; no `outline: none` anywhere; roving
tabindex means one tab stop per collection, not one per row.

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
| 10 | Stream A | **FE-11** — is any time series ever coming? If not, the answer is recorded once and no surface attempts a trend, which is the current design assumption |
| 11 | Stream A + C | **FE-12** — do connections and hotels ever become per-entity records? If not, blast radius keeps them as terminal counts permanently, and that is stated in the UI rather than looking like an omission |
| 12 | Stream B | Confirm `config_version` / `config_hash` are stable within a group in normal operation, so the mismatch flag in §9 is an exception path rather than a permanent banner |

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


---

## 16. Anti-goals

Written down so that "flagship" is not misread as "more of everything". Each of these would make
the product worse, and each is tempting.

| Anti-goal | Why it is refused |
| --- | --- |
| A geographic map | No coordinates exist in any contract. A map drawn from guessed positions is a lie with a legend |
| A KPI wall of big numbers | Numbers without derivations are decoration. Every tile here costs a `<Metric>` with a provenance path, which is the constraint that keeps the count honest |
| Trend lines and sparklines | No endpoint returns a series. Two points joined by a line is invention with a slope |
| An "overall health" or "assurance" score | A fail-closed ordered gate has no meaningful average, and a single number invites exactly the trust the gate exists to replace |
| Drag-and-drop plan editing | The orchestrator owns the plan; a UI that edits it makes the audit trail fiction |
| A chat panel | `docs/27` rules it out explicitly. This is an operating layer, not an assistant |
| Free-form what-if inputs | An unbounded input space is a simulation engine, which D2 rules out |
| Auto-refreshing animation, live-updating charts, "pulse" effects | Motion that does not carry meaning costs attention during a disruption |
| Dark-mode toggle, theme customisation, dashboard layout editing | Zero demo value, real maintenance cost, and `docs/27` lists them as deliberately not built |
| Optimistic writes on approvals | An approval that appears to succeed and did not is the worst failure this product can have |

## 17. What "best-in-class" means here, concretely

Four claims a reviewer should be able to test in under a minute each, on any surface:

1. **Point at any number and ask "where is that from?"** — it has a popover naming the endpoint,
   the field, the rule or formula, and when it was recorded. Type-enforced, not promised.
2. **Ask "what does this system not know?"** — absences are on screen as absences: `—` with "not
   computed by this endpoint", terminal counts labelled as counts, `not_owed` as a result, a
   missing evaluation as a gap rather than a pass.
3. **Unplug the mouse.** Every surface completes its task from the keyboard with a visible focus
   ring, including approving an action and scrubbing a replay.
4. **Stand three metres back at 1920×1080.** Nothing below 4.5:1, no horizontal scrolling, every
   primary action inside the fold, operational numbers in tabular mono so columns scan.

The console is the argument. If a judge believes the interface, they believe the audit trail behind
it — which is the only reason any of this styling matters.


---

## 18. Answers to Stream A's open confirmations (D-3, D-5, D-7)

`docs/34-phase2-contract-alignment.md` §4 leaves three rows addressed to Stream D. Answering them
here rather than in a PR comment, so the answer is versioned next to the plan it constrains.

### D-7 — one group assurance endpoint, not client fan-out. **Confirmed.**

`GET /api/v1/incident-groups/{ref}/assurance` returning `GroupAssuranceSummary`. A read the plan
correctly. Client-side fan-out over eight incidents is wrong for a reason beyond request count: a
partial failure would render as a smaller set of blocked actions, which reads as *better* news than
the truth. A group approval screen that under-reports blockers because one request failed is worse
than one that refuses to render. One endpoint fails once, visibly.

### D-3 — A5's plan-comparison contract satisfies D's what-if surface. **Confirmed, with the boundary named.**

P2-D2 grants a **plan-comparison** what-if, not an operational one. Concretely, what the UI will and
will not offer:

| Offered | Not offered |
| --- | --- |
| Re-evaluate the **same recorded evidence** under a different candidate plan, and show the six checks for each | Edit a delay, a passenger count or a weather value and ask what would happen |
| Show the **recorded cause** versus an **alternative cause the engine already computes**, as the policy screen does today | Project a world state, an outcome, or a cost that no stored fact supports |
| State on screen that nothing was written | Any wording that implies a simulation engine or a twin |

C2-8 is **not** deferred to Phase 3. The plan's earlier note contemplating that predates the
one-increment delivery model and is superseded by this section.

The structural guarantee matters more than the wording: A5's `basis: Literal["recorded_evidence"]`
means a response that is not a re-evaluation of stored facts cannot be represented in the contract
at all. The UI should render `basis` rather than assert the boundary in prose — a label the server
sends is auditable, and a sentence in a component is not.

**Precondition D depends on:** the comparison endpoint must be zero-write in the HTTP sense as well
as the domain sense — no `Idempotency-Key`, and safe to call on every keystroke of a filter. The
current verification asserts zero `POST`/`PUT`/`PATCH`/`DELETE` during read-only browsing; a
what-if that POSTs would break that assertion by design, so it needs to be a `GET`, or a `POST`
explicitly exempted and documented as such.

### D-5 — exact shape for `warn_permitted_by_config`. **Per check, not per evaluation.**

A asks for the shape and notes it is on no model today. It belongs on `CheckResult`, not on the
evaluation, because permission is per check by configuration: a `WARN` on `freshness` may be
tolerated while a `WARN` on `policy` is not, and a single evaluation-level boolean cannot express
that. The current `CheckResult` is `{name, state, reason_code, reason, tier, evidence_refs}`.

Requested addition:

```jsonc
{
  "name": "freshness",
  "state": "warn",
  "reason_code": "observation_age_within_tolerance",
  "reason": "Observation is 41 minutes old; tolerance is 60.",
  "tier": "medium",
  "evidence_refs": [],

  // NEW — both keys always present, never inferred by the client.
  "warn_permitted_by_config": true,
  "warn_permitted_by": "checks.freshness.warn_permitted"   // null when the field is false
}
```

Two properties this shape has that a bare boolean does not:

1. **The UI can cite the rule instead of asserting it.** `warn_permitted_by` gives the popover a
   config key to name, alongside the `config_version` and `config_hash` already displayed. Without
   it the interface would be claiming "this warning is acceptable" on its own authority, which is
   exactly the kind of unsourced claim the rest of this plan exists to prevent.
2. **`false` is distinguishable from "not evaluated".** Both keys always present means a missing
   field is a contract violation rather than a silent `false`. A permitted warning and an
   unpermitted one look different on screen; a client-side default would make them look the same.

Rendering: a permitted `WARN` shows as `WARN · permitted` with the config key in its popover and
does **not** appear in the blocking list. An unpermitted `WARN` appears in the blocking list with
the other blockers. Per P2-D3, neither can be cleared by a plan approval — approval covers risk,
never failed or unpermitted evidence.
