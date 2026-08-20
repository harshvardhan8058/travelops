# 15. Seven-Minute Demo Script

This script is the semi-final/final target. Stage 2 uses Acts 0–3 only. Never demonstrate a feature that
has not passed the relevant gate in [`25-evaluation-readiness.md`](25-evaluation-readiness.md).

## Pre-flight checklist

- Fixed dataset loaded; `make demo-reset` tested.
- `bengaluru_storm` fixture ready; never wait for actual bad weather.
- Browser at 1920×1080, projector-readable zoom, no terminal needed.
- `LLM_MODE` control visible: `LIVE`, `FIXTURE`, `OFF`.
- Real-email mode sends only to allowlisted team inboxes; bulk records simulated.
- Policy UI displays `VERIFIED` only if source hash/review validation passes; otherwise `DEMO FIXTURE`.
- Backup recording is local and tested without network.

## Act 0 — Frame the problem (30 seconds)

> “A major disruption is not one delayed flight. It is a coordination problem across passengers,
> connections, hotels, crew pairings, gates and communications. TravelOps AI is not a chatbot. It is a
> bounded operating layer that coordinates the recovery and shows the evidence behind every action.”

Do not present “manual, phone-driven and unauditable” as research unless an airline SME has validated it.
Say “fragmented coordination under time pressure” instead.

## Act 1 — Operations Room and data honesty (45 seconds)

Show the graphite Operations Room UI and provenance legend.

> “This panel uses a public aviation-weather METAR source when available. Airports and runways come from
> an archived OurAirports snapshot. Flight status, passengers, hotels, transport and crew are
> deterministic demo data, labelled on every panel. If the network disappears, the same provider
> contract switches to a committed fixture.”

If an inspected AIKosh file is present, name it and show its source record. Otherwise call schedules
synthetic—never “real schedules planned from AIKosh.”

## Act 2 — Deterministic risk with evidence (45 seconds)

Inject the fixture. Show one event, not repeated poll events.

> “Bengaluru crossed our configured high-risk threshold. This is a risk index, not an uncalibrated 87%
> probability. The contributing factors are explicit: visibility, crosswind relative to runway 09L,
> and precipitation. A deterministic rule version and source timestamp sit beside the result.”

The deliberate non-use of an LLM here demonstrates engineering judgement.

## Act 3 — Cascade and plan (90 seconds)

**Stage 2 branch:** show the cascade data and deterministic fallback task list. Say: *“Reasoning agents
arrive in Stage 3; today the same typed workflow runs from a deterministic playbook.”* Do not narrate a
Groq Planner or matched precedent before those gates pass.

**Stage 3 and later:** use the full narration below.

> “One airport event now touches eight traceable flights, about six hundred synthetic passengers,
> twenty-two connections and nine crew pairings. The ninth pairing is not a typo: crew map many-to-many
> to flights, and onward/positioning duties propagate the impact.”

Open the cascade view and let the reviewer count the pairing nodes.

> “The Planner receives typed incident context and an explainably matched precedent. It can propose only
> known actions and entity IDs. This is one orchestrator, three reasoning agents and ten deterministic
> services—the submitted slide counted tools as agents; we corrected the terminology after review.”

## Act 4 — Assurance and execution (90 seconds)

Open the Assurance Gate panel before actions run.

> “The model does not authorise this. Six code-level checks do: evidence completeness, source freshness,
> entity validity, policy, conflicts and action risk. This reservation is medium risk and passes. This
> bulk external action is high risk, so it stops for operator approval even when the model sounds
> certain.”

Approve one action. Show simulated hotel/connection/resource records, then the controlled inbox.

> “One allowlisted email was really sent; the remaining bulk messages are simulated records. Every row
> says which.”

### Regulatory branch

**If the India pack is verified:**

> “This entitlement was calculated by a deterministic versioned policy pack. Here are the exact input
> facts, pack hash, rule and source clause. Retrieval explains the clause; no model calculated or
> authorised the result.”

**If it is not verified:**

> “The policy engine is running in a clearly labelled demo-fixture mode. The architecture is complete,
> but we do not present secondary-source figures as law. Verified mode stays blocked until the current
> DGCA source and SME review are attached.”

Never assert that any weather event automatically equals force majeure, never show a rupee figure as
legally owed from an unreviewed pack, and never repeat the frozen slide's unverified detail as fact.

## Act 5 — Replay and explanation (45 seconds)

> “Every proposal, failed check, approval and side effect is timestamped and replayable. This is not
> model-generated history: the Explainer reads immutable records and cited evidence.”

Open one timeline item to show actor, evidence, assurance config version and result.

## Act 6 — Turn the AI off (45 seconds)

Switch `LLM_MODE` to `OFF`, reset and rerun.

> “The reasoning provider is now off. The deterministic fallback still detects, checks connections,
> allocates simulated resources and prepares notifications. The plan is less adaptive, but passenger
> recovery does not stop. An autonomous system that dies with one inference API is not autonomous.”

This is the strongest technical proof. Do not cut it.

## Act 7 — Close (30 seconds)

> “TravelOps AI separates judgment from control: models propose and explain; typed workflows, reviewed
> policy and deterministic assurance decide what may execute. It is an operating layer, not an
> assistant—and every claim you saw is traceable to code, evidence or a labelled fixture.”

## Timing

| Act | Time | Cumulative |
| --- | ---: | ---: |
| 0 Frame | 0:30 | 0:30 |
| 1 Operations + provenance | 0:45 | 1:15 |
| 2 Risk | 0:45 | 2:00 |
| 3 Cascade + plan | 1:30 | 3:30 |
| 4 Assurance + execution | 1:30 | 5:00 |
| 5 Replay | 0:45 | 5:45 |
| 6 AI off | 0:45 | 6:30 |
| 7 Close | 0:30 | 7:00 |

If over time, shorten Acts 1 and 5. Never cut provenance, assurance or Act 6.

## Q&A

| Question | Answer |
| --- | --- |
| Is this just an LLM wrapper? | No. Three reasoning agents propose/explain. Ten deterministic services execute behind one orchestrator, and every action is gated in code. It runs with the LLM off. |
| Why did the slide say 13 agents? | The slide counted tools as agents. Mentor review was right; the precise architecture is 1 orchestrator + 3 reasoning agents + 10 deterministic services. The submitted slide is frozen, but the build and docs use the corrected taxonomy. |
| Why 9 rotations for 8 flights? | Crew operate multi-leg pairings; cockpit/cabin and positioning duties create many-to-many links. The UI renders the exact nine records and edges. |
| Can we trust the confidence score? | We do not use one. Execution depends on six verifiable checks. Model self-report, if emitted, is audit metadata only. |
| How does DGCA scale globally? | Trip context selects reviewed versioned packs; a generic deterministic engine evaluates them; retrieval cites clauses. New concepts may require reviewed DSL extensions, but rules already expressible in the DSL are data changes. |
| Is the data real? | The UI answers per panel: real public weather and airport snapshots when available; inspected schedules only if archived; synthetic passengers/hotels/crew; simulated flight state and bulk actions. |
| Are regulatory amounts legally verified? | Only when verified mode shows an approved source-hashed pack. Otherwise no—we show engine mechanics in labelled demo mode and block authoritative claims. |
| Why no real flight/hotel API? | No suitable feed has been validated under our budget, coverage and access constraints. Provider interfaces make later replacement local; deterministic simulators make evaluation reliable. |
| What stops hallucinations? | Action enums, entity validation, policy checks, conflict checks, evidence references and high-risk human approval. Unknowns fail closed. |
| How is this autonomous? | It constructs a context-specific plan and coordinates permitted actions, but autonomy is bounded by explicit deterministic controls and human approval for risk. |
| Would an airline deploy this now? | No. Production needs airline-system integrations, IAM, certified crew-legality logic, legal/compliance approval, SLOs and operational change management. This prototype proves the orchestration pattern. |

## Honesty rules

- Do not call fixture or simulator output live.
- Do not call a risk index a probability without calibration.
- Do not call a draft policy pack law.
- Do not claim measured business savings until prototype runs produce the measurement—and label those as
  prototype results, not airline outcomes.
- Do not say the AWC source is “the same feed airlines use” without evidence; say “public aviation-
  weather METAR source.”
- Never expose personal email addresses, API keys or real passenger data on screen.
