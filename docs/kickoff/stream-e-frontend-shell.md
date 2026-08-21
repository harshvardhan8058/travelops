# Account 5 — Stream E · Frontend Shell

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream E — Frontend Shell. You own the design system in code, the shared
primitives, the app shell, the Ops Board and the Decision Timeline.

READ FIRST (in this order):
  .kiro/steering/travelops.md              - binding rules, including the UI rules
  docs/21-design-system.md                 - tokens, density, motion, what is banned
  docs/27-ui-specification.md              - screens 1, 6 and 8 are yours
  frontend/src/design/tokens.css           - the single source of colour
  frontend/src/components/ui/primitives.tsx - what already exists

I OWN ONLY THESE PATHS:
  frontend/src/design/
  frontend/src/components/
  frontend/src/api/
  frontend/src/features/ops-board/
  frontend/src/features/timeline/
  frontend/src/features/sources/
  frontend/src/App.tsx
  frontend/src/main.tsx
  frontend/src/index.css
  frontend/scripts/
  frontend/tailwind.config.ts
Do not create or modify anything outside them. Stream F owns
frontend/src/features/{incident,assurance,policy-citation,cascade,reports}/ - if they need a
primitive that does not exist, they will ask me and I will ask you. Never touch backend/.

BRANCH: stream/e/ops-board
Run `npm run typecheck`, `npm run lint` and `npm run tokens:check` before every commit.
Never push to main.

SETUP:
  cd frontend && npm install && npm run dev
VITE_USE_FIXTURES=true is the default, so the whole UI runs with NO BACKEND. You are never
blocked on an endpoint.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - src/design/tokens.css       COMPLETE. Graphite base, instrument cyan accent, state ramp
  - tailwind.config.ts          COMPLETE. theme.colors is REPLACED, so Tailwind's purple,
                                violet, indigo and fuchsia are unavailable. Keep it that way
  - src/components/ui/primitives.tsx  MonoValue, StateBadge, CheckStateBadge, RiskChip,
                                ProvenanceDot, WhyPopover, Panel, EmptyState, LoadingState,
                                ErrorState, AgeIndicator all COMPLETE
  - src/components/ui/AppShell.tsx    rail, top bar, degradation banner, timeline rail,
                                blocked-actions bar all COMPLETE
  - src/api/client.ts + types.ts      COMPLETE, fixture-backed
  - src/features/ops-board/OpsBoard.tsx        network strip + flight board COMPLETE
  - src/features/timeline/DecisionTimeline.tsx COMPLETE
  - scripts/check-tokens.mjs    the guard that fails the build on a colour literal

YOUR WORK, IN THIS ORDER:

1. Upgrade WhyPopover from the title-attribute version to a real positioned popover.
   Every derived number must explain: where the input came from, which rule or formula
   produced it, and when. This is the highest-trust, cheapest feature in the product -
   the API already returns provenance and evidence refs on every response.

2. Ops Board polish: sortable columns, the filter chips (All / At risk / Disrupted /
   In recovery / Resolved), and default sort that puts anything non-normal on top. A
   controller should never scroll to find the problem.

3. Risk factor popover: clicking a RiskChip shows contributing factors, rule version,
   evidence refs and observation age.

4. Provenance ledger screen at /sources (docs/27 screen 8). One row per source: kind,
   provider, last checked, licence, health. This is the definitive answer to "is any of
   this real?".

5. Timeline replay screen at /replay/:incidentId (docs/27 screen 6). Full-height forensic
   view with scrubbing, filter by actor kind, and an "only decisions" toggle.

6. Command palette on Cmd-K / Ctrl-K: jump to a flight or incident, inject a scenario,
   reset demo data, switch LLM_MODE, open any route. This is the ONE place a single blurred
   overlay layer is allowed.

7. Keyboard model: j/k to move, Enter to open, / to search, g-then-o/c/a/p to navigate,
   Esc to close. Everything reachable without a mouse, with a visible focus ring.

NON-NEGOTIABLE:
  - NO purple, violet, indigo or fuchsia. No gradients, glows, aurora blobs, glassmorphism
    on cards, gradient text, or emoji as icons. That palette is the default AI-demo look and
    reads as a template. `npm run tokens:check` enforces it.
  - No colour literal in any component. Tokens only. One grep for "#" under src/ should
    return only tokens.css.
  - Every status renders through StateBadge - icon AND label AND colour, never colour alone.
    Deuteranopia makes green and amber hard to separate.
  - Every operational number renders through MonoValue with tabular figures. Flight numbers,
    gates, timestamps, delays, amounts, PNRs. This single detail is what makes the UI look
    like instrumentation rather than a web app.
  - Never show an uncalibrated percentage. Risk is an index plus a band.
  - The policy badge renders the pack's real status verbatim. Never upgrade the label.
  - Icons: Lucide only, 16px dense, 20px rail, 1.5 stroke.
  - Elevation is a 1px border, not a shadow. rounded-md, not rounded-3xl.
  - Motion: 100-220ms ease-out. Nothing loops, pulses or floats. Honour reduced-motion.

DEFINITION OF DONE for every screen:
All five states implemented (loading skeleton with no layout shift, designed empty,
populated, error with correlation ID, degraded banner). Keyboard reachable with visible
focus. WCAG AA contrast. Legible at 1920x1080 from three metres - it will be projected.

Start by reading docs/21-design-system.md and the existing primitives, then tell me your
plan for the WhyPopover upgrade.
```
