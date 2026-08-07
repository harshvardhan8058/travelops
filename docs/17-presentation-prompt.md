# 17. Gamma Presentation Prompt

The canonical Gamma AI prompt for the TechCon 2026 idea submission. **3 slides, hard limit.**

Paste the block below into Gamma verbatim.

## Design intent

Three slides is tight for problem → solution → proof, so each slide is a **dense multi-zone
composition** rather than a sparse card, and the deck carries one continuity device (incident state
moving RED → AMBER → GREEN) so it reads as a single story rather than three unrelated pages.

Judge-optimisation decisions, and why:

| Decision | Criterion served | Reasoning |
| --- | --- | --- |
| Autonomy ladder on slide 1 | Engineering the Autonomous Enterprise | Reframes the category. Assistive AI *cannot* solve a cascade — it establishes why autonomy is required rather than fashionable |
| Cascade as hero visual | Relevance, Creativity | Judges must feel operational scale before seeing the solution |
| "13 agents · 3 use the model" as a headline metric | Technical depth | The single most efficient rebuttal to "is this a chatbot?" |
| Guardrails cluster | Technical depth, Feasibility | Schema validation, policy gate, loop caps, idempotency and escalation signal engineering maturity in very little space |
| DGCA force-majeure panel leads slide 3 | Creativity, Relevance, Business value | Hardest element for another team to replicate; real regulation, real cost consequence |
| Kill-the-model panel | Feasibility, Autonomy | Demonstrates the system survives its own AI failing |
| Qualitative value framing only | Business value, credibility | Invented ROI figures are the fastest way to lose a technical judge |
| Real-vs-simulated honesty marker | Feasibility | Candour about the synthetic hotel data reads as rigour, and pre-empts the question |
| Internal tools left as a marked placeholder | Use of Internal Tools | ⚠️ Unresolved. Must not be invented — see [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) |

## Before submitting

- [ ] Replace the internal-tools placeholder once Coforge tooling is confirmed
- [ ] Verify DGCA figures against the CAR PDF ([`13-compensation-and-policy.md`](13-compensation-and-policy.md))
- [ ] Confirm all registration strings match the sheet exactly

---

## The prompt

```text
Create a 3-slide premium enterprise presentation for a hackathon idea submission.
EXACTLY 3 slides. Do not add a title slide, agenda, thank-you slide, or appendix.
Each slide is a dense, multi-zone visual composition — not a sparse card. 16:9.

═══════════════════════════════════════════════════════════════════════
SECTION 1 — RESEARCH THIS DOMAIN BEFORE DESIGNING
═══════════════════════════════════════════════════════════════════════

Project / Use Case Title: TravelOps AI
Team Name: SkyForge AI
Registration ID: 201
Industry: Travel Transport Hospitality (TTH)
Sub-Industry: Airlines Operations
Event: TechCon 2026 Hackathon (Coforge)
Theme: Engineering the Autonomous Enterprise

NAMING RULE — follow exactly:
"TravelOps AI" is the project. "SkyForge AI" is the team. Never merge them
into a single product name. The product is always TravelOps AI.

Before generating anything, research the airline Operations Control Centre
(OCC) domain and Irregular Operations (IRROPS): how controllers currently
handle weather disruption, why a single event cascades across flights,
passenger connections, hotel accommodation and crew rotations, and why
manual recovery is slow, inconsistent between controllers, and hard to
audit afterwards. Use correct operational vocabulary throughout — IRROPS,
OCC, duty of care, force majeure, METAR/TAF, block time, rotation.

AUDIENCE: hackathon judges scoring on Creativity, Feasibility, Relevance,
Use of Internal Tools, Use of Open Source, and Engineering the Autonomous
Enterprise. They are technically literate, time-poor, and sceptical of AI
demos that are thin wrappers around a language model. Assume each slide
gets a 20-second scan before any detailed reading.

POSITIONING — CRITICAL:
TravelOps AI is an autonomous enterprise operations platform. It is NOT a
chatbot, assistant or copilot, and it has no conversational interface by
design. It detects, reasons, decides, executes and learns. Frame it as an
operating layer — infrastructure, not assistance. Never use the words
"chatbot", "assistant", "copilot" or "prompt" in the slide copy.

═══════════════════════════════════════════════════════════════════════
SECTION 2 — FACTUAL CONTENT (USE ONLY THESE FIGURES)
═══════════════════════════════════════════════════════════════════════

Do NOT invent statistics, market sizes, ROI percentages, cost savings or
industry benchmarks. Use only what appears below. Keep any additional
framing qualitative rather than numeric. Fabricated numbers will lose
credibility with this audience.

THE CASCADE — core scenario:
One storm at Bengaluru (BLR) propagates into:
  8 flights delayed → 600 passengers affected → 22 connections at risk →
  11 hotels required → 9 crew rotations disrupted → executive report

ARCHITECTURE:
- 13 specialised agents. Only 3 use a language model: Planner, Explainer,
  Report Generator.
- Event-driven flow: Event → Prediction → Planner → Workflow → Agents →
  Execution → Memory, with Memory feeding back into Planner.
- Agents communicate through events, never direct calls.
- Every agent returns validated JSON carrying status, confidence and
  reason — never free-form text.
- Deterministic code owns: delay prediction, filtering, sorting, hotel
  search, compensation calculation, business rules.
- The language model owns only: recovery planning, explanation, reporting.

ENGINEERING GUARDRAILS:
Schema validation on model output · policy validation before execution ·
recursion and iteration caps · execution idempotency keys · automatic
escalation to a human when confidence is low.

TECHNOLOGY — all open source or free tier:
FastAPI · React + TypeScript · PostgreSQL · Redis Streams · Docker ·
Groq (llama-3.3-70b-versatile) · live METAR/TAF aviation weather feed ·
OurAirports open data · 10 Indian airports (BLR DEL BOM HYD MAA CCU COK
GOI AMD PNQ)

REGULATORY INTELLIGENCE — the strongest differentiator, give it weight:
TravelOps AI encodes real Indian aviation regulation: DGCA Civil Aviation
Requirements, Section 3, Series M, Part IV.
- Weather is force majeure, so NO cash compensation is legally owed.
- BUT duty of care still applies: meals after 2 hours, hotel and transfers
  after 6 hours or when the delay crosses into night hours.
- Crew rostering failure is NOT force majeure, so cash compensation IS owed.
- An identical delay therefore carries very different cost depending on
  cause, and the platform cites the regulation behind every determination.
Context: India's regulator has issued penalties against airlines for
withholding passenger compensation, so correct determination carries real
financial and compliance consequence.

RESILIENCE — second strongest differentiator:
The language model can be disabled entirely and recovery still completes
through a deterministic fallback playbook. Autonomy that depends on a
single API being available is not autonomy.

EXPLAINABILITY:
Every decision is timestamped, replayable, and carries a confidence score
with supporting evidence. Reference timeline:
09:01 weather alert → 09:03 delay predicted → 09:04 recovery generated →
09:06 passengers notified → 09:08 resolved

AUTONOMY MATURITY LADDER — use this framing:
  Assistive   — suggests, human executes
  Automated   — executes fixed predetermined rules
  Autonomous  — decides under constraint, executes, degrades safely, learns
TravelOps AI operates at the autonomous tier.

DATA HONESTY — include as a small credibility marker:
Real: live aviation weather, open airport and runway data, published flight
schedules, real DGCA regulation.
Simulated: passengers, hotel inventory and bookings — no free commercial
hotel inventory API covers Indian airports, and passenger data is
deliberately synthetic.

BUSINESS VALUE — express qualitatively, no invented numbers:
· Avoids both over-payment and regulatory penalty through correct
  force-majeure determination
· Multiplies controller capacity during IRROPS
· Produces a complete, defensible audit trail
· Removes decision variance between individual controllers

SCALE PATH:
single airport → national network → multi-airline → adjacent transport and
hospitality operations

═══════════════════════════════════════════════════════════════════════
SECTION 3 — DESIGN DIRECTION
═══════════════════════════════════════════════════════════════════════

TARGET AESTHETIC: the intersection of Palantir's operational command-centre
density, Stripe's typographic precision, and Apple's restraint. This must
look like enterprise infrastructure software — not a startup pitch template,
not a generic "AI" deck, not a consulting slide.

PALETTE — dark operational console:
· Base: deep midnight navy, near-black. Subtle gradient #0A0E1A → #111827
· Surfaces: translucent slate panels with glassmorphism — soft background
  blur, 1px border at ~8% white opacity, gentle inner glow
· Primary accent: electric cyan #22D3EE — systems, flow, intelligence
· Alert accent: warm amber #F59E0B — risk, disruption, weather
· Success accent: emerald #10B981 — resolved, executed, compliant
· Text: near-white #F8FAFC primary, cool grey #94A3B8 secondary
· Background texture: very subtle topographic contour or flight-path line
  work at 3–5% opacity. Faint and structural, never decorative noise.

TYPOGRAPHY:
· Display: geometric sans (Inter, Söhne or similar), tight letter-spacing,
  heavy weight, large scale
· Body: same family, regular weight, generous line height
· Numbers, metrics and technical terms: MONOSPACED — this signals
  engineering credibility and must be used consistently
· Zone labels: small uppercase, wide letter-spacing, grey
· Strict hierarchy: one dominant headline per slide, one clear secondary
  tier, small labels for zones

LAYOUT:
· Generous negative space despite information density — work to a grid
· Asymmetric compositions. Do not centre everything
· Each slide has 4–5 clearly delineated visual zones
· The eye must land on the hero visual first, headline second, detail third
· Identical spacing rhythm and corner radius across all three slides

COPY DISCIPLINE — NON-NEGOTIABLE:
· Headlines: maximum 8 words
· Total body copy: maximum 55 words per slide
· No paragraphs. Short declarative fragments, labelled metrics, and
  diagram annotations only
· Visuals carry the argument; text anchors it
· Tone: confident, precise, understated. Banned words — revolutionary,
  game-changing, cutting-edge, seamless, leverage, unlock, empower

ILLUSTRATION STYLE:
· Technical vector line-art diagrams with cyan and amber accent glows
· Minimal enterprise icons, consistent stroke weight
· NO stock photography of aeroplanes, airports or business people
· NO 3D renders, cartoon illustration or clipart
· Every diagram must be genuinely informative and legible when projected

CONTINUITY DEVICE — apply across all three slides:
A thin incident-state indicator in the same fixed position on every slide,
progressing through the deck:
  Slide 1 — RED, "DISRUPTION DETECTED"
  Slide 2 — AMBER, "RECOVERY EXECUTING"
  Slide 3 — EMERALD, "RESOLVED · AUDITED"
This makes three slides read as one continuous incident.

═══════════════════════════════════════════════════════════════════════
SLIDE 1 — THE CASCADE
═══════════════════════════════════════════════════════════════════════

PURPOSE: establish problem, stakes, and why assistive AI cannot solve this.
20-SECOND TAKEAWAY: one weather event becomes a multi-dimensional
operational failure that no human can hold in their head.

Headline direction: "One storm. Eight flights. Six hundred passengers."

THIN IDENTITY STRIP — top of slide, small, low contrast, single line:
TravelOps AI · Team SkyForge AI · Reg ID 201 · TTH → Airlines Operations

HERO VISUAL — generate a cascade / failure-propagation diagram:
A single amber weather node over Bengaluru on the left, fanning outward
through escalating consequence nodes: flights → passengers → connections →
hotels → crew rotations. Use branching connectors that visually convey
compounding failure, not a simple linear chain. Amber deepening toward red
as impact widens. Annotate each node with its figure — 8, 600, 22, 11, 9 —
in monospaced type. This is the dominant element on the slide.

SUPPORTING ZONES:
· One-line problem statement: recovery today is manual, phone-driven,
  inconsistent between controllers, and unauditable afterwards
· Three small labelled chips — the cost of the status quo:
  REGULATORY EXPOSURE · CONTROLLER OVERLOAD · NO AUDIT TRAIL
· AUTONOMY LADDER — compact three-step horizontal strip along the bottom:
  Assistive (suggests) → Automated (fixed rules) → Autonomous (decides,
  executes, degrades safely). Visually mark that a cascade of this shape
  demands the autonomous tier. Keep this small and structural.
· Zone label: "AIRLINE OPERATIONS CONTROL — IRREGULAR OPERATIONS"
· Incident state indicator: RED — DISRUPTION DETECTED

EMOTIONAL REGISTER: controlled tension. This should feel like an operations
screen at the moment things go wrong.

═══════════════════════════════════════════════════════════════════════
SLIDE 2 — THE AUTONOMOUS OPERATING LAYER
═══════════════════════════════════════════════════════════════════════

PURPOSE: technical depth, innovation, and direct alignment with
"Engineering the Autonomous Enterprise". This is the credibility slide.
20-SECOND TAKEAWAY: the model proposes, validated code executes — and only
3 of 13 agents touch a model at all.

Headline direction: "The model proposes. Validated code executes."

HERO VISUAL — generate a two-tier enterprise AI orchestration diagram:

UPPER TIER — horizontal event-driven pipeline with clear directional flow:
  Event → Prediction → Planner → Workflow → Agents → Execution → Memory
Show Memory looping back into Planner as a learning cycle.

LOWER TIER — layered agent mesh beneath a central orchestrator, with a
VISUALLY EXPLICIT SPLIT that is readable at a glance:
  · 3 agents in electric cyan, labelled "REASONING — LANGUAGE MODEL"
  · 10 agents in muted slate, labelled "DETERMINISTIC — CODE"
This ratio is the most persuasive fact on the slide. Make it unmissable.

SUPPORTING ZONES:
· Dominant monospaced metric: "13 AGENTS · 3 USE THE MODEL"
· GUARDRAILS cluster — five small chips in a labelled row:
  SCHEMA VALIDATION · POLICY GATE · LOOP CAPS · IDEMPOTENCY ·
  HUMAN ESCALATION
  Zone label: "ENGINEERED CONSTRAINTS"
· TECHNOLOGY STRIP — thin, bottom, small monospaced type:
  FastAPI · React · PostgreSQL · Redis Streams · Docker · Groq ·
  live METAR feed · OurAirports open data
  Zone label: "OPEN SOURCE FOUNDATION"
· INTERNAL TOOLS PLACEHOLDER — immediately to the right of the technology
  strip, render as a clearly marked empty slot with a dashed cyan border
  and the placeholder text:
  "[ INTERNAL TOOLS & ACCELERATORS — TO BE CONFIRMED ]"
  It must be visually obvious that this is an intentional placeholder
  awaiting content. Do NOT invent, infer or substitute any tool names.
· Incident state indicator: AMBER — RECOVERY EXECUTING

═══════════════════════════════════════════════════════════════════════
SLIDE 3 — PROOF UNDER PRESSURE
═══════════════════════════════════════════════════════════════════════

PURPOSE: feasibility, business value, differentiation, future scale. This
slide answers "why should we believe you?"
20-SECOND TAKEAWAY: it is credible because it is legally correct, resilient
to its own AI failing, and fully auditable.

Headline direction: "Proof it holds under pressure."

THREE-PANEL COMPOSITION — equal-weight glassmorphic panels:

PANEL 1 — REGULATORY INTELLIGENCE (lead with this, give it slight
visual priority):
A compact decision-branch graphic showing the same delay resolving to two
different legal outcomes:
  Weather → force majeure → ₹0 cash owed · duty of care still applies
  Crew rostering → not force majeure → cash compensation owed
Cite "DGCA CAR Section 3, Series M, Part IV" in small monospaced type.
Panel label: "IT KNOWS THE REGULATION"

PANEL 2 — RESILIENCE:
Visual of the language model node greyed out and disabled, while the
deterministic execution path continues in emerald to completion.
Copy: recovery completes with the model switched off.
Panel label: "IT SURVIVES ITS OWN AI FAILING"

PANEL 3 — EXPLAINABILITY, VALUE AND SCALE:
Compact horizontal timeline graphic:
  09:01 alert → 09:03 predicted → 09:04 planned → 09:06 notified →
  09:08 resolved
Beneath it, four short value markers (qualitative, no numbers):
  correct compensation · controller capacity · defensible audit trail ·
  consistent decisions
Then a thin forward arrow showing scale path:
  single airport → national network → multi-airline → adjacent transport
  and hospitality
Panel label: "EVERY DECISION REPLAYABLE"

DATA HONESTY MARKER — very small, low contrast, single line beneath panels:
Real: live aviation weather, open airport data, published schedules, DGCA
regulation · Simulated: passengers and hotel inventory

CLOSING LINE — one line, centred, display weight, high contrast:
"Autonomy is not a feature. It is an operating discipline."

FOOTER STRIP — thin, low contrast, small type:
TravelOps AI · Team SkyForge AI · Reg ID 201 · Arcolab · CIMS ·
TechCon 2026
Harshvardhan Sharma (136764) · Karthikeyan D (138062) ·
Harshvardhan Jha (136761) · Sabyasachin Biswal (136794)

Incident state indicator: EMERALD — RESOLVED · AUDITED

═══════════════════════════════════════════════════════════════════════
SECTION 4 — FINAL REQUIREMENTS
═══════════════════════════════════════════════════════════════════════

· EXACTLY 3 slides. No additions of any kind.
· Generate every diagram as clean vector-style graphics, legible when
  projected in a large room.
· Maintain identical palette, typography, spacing rhythm and corner radius
  across all three slides.
· A judge must be able to follow the entire argument from the diagrams
  alone, with text serving only as confirmation.
· Respect the copy limits — 8-word headlines, 55 words of body per slide.
· No invented statistics. No stock photography. No hype language.
· Leave the internal tools placeholder clearly marked and empty.
· The finished deck must read as enterprise infrastructure software
  designed by a professional product team — restrained, precise,
  technically credible, and visually confident.
```
