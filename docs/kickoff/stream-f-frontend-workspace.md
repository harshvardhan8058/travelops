# Account 6 — Stream F · Frontend Workspace

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream F — Frontend Workspace. You own the screens where the work actually happens:
the recovery workspace, the assurance panel, the approval queue, the policy citation card,
the cascade graph and the executive report.

READ FIRST (in this order):
  .kiro/steering/travelops.md               - binding rules, including the UI rules
  docs/27-ui-specification.md               - screens 2, 3, 4, 5 and 7 are yours
  docs/21-design-system.md                  - tokens, density, motion, what is banned
  docs/18-decision-assurance-gate.md        - what the assurance panel must convey
  docs/22-crew-pairing-model.md             - why the cascade graph matters
  frontend/src/components/ui/primitives.tsx - IMPORT these; never duplicate them
  fixtures/api/                             - the exact shapes you render

I OWN ONLY THESE PATHS:
  frontend/src/features/incident/
  frontend/src/features/assurance/
  frontend/src/features/policy-citation/
  frontend/src/features/cascade/
  frontend/src/features/reports/
Do not create or modify anything outside them. Stream E owns tokens, primitives, the app
shell, the API client and the Ops Board. If you need a primitive that does not exist, tell
me and I will request it from Stream E - do not build your own. Never touch backend/.

BRANCH: stream/f/recovery-workspace
Run `npm run typecheck`, `npm run lint` and `npm run tokens:check` before every commit.
Never push to main.

SETUP:
  cd frontend && npm install && npm run dev
VITE_USE_FIXTURES=true is the default, so you build against committed fixtures with NO
BACKEND. Your routes currently render StreamPlaceholder components - replace them.

AVAILABLE TO YOU ALREADY (import, do not rebuild):
  from '@/components/ui/primitives': MonoValue, StateBadge, CheckStateBadge, RiskChip,
    ProvenanceDot, WhyPopover, Panel, EmptyState, LoadingState, ErrorState, AgeIndicator
  from '@/api/client': api.incident, api.assurance, api.policy, api.incidentGroup,
    api.report, api.submitDecision
  from '@/api/types': every response type, plus CHECK_ORDER

YOUR WORK, IN THIS ORDER:

1. Recovery Workspace at /incidents/:incidentId (docs/27 screen 2). Three columns:
   - LEFT Evidence: weather used, risk factors, affected entities, retrieved precedent.
     All inputs, nothing editable.
   - CENTRE Plan: ordered task list with state per task. Above it, a generator chip stating
     "Planner · groq · llama-3.3-70b · prompt v1" OR "Fallback playbook · deterministic".
     NEVER ambiguous - a judge must always know whether a model was involved.
   - RIGHT Assurance panel for the selected task.
   Header carries the state rail: detected -> assessing -> planning -> assuring ->
   executing -> resolved.

2. Assurance panel (docs/27 screen 4). All six checks in CHECK_ORDER, each showing
   PASS/WARN/FAIL as icon AND word AND colour. Reason codes, evidence refs, and the config
   version AND hash always visible - a replay must be able to prove which semantics applied.
   When the decision is needs_human it becomes an approve/reject panel with a MANDATORY
   reason field.
   Render the fixture case where every check passes but the action still blocks because it
   is high risk. That is the point of the gate, and it must be legible.

3. Approval Queue at /assurance. Everything blocked across all incidents, newest first.
   Deliberately NO bulk-select for high-risk items - approving eight cash payouts in one
   click defeats the gate.

4. Policy and Citation at /policy/:incidentId (docs/27 screen 5). The strongest screen in
   the product:
   - Pack status banner at the TOP, rendering pack.ui_label verbatim. Never a manual
     override, never upgraded by hand.
   - Entitlement breakdown showing the ACTUAL formula, e.g.
     "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000".
   - "Not owed" is a RESULT, displayed as such - not an absence.
   - Citation card: pack id, version, source document, clause refs, clause text.
   - Cause comparison toggle: recompute the same incident as weather versus crew rostering.
     The amounts change and the rules that fired change. Nothing demonstrates a real rules
     engine faster.
   - Missing-evidence state: name the exact missing fact and show that the result is
     needs_human, not a guessed number.
   - Excluded rules surface a supersession notice.

5. Cascade view at /cascade/:groupId (docs/27 screen 3). SVG node-link graph, no graph
   database. Edges MUST be labelled with the propagation mechanism: operating, onward_duty,
   second_pairing, positioning. Counts come from the API - never hardcode a total.
   A reviewer must be able to count nine pairing nodes and read why each one is affected.
   That is what answers "why nine rotations for eight flights?" without a word being said.

6. Executive Report at /reports/:incidentId (docs/27 screen 7). Metrics derived from records
   only. The generated narrative is clearly attributed. If a metric is null, show it as
   absent - never substitute a placeholder sentence.

NON-NEGOTIABLE:
  - NO purple, violet, indigo or fuchsia. No gradients, glows, glassmorphism on cards, or
    emoji as icons. `npm run tokens:check` enforces it.
  - No colour literal anywhere. Tokens only.
  - Every operational number through MonoValue with tabular figures.
  - Every status through StateBadge - icon AND label, never colour alone.
  - Never show an uncalibrated percentage. Risk is an index plus a band.
  - Never present a dated or fixture policy pack as current law.
  - Never let the UI compute an entitlement. Render what the API returns.

DEFINITION OF DONE for every screen:
All five states implemented (loading skeleton with no layout shift, designed empty,
populated, error with correlation ID, degraded banner). Keyboard reachable with visible
focus. WCAG AA contrast. Legible at 1920x1080 from three metres - it will be projected.

Start with the Recovery Workspace layout. Read fixtures/api/incident_detail.json and
fixtures/api/assurance.json first so you match the real shapes, then tell me your plan.
```
