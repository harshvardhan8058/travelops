# 21. Design System — "Operations Room"

The UI is the product. A judge sees the interface for six minutes and forms an opinion about the
engineering behind it. This document removes every UI decision from the sprint so nobody improvises at
2am.

## The single most important rule

> **No purple. No violet. No indigo. No gradient-on-dark "AI product" look.**

Every AI demo at every hackathon in 2026 is purple-to-blue gradient with a glowing orb and a sparkle
icon. It reads as *template*. It signals "we styled this in an afternoon" precisely when we need to
signal "this could run in an airline's operations centre tomorrow."

We are building an **airline operations console**, not an AI landing page. The reference points are
Bloomberg Terminal, Linear, Stripe's dashboard, Vercel, Palantir Foundry, and real airline OCC
displays — dense, calm, monochrome, where **colour means something**.

### Banned outright

| Banned | Why |
| --- | --- |
| Purple / violet / indigo as brand or accent | The default AI aesthetic. Instantly generic |
| Gradient text headings | Decorative, hurts legibility, dates the product |
| Glowing orbs, neon blur backgrounds, aurora blobs | Landing-page decoration on an ops tool |
| ✨ 🤖 🚀 sparkle/emoji as UI iconography | Reads as toy. Use a real icon set |
| Glassmorphism everywhere | One blurred layer maximum (command palette). Not on cards |
| Rainbow or multi-hue chart palettes | Colour is reserved for operational state |
| Drop shadows on everything | Use a border. Elevation is earned, not default |
| Rounded-3xl pill cards | Ops UI is `rounded-md`. Precision, not pillows |
| Bouncy spring animation | Confidence is calm. 150ms ease-out |

The Gamma deck asked for glassmorphism because a *presentation* is a marketing surface. The
*application* is not. Different medium, different rules. This is deliberate, not a contradiction.

## Colour system

Near-monochrome graphite base, one non-status accent, and a semantic ramp reserved exclusively for
operational meaning.

**Why the accent is not green, amber or red:** those three carry operational status. If the brand
colour were amber, an amber button would be indistinguishable from an amber warning. So the accent sits
outside the semantic ramp — a cool instrument cyan — and status colours stay unambiguous.

### Tokens

```css
:root {
  /* Base — cool graphite, deliberately not navy, never violet */
  --bg-base:        #0B0F14;   /* app background */
  --bg-surface:     #111821;   /* cards, panels */
  --bg-raised:      #18212C;   /* popovers, hovered rows */
  --bg-inset:       #080B0F;   /* wells, code, table headers */

  --border-subtle:  #1E2833;
  --border-default: #283542;
  --border-strong:  #38485A;

  --text-primary:   #E7EDF4;
  --text-secondary: #9AA8B6;
  --text-muted:     #66757F;
  --text-inverse:   #0B0F14;

  /* Accent — "instrument cyan". Brand, focus, active, links. Never a status */
  --accent:         #3FC9DE;
  --accent-hover:   #5CD8EA;
  --accent-pressed: #2AAFC4;
  --accent-subtle:  rgba(63, 201, 222, 0.12);
  --accent-border:  rgba(63, 201, 222, 0.32);

  /* Semantic — operational state ONLY */
  --state-ok:       #3DD68C;   /* on time, resolved, executed */
  --state-warn:     #F5A623;   /* at risk, degraded, awaiting approval */
  --state-crit:     #F2555A;   /* disrupted, breached, failed */
  --state-info:     #5B9DF9;   /* informational, scheduled */
  --state-neutral:  #66757F;   /* cancelled, n/a, skipped */

  /* Each state gets a tinted background for badges and row highlights */
  --state-ok-bg:    rgba(61, 214, 140, 0.12);
  --state-warn-bg:  rgba(245, 166, 35, 0.12);
  --state-crit-bg:  rgba(242, 85, 90, 0.12);
  --state-info-bg:  rgba(91, 157, 249, 0.12);

  --focus-ring:     #3FC9DE;
  --radius:         6px;
}
```

Dark is the default and the demo theme — operations centres run dark, and it photographs better on a
projector. A light theme is a stretch goal, not a sprint commitment.

### Colour discipline

- A screen should be **~90% graphite and text**. Colour is punctuation.
- Never encode meaning in colour alone: every state badge carries an icon *and* a label. Deuteranopia
  makes `--state-ok` and `--state-warn` hard to separate.
- Charts use a single-hue ramp of the accent, plus semantic colours for thresholds. No categorical
  rainbow.

## Typography

| Role | Font | Notes |
| --- | --- | --- |
| UI, body, headings | **Inter** (or Geist Sans) | `-0.011em` tracking at display sizes |
| Numbers, times, flight codes, IDs | **JetBrains Mono** | `font-variant-numeric: tabular-nums` |

**The monospace rule is the signature move.** Every flight number, gate, timestamp, duration, rupee
amount, delay in minutes and PNR renders monospaced with tabular figures. Digits align vertically down a
column, so the eye scans instead of reading. This one decision is what makes the UI look like
instrumentation rather than a web app.

```
6E 2134   BLR → DEL   09:40   +47m   ₹5,000
6E 811    BLR → BOM   10:15   +12m   ₹    0
```

### Scale

| Token | Size / line-height | Use |
| --- | --- | --- |
| `display` | 30 / 36, semibold | One per screen, maximum |
| `title` | 20 / 28, semibold | Panel headers |
| `subtitle` | 16 / 24, medium | Card headers |
| `body` | 14 / 20, regular | Default — **not 16px**; ops UI is denser |
| `label` | 12 / 16, medium, `0.04em`, uppercase | Field labels, column headers |
| `mono-sm` | 12.5 / 18 | Table data, timestamps |
| `caption` | 11 / 14 | Source attribution, versions |

Three weights only: 400, 500, 600. No 700, no 300.

## Layout, spacing, density

4px base unit. Only `4 · 8 · 12 · 16 · 24 · 32 · 48 · 64`.

```
┌──┬────────────────────────────────────────────────────────────┐
│  │  Top bar · 52px · live clock · LLM_MODE · system health    │
│56├──────────────────────────────────┬─────────────────────────┤
│px│                                  │                         │
│  │   Ops Board                      │   Decision Timeline     │
│  │   disruption cards + cascade      │   380px, live stream    │
│  │                                  │                         │
└──┴──────────────────────────────────┴─────────────────────────┘
```

- Left rail 56px, icon-only, tooltips on hover. No expanding sidebar — it steals demo pixels.
- Table rows **34px**. Tight is correct here; controllers scan hundreds of rows.
- Panels: 1px `--border-subtle`, `--bg-surface`, `--radius` 6px, **no shadow**.
- Max content width: none. Ops tools fill the viewport.
- Target 1920×1080 first — that is the projector.

## Motion

| Interaction | Duration | Easing |
| --- | --- | --- |
| Hover, focus | 100ms | `ease-out` |
| Panel, drawer | 180ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| New timeline event | 220ms | fade + 4px rise |
| Number changing | 300ms | count-up, tabular so width never jitters |

Animate only what carries meaning: an event arriving, a gate resolving, a status flipping. Nothing
loops, nothing pulses, nothing floats. Honour `prefers-reduced-motion` by dropping to opacity only.

Framer Motion stays optional — most of this is CSS transitions.

## The five signature screens

These are what the demo shows and what the build must nail. Everything else is scaffolding.

**1. Ops Board** — the opening shot. Live disruption cards, each with flight, route, delay, passengers
affected, and current state. One card is mid-recovery with a progress rail.

**2. Cascade view** — 8 delayed flights, 9 crew rotations, downstream connections. A node-link graph
where edges are *pairings and connections*, not just flights. This is the visual that answers
"why 9 and not 8?" without a word. Built with SVG over SQL joins — no graph database.

**3. Decision Timeline** — the right rail, always visible, streaming. Every entry timestamped
monospace, with actor (orchestrator / service / agent), action, and a chevron opening the evidence.
This is `decision_log` rendered honestly.

**4. Assurance Gate panel** — replaces the old "92% confidence" badge. Six deterministic checks with
pass/fail, the resulting gate decision, and the reason. When it says `needs_human`, an approve/reject
control appears. See [`18-decision-assurance-gate.md`](18-decision-assurance-gate.md).

**5. Policy citation card** — the entitlement, the rule that produced it, jurisdiction, document
version, effective date, and the quoted clause. Looks like a legal artefact, because it is one. See
[`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md).

### Two details worth the ten minutes each

- **`LLM_MODE` indicator in the top bar.** A monospace chip reading `LLM: LIVE`, `FIXTURE` or `OFF`.
  Flipping it to `OFF` on stage and watching the recovery still complete is the strongest single moment
  in the demo, and it needs a visible home.
- **Data provenance dots.** A 6px dot on every data panel: `--state-ok` for live/real,
  `--state-info` for simulated. Hovering names the source. When a judge asks "is this real data?", the
  UI has already answered.

## Implementation

Tailwind + shadcn/ui, with the default theme **overridden**, not accepted. shadcn ships a neutral
theme; the danger is a component library default or a copied snippet reintroducing violet.

```js
// tailwind.config.ts — extend, then use only these
colors: {
  base: '#0B0F14', surface: '#111821', raised: '#18212C', inset: '#080B0F',
  border: { subtle: '#1E2833', DEFAULT: '#283542', strong: '#38485A' },
  fg: { DEFAULT: '#E7EDF4', secondary: '#9AA8B6', muted: '#66757F' },
  accent: { DEFAULT: '#3FC9DE', hover: '#5CD8EA', pressed: '#2AAFC4' },
  state: { ok: '#3DD68C', warn: '#F5A623', crit: '#F2555A', info: '#5B9DF9', neutral: '#66757F' },
}
```

Rules for the build:

- Icons: **Lucide** only, 16px in dense UI, 20px in the rail, `1.5` stroke.
- No component gets a colour literal. Tokens or nothing — one grep for `#` in `src/` should come back
  empty apart from the config.
- Every state badge is one shared `<StateBadge>`. Never restyle a status inline.
- Charts: Recharts with an explicit palette passed in. Never the defaults.
- Empty states are designed, not blank: what it is, why it's empty, what to do.
- Skeletons match final layout dimensions so nothing reflows.
- Contrast: WCAG AA. `--text-muted` on `--bg-base` is 4.6:1 — the floor. Nothing lighter.
- Focus: 2px `--focus-ring` at 2px offset, always visible. Never `outline: none`.

## Definition of done for any screen

1. Zero purple, zero gradient, zero glow.
2. All numbers monospaced and tabular.
3. Colour appears only as operational state or a single accent.
4. Keyboard reachable with a visible focus ring.
5. Legible at 1920×1080 from three metres — the projector test.
6. Empty, loading and error states all exist.
