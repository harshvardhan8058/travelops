# 15. Demo Script

Resolves backlog item #28. Target: **7 minutes**, structured so that cutting from the end still leaves
a coherent story.

The narrative goal is to make one point repeatedly: **this is an operating system that acts, not an
assistant that answers.**

## Before you start

| Check | Why |
| --- | --- |
| Frozen dataset loaded from the committed dump | No surprises from regenerated data |
| Real Gmail inbox open in a visible tab | The notification moment needs a real inbox |
| `bengaluru_storm` fixture ready to inject | Never wait for real weather |
| Backup video ready to play | Recorded Day 5, re-recorded Day 6 |
| Groq kill-switch reachable | Act 6 depends on it |
| Browser zoom raised | Judges are watching a projector, not your laptop |

Do **not** open a terminal during the demo unless the point is deliberate. Terminals read as "unfinished".

---

## Act 0 — Framing (30s)

> "When a storm hits Bengaluru, an airline operations controller starts making phone calls. Rebook
> passengers, find hotel rooms, protect connections, reassign gates, sort out crew. It's manual, it's
> inconsistent between controllers, and afterwards nobody can explain why any particular decision was
> made.
>
> TravelOps AI is not a chatbot that answers questions about this. It's an operating layer that does
> the work and shows you why."

Do not say "we used AI to..." — say what the system *does*.

## Act 1 — Live operations (45s)

Show the dashboard: 10 Indian airports, live flight board, current weather.

> "This is live weather, right now, from the Aviation Weather Center — the same METAR feed airlines
> use. Wind, visibility, ceiling, for every airport in the network."

**Why this matters:** it is real and verifiable. A judge can check it on their phone. Lead with the
part that cannot be faked.

## Act 2 — Prediction with evidence (45s)

Inject the storm. A risk score appears.

> "Conditions at Bengaluru just crossed threshold. The system predicts an 87% probability of
> significant delay — and note what's next to that number: the evidence. Wind at 24 knots, visibility
> 800 metres, and crosswind relative to runway 09L. This is a rules engine, not a language model. It's
> fast, it's reproducible, and it can be audited."

**The point being scored:** you deliberately did *not* use AI here. That demonstrates judgement, and it
pre-empts the "isn't this just a wrapper around an LLM?" question before it is asked.

## Act 3 — The cascade (90s)

The disruption propagates.

> "One weather event, but look at what it actually touches: 8 flights, 600 passengers, 22 connections
> now at risk, 11 hotels in range, 9 crew rotations affected.
>
> This is the part controllers get wrong under pressure — not any single decision, but holding the whole
> picture at once."

Then the plan appears.

> "The planner is Groq. It gets the disruption context and the retrieved precedent — and here's the
> precedent it found: a storm at Delhi in July, resolved successfully, hotels allocated first. The plan
> comes back as validated JSON, not prose. Every task is a known action type, schema-checked before
> anything executes."

Point at the retrieved precedent on screen. Memory that visibly changes a decision is far more
convincing than a claim about RAG.

## Act 4 — Execution and the compensation moment (90s)

Tasks execute in parallel.

> "Hotels reserved. Connections flagged. Gates reassigned. Passengers notified —"

Switch to the real inbox. A genuine email is sitting there.

> "— that's a real email, sent by the system, thirty seconds ago."

Then the compensation line. **This is the strongest 20 seconds of the demo.**

> "Now look at compensation: zero rupees cash. That's not a bug.
>
> Under DGCA CAR Section 3 Series M Part IV, weather is force majeure, so no cash compensation is owed.
> But the duty of care still applies — so the system reserved hotels and issued meal vouchers, because
> the delay exceeds six hours.
>
> If this same delay had been caused by crew rostering, cash *would* be owed, because regulators have
> held that rostering failures are within an airline's control. The system knows the difference, and
> cites the regulation either way."

**Why this wins:** it is real regulatory nuance that cannot be bluffed, and it demonstrates the
AI/deterministic boundary in one concrete example. It hits Relevance and Feasibility together.

## Act 5 — Replay (45s)

Scrub the timeline.

> "Every decision is timestamped and replayable. 09:01 weather alert, 09:03 delay predicted, 09:04
> recovery generated, 09:06 passengers notified, 09:08 resolved.
>
> When an operations manager asks what happened at 09:04, this is the answer. Not a log file — the
> actual decision, its inputs, and its reason."

## Act 6 — Kill the AI (45s)

The differentiating moment. Disable Groq. Re-run.

> "Last thing. I'm going to turn off the LLM entirely.
>
> Same storm. Recovery still completes — deterministic fallback playbook: notify, check connections,
> reserve hotels. Degraded, but passengers are still looked after.
>
> Most AI demos die when the model does. Autonomy that depends on a single API being up isn't autonomy."

**This is the most important 45 seconds in the demo.** It converts "they built an LLM wrapper" into
"they engineered a system." Protect it in rehearsal; it is the last thing to cut.

## Act 7 — Close (30s)

Show the executive report.

> "The incident closes with a generated executive summary — cost, passengers reaccommodated,
> connections protected — and it's stored with its outcome. The next storm at Bengaluru retrieves this
> incident as precedent.
>
> TravelOps AI. It detects, reasons, decides, executes, and learns. That's the autonomous enterprise,
> in one operational domain."

---

## Timing

| Act | Time | Cumulative |
| --- | --- | --- |
| 0 Framing | 0:30 | 0:30 |
| 1 Live ops | 0:45 | 1:15 |
| 2 Prediction | 0:45 | 2:00 |
| 3 Cascade | 1:30 | 3:30 |
| 4 Execution + compensation | 1:30 | 5:00 |
| 5 Replay | 0:45 | 5:45 |
| 6 Kill the AI | 0:45 | 6:30 |
| 7 Close | 0:30 | 7:00 |

### If you are over time

Cut in this order: Act 5 (replay) → Act 1 (shorten) → Act 3 (shorten the cascade narration).

**Never cut Act 4's compensation moment or Act 6.** Those two are where the score is.

---

## Anticipated questions

| Question | Answer |
| --- | --- |
| "Is this just a ChatGPT wrapper?" | The LLM does planning and explanation only. Prediction is a rules engine, compensation is a regulatory rules table, hotel search is SQL. Act 6 proves the system runs without it. |
| "Is the data real?" | Weather is live from the Aviation Weather Center. Airports and runways are OurAirports public-domain data. Schedules come from AIKosh. Passengers and hotels are synthetic — no free source provides hotel inventory for Indian airports, and passenger data is deliberately synthetic. |
| "Why not use a real flight API?" | No free tier is usable — AviationStack allows 100 requests a month, OpenSky gives positions rather than delay status. It is also better for a demo: reproducible and controllable. The data access is behind an interface, so a paid API drops in without touching the agents. |
| "How do you stop hallucination?" | The planner only emits action types from a known enum against entities retrieved from the database. Output is schema-validated and policy-checked before execution. It cannot invent a hotel. |
| "What if agents loop forever?" | Hard caps on recursion depth, iterations and wall-clock time, enforced by the orchestrator. |
| "How is this autonomous rather than automated?" | It decides *what* to do, not just how. The plan is generated per incident from context and precedent, and it escalates to a human when confidence is low. |
| "Would an airline actually use this?" | Not as-is — real deployment needs crew legality, GDS integration and certification. What it demonstrates is the orchestration pattern, with real regulatory rules and a real weather feed. |
| "What's the business value?" | Faster recovery, consistent decisions between controllers, and a complete audit trail. The audit trail alone matters given DGCA enforcement action against airlines for withholding compensation. |
| "Why no chatbot?" | A chatbot answers questions. Operations needs something that acts. Adding a chat box would have made it demo better and work worse. |

## Honesty rules

State plainly that passengers and hotels are synthetic, and that bookings are simulated. Being asked
and having a clean answer is far better than being caught. The synthetic data is a *forced* constraint
with a documented reason — that is a defensible position, and it maps to a real finding rather than a
shortcut.

Do not claim ML where there is a rules engine. "Rules engine, deliberately" is a stronger answer than a
vague gesture at a model.
