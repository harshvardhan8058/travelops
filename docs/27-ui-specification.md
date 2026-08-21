# 27. UI Specification — Functional Behaviour

[`21-design-system.md`](21-design-system.md) defines how it looks. This document defines what it does:
every screen, every feature, every interaction, and what happens when things go wrong.

## The user and the ten-second test

The primary user is an **Operations Controller** during a live disruption. They are under time pressure,
watching several things at once, and accountable for what the system did.

Within ten seconds of looking at the screen they must be able to answer:

1. What is broken, and how bad is it?
2. What has the system already done without me?
3. What is waiting for my decision?
4. Can I trust the number I am looking at?

Every design decision below serves one of those four. If a feature serves none of them, it is not built.

## Information architecture

```text
┌──┬────────────────────────────────────────────────────────────────────────────┐
│  │ Top bar 52px · clock · LLM_MODE · POLICY_MODE · provider health · ⌘K       │
│  ├──────────────────────────────────────────────┬─────────────────────────────┤
│56│                                              │                             │
│px│  Main workspace                              │  Decision Timeline 380px    │
│  │  (route-dependent)                           │  always visible, streaming  │
│  │                                              │                             │
│  ├──────────────────────────────────────────────┴─────────────────────────────┤
│  │ Blocked-actions bar — appears only when items await human decision         │
└──┴────────────────────────────────────────────────────────────────────────────┘
```

**Left rail** (icon-only, 56px, tooltip on hover):

| Icon | Route | Purpose |
| --- | --- | --- |
| `layout-dashboard` | `/` | Ops Board |
| `git-fork` | `/cascade/:groupId` | Cascade view |
| `list-checks` | `/incidents/:id` | Recovery workspace |
| `shield-check` | `/assurance` | Approval queue |
| `scale` | `/policy/:incidentId` | Entitlements and citations |
| `history` | `/replay/:incidentId` | Timeline replay |
| `file-text` | `/reports/:incidentId` | Executive report |
| `database` | `/sources` | Provenance ledger |

No expanding sidebar. It would steal demo pixels and controllers do not need labels after day one.

**The Decision Timeline never leaves the screen.** It is not a page you navigate to. Anything the system
does appears there within a second, on every route. That single decision is what makes the product feel
like an operating layer rather than a set of forms.

---

## Screen 1 — Ops Board

The default route and the opening shot of the demo. Situational awareness, nothing else.

### Zones

**Network strip** (top, ~96px). One compact tile per airport in the configured set:

- ICAO code in mono, city name small
- Current weather: wind, visibility, ceiling
- Risk level chip: `low` / `elevated` / `high` / `severe`
- Provenance dot: green = live, blue = fixture
- Age of the observation in mono (`4m`, `71m`). Turns amber past the freshness limit

A stale observation is visible *before* it causes a gate failure. That prevents the "why did it block?"
confusion during a demo.

**Flight board** (centre, the largest zone). Dense table, 34px rows, tabular mono:

| Column | Notes |
| --- | --- |
| Flight | `6E 2134`, mono |
| Route | `BLR → DEL` |
| Sched / Est | Local time, mono, delta in amber when non-zero |
| Status | `<StateBadge>` — icon + label + colour |
| Risk | Index number + level chip. Click opens the factor breakdown |
| Pax | Count, mono |
| Connections | At-risk count; amber when > 0 |
| Incident | Reference link when one is open |
| Source | Provenance dot |

Sortable. Filter chips above: `All` · `At risk` · `Disrupted` · `In recovery` · `Resolved`. Default sort
puts anything non-normal on top — a controller should never scroll to find the problem.

**Active incidents** (right of flight board, or below at narrow widths). One card per open incident:

- Reference, flight, trigger, opened-at
- State machine progress rail: `detected → assessing → planning → assuring → executing → resolved`
- Rollups: pax affected, connections at risk, crew pairings affected, cost to date
- Amber left border when the incident has anything awaiting approval

### Interactions

- Click a row → recovery workspace for that flight's incident, or a peek panel if none is open
- Click a risk value → popover listing contributing factors, rule version, evidence refs, observation age
- Click any provenance dot → source detail: provider, kind, retrieved-at, licence
- `Inject scenario` button (demo-mode only) and `Reset demo` (admin role only)

---

## Screen 2 — Recovery Workspace

Where the actual work happens. Route `/incidents/:id`. Three columns.

**Header:** incident reference, flight, trigger, elapsed timer, state rail with the current state
highlighted, and a `Continue workflow` action when the incident is resumable.

**Left column — Evidence** (320px). What the system knows and where it came from:

- Weather observation used, with timestamp and provenance
- Risk index, level, factors, rule version
- Affected entities: flight, passengers, bookings, connections, crew pairings, candidate hotels
- Retrieved precedent, when present, with the reason it matched

Everything here is an input. Nothing here is editable.

**Centre column — Plan and execution.** The ordered task list:

Each task row shows sequence number, action type in mono, target, dependency chips, and status
(`pending` → `proposed` → `assured` → `executing` → `succeeded` / `failed` / `skipped` /
`needs_human` → `rejected`).

Above the list, a generator chip states where the plan came from: `Planner · groq · llama-3.3-70b ·
prompt v1`, or `Fallback playbook · deterministic`. Never ambiguous.

Expanding a task reveals its inputs, its assurance evaluation, its resulting action, cost, and
idempotency key.

**Right column — Assurance.** For the selected task, the six checks:

```text
ASSURANCE  ·  task 3  ·  reserve_hotel_block
config assurance-v1 · hash 9f2c…

✓ Evidence completeness      PASS
⚠ Source freshness           WARN   METAR VOBL 64m old, max 60m
✓ Entity validation          PASS
✓ Policy compliance          PASS
✓ Conflict detection         PASS
✓ Action risk tier           PASS   tier=medium

DECISION   execute_flagged
```

Colour is not the only signal — each row carries an icon and the literal word. The config version and
hash are always visible, because a replay must be able to prove which semantics applied.

When the decision is `needs_human`, this column becomes an action panel: blocking reasons listed, then
`Approve` / `Reject` with a mandatory reason field. Submitting writes an immutable decision; the original
evaluation is never mutated.

**Bottom strip — Actions.** Executed side effects with actor, result, cost, provenance and idempotency
key. This is the "what did it actually do in the world" answer.

---

## Screen 3 — Cascade View

The screen that answers *"why nine crew rotations for eight flights?"* without a word of explanation.

An SVG node-link graph over SQL results. No graph database.

```text
        ┌─ weather event: VOBL ─┐
                    │
    ┌───────┬───────┼───────┬───────┐
   6E2134  6E811  6E455   AI503  ...      ← 8 flight nodes
     │  \    │       │       │
     │   \   │       │       │
   PAIR-A  PAIR-B  PAIR-C  PAIR-D ...     ← 9 pairing nodes
```

- **Node types:** event, flight, crew pairing, connection group, hotel-capacity pool
- **Edge labels carry the mechanism** — this is the whole point:
  - `operating` — crew aboard this flight
  - `onward duty` — next leg of the same pairing now at risk
  - `second pairing` — cockpit and cabin on different pairings
  - `positioning` — crew deadheading to operate another flight
- Node size reflects passengers affected; border colour reflects state
- Counts in the legend are **computed from records**, never hardcoded

Interactions: click a node to filter the flight board and timeline to it; hover an edge for the
propagation reason; toggle layers (crew / connections / hotels) to reduce clutter; `Explain this cascade`
calls the Explainer and renders grounded prose beside the graph.

A judge can literally count the nine pairing nodes and read why each one is there. That is far stronger
than a verbal defence of a number on a slide.

---

## Screen 4 — Approval Queue

Route `/assurance`. Everything currently blocked, across all incidents, newest first.

Each item: incident, task, action type, risk tier, blocking reasons, age. Bulk-select is deliberately
**not** offered for `high` risk items — approving eight cash payouts with one click defeats the gate.

The queue is also surfaced as the persistent bottom bar on every route: `3 actions awaiting approval`.
A blocked action must never be discoverable only by navigating to the right page.

---

## Screen 5 — Policy and Citation

Route `/policy/:incidentId`. Where a rupee figure becomes defensible.

**Pack status banner, always at the top.** Not a footnote:

| Mode | Banner |
| --- | --- |
| `demo` | Neutral: `DEMO FIXTURE · fictional policy · no legal claim` |
| `charter` | Amber-bordered: `MoCA PASSENGER CHARTER · FEB 2019 · PENDING CAR VERIFICATION` |
| `verified` | Green: `VERIFIED · <pack> <version> · reviewed <date>` |

The UI renders the pack's real status. There is no manual override.

**Entitlement breakdown.** Per entitlement:

- Type, amount and currency in mono — or explicitly `Not owed`, which is a result, not an absence
- The rule that fired, by ID
- The exact input facts used
- The formula applied, e.g. `least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000`
- Reason codes where an amount was suppressed

**Citation card.** Pack ID, version, status, source document, retrieved date, source reference, and the
clause text. It should read like a legal artefact, because it is one.

**Two features that make this the strongest screen in the product:**

1. **Cause comparison.** A toggle recomputing the same incident under a different cause — weather versus
   crew rostering. The amounts change, the rules that fired change, and the reasoning is visible.
   Nothing demonstrates a real rules engine faster.
2. **Missing-evidence state.** When a required fact is absent, the panel shows exactly which fact is
   missing and that the result is `needs_human` — not a guessed number. This is the screen that proves we
   are not doing a `trigger_type` lookup.

Excluded rules surface a supersession notice: the rule, why it is suspected superseded, and that it was
not evaluated.

---

## Screen 6 — Timeline and Replay

The right rail is the live view. Route `/replay/:incidentId` is the full-height forensic view.

Each entry: timestamp in mono, actor badge (`orchestrator` / `agent` / `service` / `human` / `provider`),
event type, one-line summary, expand chevron. Expanding gives the full record — inputs, evidence refs,
assurance reference, config hash, correlation ID.

Replay controls scrub through the incident and reconstruct state at any point. Filters by actor type and
by stage. An `Only decisions` toggle hides routine chatter.

This is a read over immutable records. It is not a model narrating history.

---

## Screen 7 — Executive Report

Metrics computed from records: time to first action, time to resolution, passengers reaccommodated,
connections protected, actions executed versus blocked, human approvals, cost breakdown, provider mode
during the incident.

Below them, the generated narrative, clearly attributed to the Report Generator, with every figure
traceable to the metrics above. Export to Markdown or PDF.

Any metric the records cannot support is absent. There is no placeholder and no estimate.

---

## Screen 8 — Provenance Ledger

Route `/sources`. One row per data source: name, kind (`real` / `simulated` / `synthetic` / `fixture` /
`unavailable`), provider, last checked, licence and attribution, and current health.

When a judge asks "is any of this real?", this screen is the answer, and it is the same data the badges
elsewhere are derived from.

---

## Cross-cutting features

### The `LLM_MODE` switch

A mono chip in the top bar: `LLM: LIVE` / `FIXTURE` / `OFF`. Clicking it opens a confirm, then switches
mode and shows a persistent banner while degraded.

Flipping it to `OFF` on stage and watching a recovery still complete is the strongest 45 seconds of the
demo. It needs a visible, deliberate home — not a config file.

### "Why?" on every number

Every figure in the product is clickable and answers: where the input came from, which rule or formula
produced it, and when. Risk indices, amounts, counts, durations.

This is the highest-trust feature in the design and the cheapest to build, because the API already
returns provenance and evidence references on every response.

### Degraded-mode honesty

When any provider is in fixture or offline mode, a persistent bar states which. The system never hides
degradation to look healthier. A controller who cannot tell live data from simulated data cannot make a
decision.

### Command palette (⌘K / Ctrl-K)

Jump to a flight, incident or pairing; inject a scenario; reset demo data; switch `LLM_MODE`; open any
route. This is the one place a single blurred overlay layer is allowed.

### Keyboard model

| Key | Action |
| --- | --- |
| `j` / `k` | Next / previous row |
| `Enter` | Open selected |
| `a` / `r` | Approve / reject focused blocked action (opens reason field) |
| `t` | Toggle timeline focus |
| `/` | Search |
| `g` then `o` `c` `a` `p` | Go to Ops Board / Cascade / Approvals / Policy |
| `Esc` | Close overlay |

Everything is reachable without a mouse, with a visible focus ring. Judges notice.

---

## States — designed, not accidental

Every data surface implements all five:

| State | Requirement |
| --- | --- |
| Loading | Skeleton matching final dimensions. No layout shift |
| Empty | Says what it is, why it is empty, what to do next |
| Populated | The specified layout |
| Error | Error code, correlation ID, retry affordance, and what still works |
| Degraded | Explicit banner naming the fixture or offline provider |

A blank panel during a demo reads as broken. An empty state that says
`No open incidents · inject bengaluru_storm to begin` reads as finished.

---

## Component inventory

Build these once and reuse everywhere:

`<StateBadge>` · `<RiskChip>` · `<ProvenanceDot>` · `<MonoValue>` · `<CheckRow>` · `<TaskRow>` ·
`<TimelineEntry>` · `<CitationCard>` · `<PackStatusBanner>` · `<EvidenceList>` · `<StateRail>` ·
`<ApprovalPanel>` · `<CascadeGraph>` · `<WhyPopover>` · `<ModeChip>` · `<EmptyState>` · `<ErrorState>`

Rules: no component takes a colour literal; every status renders through `<StateBadge>`; every number
that represents operational data renders through `<MonoValue>`; every figure that has a derivation wraps
in `<WhyPopover>`.

---

## Feature-to-stage mapping

Build in this order. Nothing later is started before the previous gate passes.

**Stage 2 — 20–24 August**

Ops Board, network strip, flight board, active incidents, Decision Timeline rail, Recovery Workspace
with evidence/plan/assurance columns, Approval Queue plus blocked bar, Policy screen in `charter` mode,
Provenance ledger, scenario injector and reset, `WhyPopover` on risk and amounts, all five states.

**Stage 3 — 1–2 September**

Cascade view with the pairing graph, `LLM_MODE` switch and degraded banner, generator attribution,
retrieved-precedent panel, cause comparison toggle, command palette.

**Semi-finals — 9–10 September**

Replay scrubbing, executive report, analytics on gate and approval rates, keyboard model complete,
accessibility pass, projector rehearsal.

**Finals — 16 September**

Hardening only. Performance, empty/error polish, offline verification.

### Deliberately not built

Chat interface of any kind. Passenger-facing portal. Mobile layout. Light theme. Drag-and-drop plan
editing. Dashboard customisation. Notification centre. Multi-tenant switching.

Each would cost days and none answers one of the controller's four questions.

---

## Definition of done, per screen

1. All five states implemented.
2. Zero purple, gradient, glow or default-AI styling. Tokens only.
3. Operational numbers in tabular mono.
4. Every derived figure has a `WhyPopover`.
5. Every data surface shows provenance.
6. Keyboard reachable, visible focus, WCAG AA.
7. Legible at 1920×1080 from three metres.
8. No hardcoded count, amount or status label anywhere in the component tree.
