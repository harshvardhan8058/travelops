---
name: build-ui-screen
description: Build or modify a TravelOps UI screen or component, applying the Operations Room design system. Use when writing React or TypeScript under frontend/src, styling anything, or working on the Ops Board, recovery workspace, assurance panel, policy citation, cascade graph or reports.
---

# Build a UI screen

This is an **airline operations console**, not an AI landing page. Reference points are
Bloomberg Terminal, Linear, Stripe and Palantir Foundry — dense, calm, monochrome, where
colour carries meaning.

Look: `docs/21-design-system.md`. Behaviour: `docs/27-ui-specification.md`.

## Banned outright — the build fails on these

No **purple, violet, indigo or fuchsia**. No gradient text, glowing orbs, aurora blobs,
glassmorphism on cards, ✨🤖🚀 emoji icons, rainbow chart palettes, `rounded-3xl` pills or
bouncy springs.

That palette is the default AI-demo look and reads as a template. Tailwind's `theme.colors` is
**replaced** rather than extended, so those hues are unavailable, and
`npm run tokens:check` catches any hand-written literal.

## No colour literals, ever

Colour lives in `src/design/tokens.css` and nowhere else. One `grep '#'` under `src/` should
return only that file. Use the semantic Tailwind names: `bg-surface`, `text-fg-muted`,
`border-subtle`, `text-accent`, `text-state-warn`.

The accent (instrument cyan) is for brand, focus and active states — **never** for status. If
the brand colour were amber, an amber button would be indistinguishable from an amber warning.

## Import primitives, never duplicate them

```tsx
import {
  MonoValue, StateBadge, CheckStateBadge, RiskChip, ProvenanceDot,
  WhyPopover, Panel, EmptyState, LoadingState, ErrorState, AgeIndicator,
} from '@/components/ui/primitives';
```

Stream D owns all of `frontend/`, including these. If a primitive is missing, add it to
`primitives.tsx` rather than building a local variant inside a feature folder.

## Four rules that make it look like instrumentation

1. **Every operational number goes through `<MonoValue>`.** Flight numbers, gates, timestamps,
   delays, amounts, PNRs. Tabular figures mean digits align down a column, so the eye scans
   instead of reads. This single detail does most of the work.
2. **Every status goes through `<StateBadge>`** — icon **and** label **and** colour. Never
   colour alone: deuteranopia makes green and amber hard to separate, and projectors wash out
   hue.
3. **Every derived number gets a `<WhyPopover>`** explaining where the input came from, which
   rule produced it, and when. Highest-trust, cheapest feature in the product.
4. **Every data surface shows `<ProvenanceDot>`.** A controller who cannot tell live data from
   simulated cannot make a decision.

## Never display

- An uncalibrated percentage. Risk is an index plus a band.
- A dated or fixture policy pack as current law. Render `pack.ui_label` verbatim; never
  upgrade the label by hand.
- A hardcoded count. Totals come from the API, computed from records.
- A locally computed entitlement. Render what the API returns.

## Density and geometry

14px body (not 16 — ops UI is denser), 34px table rows, 4px spacing scale, `rounded-md`,
**1px borders instead of shadows**, 100–220ms `ease-out` transitions. Lucide icons only,
16px dense / 20px rail, 1.5 stroke. Target 1920×1080 — it will be projected.

## All five states, every time

| State | Requirement |
| --- | --- |
| Loading | Skeleton matching final dimensions. No layout shift |
| Empty | Says what it is, why it is empty, what to do next |
| Populated | The specified layout |
| Error | Error code, correlation ID, retry affordance |
| Degraded | Explicit banner naming the fixture or offline provider |

A blank panel during a demo reads as broken. `No open incidents · inject bengaluru_storm to
begin` reads as finished.

## Work against fixtures

`VITE_USE_FIXTURES=true` is the default, so the whole UI runs with **no backend**. The shapes
in `fixtures/api/*.json` are contractual — match them rather than inventing a shape.

## Before committing

```bash
cd frontend && npm run typecheck && npm run lint && npm run tokens:check && npm run build
```

Then check keyboard reachability with a visible focus ring, and WCAG AA contrast.
