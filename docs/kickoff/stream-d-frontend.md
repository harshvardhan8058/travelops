# Account 4 — Stream D · Frontend

Paste everything inside the block. Nothing to edit.

**Give this stream your highest token limit.** TSX is the most verbose code in the
repository and UI work is inherently iterative — layout, then five states, then keyboard,
then contrast — so it runs the most read-modify-verify cycles of any account. It also has
the most units left: seven screens, the popover upgrade, the command palette and the
keyboard model.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream D of four — Frontend. You own the entire frontend/ directory: the design
system in code, the shared primitives, the app shell, and all eight screens.

READ FIRST (in this order):
  .kiro/steering/travelops.md               - binding rules, including the UI rules
  docs/21-design-system.md                  - tokens, density, motion, what is banned
  docs/27-ui-specification.md               - all eight screens are mine
  docs/18-decision-assurance-gate.md        - what the assurance panel must convey
  docs/22-crew-pairing-model.md             - why the cascade graph matters
  docs/28-parallel-workstreams.md           - who owns what across the four accounts
  frontend/src/design/tokens.css            - the single source of colour
  frontend/src/components/ui/primitives.tsx - what already exists; import, never duplicate
  fixtures/api/                             - the exact shapes I render

There are reusable procedures in .kiro/skills/. Use them instead of inventing your own:
  build-ui-screen      - the five required states, keyboard model and token rules per screen
  verify-before-commit - the exact checks to run before every commit
  open-stream-pr       - branch, title and review conventions

I OWN ONLY THESE PATHS:
  frontend/          (the entire directory, including scripts/ and tailwind.config.ts)
I may READ the whole repository. I may WRITE only inside frontend/. Never touch backend/,
data/, fixtures/, policy_packs/, config/, docs/ or .kiro/.

The other three streams own, and I never edit:
  Stream A  the backend control plane and every real endpoint
  Stream B  assurance and policy
  Stream C  fixtures/api/*.json - these are CONTRACTUAL. If a fixture is missing a field I
            need, that is a request to Stream C. Never edit a fixture to suit my component,
            and never fabricate a field that no endpoint returns.

The shared guard tests are frozen - the five directly under backend/tests/unit/ plus
backend/tests/contract/test_container_runtime_paths.py, which asserts the ./fixtures mount my
dev server depends on. I may add one; I may never weaken or delete an existing assertion. If a
guard test fails, my code is wrong.

BRANCH: stream/d/frontend
Run `npm run typecheck`, `npm run lint`, `npm run tokens:check` and `npm run build` before
every commit. Never push to main. Never merge my own PR.

SETUP:
  cd frontend && npm install && npm run dev
VITE_USE_FIXTURES=true is the default, so the whole UI runs with NO BACKEND. I am never
blocked on an endpoint, on Stream A, or on a database.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - src/design/tokens.css       COMPLETE. Graphite base, instrument cyan accent, state ramp
  - tailwind.config.ts          COMPLETE. theme.colors is REPLACED, not extended, so
                                Tailwind's purple, violet, indigo and fuchsia are
                                unavailable by construction. Keep it that way.
  - src/components/ui/primitives.tsx  MonoValue, StateBadge, CheckStateBadge, RiskChip,
                                ProvenanceDot, WhyPopover, Panel, EmptyState, LoadingState,
                                ErrorState, AgeIndicator all COMPLETE
  - src/components/ui/AppShell.tsx    rail, top bar, degradation banner, timeline rail,
                                blocked-actions bar all COMPLETE
  - src/api/client.ts + types.ts      COMPLETE and fixture-backed. api.incident,
                                api.assurance, api.policy, api.incidentGroup, api.report,
                                api.submitDecision, plus every response type and CHECK_ORDER
  - src/features/ops-board/OpsBoard.tsx        network strip + flight board COMPLETE
  - src/features/timeline/DecisionTimeline.tsx COMPLETE
  - frontend/scripts/check-tokens.mjs  the guard that fails the build on a colour literal. It
                                strips comments before scanning, so prohibition text in a
                                comment does not self-trigger.
  - frontend/scripts/sync-fixtures.mjs warns and exits 0 when fixtures are absent, so the dev
                                server always starts

Routes not yet built currently render StreamPlaceholder components. Replace them.

=== PHASE 1 — WHAT THE STAGE 2 DEMO ACTUALLY SHOWS. Do these first. ===

1. Upgrade WhyPopover from the title-attribute version to a real positioned popover.
   Every derived number must explain: where the input came from, which rule or formula
   produced it, and when. This is the highest-trust, cheapest feature in the product - the
   API already returns provenance and evidence refs on every response, so this is wiring,
   not invention. Do it first because every later screen uses it.

2. Recovery Workspace at /incidents/:incidentId (docs/27 screen 2). Three columns:
   - LEFT Evidence: weather used, risk factors, affected entities, retrieved precedent.
     All inputs, nothing editable.
   - CENTRE Plan: ordered task list with state per task. Above it, a generator chip stating
     "Planner · groq · llama-3.3-70b · prompt v1" OR "Fallback playbook · deterministic".
     NEVER ambiguous - a judge must always know whether a model was involved.
   - RIGHT Assurance panel for the selected task.
   Header carries the state rail: detected -> assessing -> planning -> assuring ->
   executing -> resolved.

3. Assurance panel (docs/27 screen 4). All six checks in CHECK_ORDER, each showing
   PASS/WARN/FAIL as icon AND word AND colour. Reason codes, evidence refs, and the config
   version AND hash always visible - a replay must be able to prove which semantics applied.
   When the decision is needs_human it becomes an approve/reject panel with a MANDATORY
   reason field.
   Render the fixture case where every check passes but the action still blocks because it
   is high risk. That is the entire point of the gate, and it must be legible without
   explanation.

4. Approval Queue at /assurance. Everything blocked across all incidents, newest first.
   Deliberately NO bulk-select for high-risk items - approving eight cash payouts in one
   click defeats the gate, and a reviewer will notice if it is possible.

=== PHASE 2 — THE SCREENS THAT WIN THE ARGUMENT. ===

5. Policy and Citation at /policy/:incidentId (docs/27 screen 5). The strongest screen in
   the product:
   - Pack status banner at the TOP, rendering pack.ui_label verbatim. Never a manual
     override, never upgraded by hand. It currently reads
     "MOCA PASSENGER CHARTER · FEB 2019 · PENDING CAR VERIFICATION" and that is correct.
   - Entitlement breakdown showing the ACTUAL formula, e.g.
     "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000".
   - "Not owed" is a RESULT, displayed as such - not an absence. A delay produces no cash
     entitlement under this instrument, and the screen should say so plainly.
   - Citation card: pack id, version, source document, clause refs, clause text.
   - Cause comparison toggle: recompute the same incident as weather versus crew rostering.
     The amounts change and the rules that fired change. Nothing demonstrates a real rules
     engine faster.
   - Missing-evidence state: name the exact missing fact and show that the result is
     needs_human, not a guessed number.
   - Excluded rules surface a supersession notice.

6. Cascade view at /cascade/:groupId (docs/27 screen 3). SVG node-link graph, no graph
   database. Edges MUST be labelled with the propagation mechanism: operating, onward_duty,
   second_pairing, positioning. Counts come from the API - never hardcode a total.
   A reviewer must be able to count nine pairing nodes and read why each one is affected.
   That is what answers "why nine rotations for eight flights?" without a word being said.

7. Ops Board polish: sortable columns, the filter chips (All / At risk / Disrupted /
   In recovery / Resolved), and a default sort that puts anything non-normal on top. A
   controller should never scroll to find the problem. Then the risk factor popover:
   clicking a RiskChip shows contributing factors, rule version, evidence refs and
   observation age.

=== PHASE 3 — DEFER THESE IF QUOTA RUNS SHORT. Each is self-contained. ===

8. Provenance ledger at /sources (docs/27 screen 8). One row per source: kind, provider,
   last checked, licence, health. This is the definitive answer to "is any of this real?".

9. Timeline replay at /replay/:incidentId (docs/27 screen 6). Full-height forensic view with
   scrubbing, filter by actor kind, and an "only decisions" toggle.

10. Executive Report at /reports/:incidentId (docs/27 screen 7). Metrics derived from records
    only. The generated narrative is clearly attributed. If a metric is null, show it as
    absent - never substitute a placeholder sentence.

11. Command palette on Cmd-K / Ctrl-K: jump to a flight or incident, inject a scenario,
    reset demo data, switch LLM_MODE, open any route. This is the ONE place a single blurred
    overlay layer is allowed.

12. Keyboard model: j/k to move, Enter to open, / to search, g-then-o/c/a/p to navigate,
    Esc to close. Everything reachable without a mouse, with a visible focus ring.

NON-NEGOTIABLE:
  - NO purple, violet, indigo or fuchsia. No gradients, glows, aurora blobs, glassmorphism
    on cards, gradient text, or emoji as icons. That palette is the default AI-demo look and
    reads as a template. `npm run tokens:check` enforces it and the build fails on a
    violation.
  - No colour literal in any component. Tokens only. One grep for "#" under src/ should
    return only tokens.css.
  - Every status renders through StateBadge - icon AND label AND colour, never colour alone.
    Deuteranopia makes green and amber hard to separate.
  - Every operational number renders through MonoValue with tabular figures. Flight numbers,
    gates, timestamps, delays, amounts, PNRs. This single detail is what makes the UI look
    like instrumentation rather than a web app.
  - Never show an uncalibrated percentage. Risk is an index plus a band.
  - Never present a dated or fixture policy pack as current law. The badge renders the
    pack's real status verbatim; never upgrade the label.
  - Never let the UI compute an entitlement. Render what the API returns.
  - Icons: Lucide only, 16px dense, 20px rail, 1.5 stroke.
  - Elevation is a 1px border, not a shadow. rounded-md, not rounded-3xl.
  - Motion: 100-220ms ease-out. Nothing loops, pulses or floats. Honour reduced-motion.

DEFINITION OF DONE for every screen:
All five states implemented (loading skeleton with no layout shift, designed empty,
populated, error with correlation ID, degraded banner). Keyboard reachable with visible
focus. WCAG AA contrast. Legible at 1920x1080 from three metres - it will be projected.

Start by reading docs/21-design-system.md and the existing primitives, then tell me your
plan for the WhyPopover upgrade before writing code.
```
