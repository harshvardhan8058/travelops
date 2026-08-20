# 22. Crew Pairing Model

Why 8 delayed flights disrupt 9 crew rotations.

## The question

Mentor review, on slide 1:

> Why 9 (and not 8) crew rotations disrupted? If 8 flights are delayed, what makes the crew number
> higher?

Fair challenge. A number that looks arbitrary invites doubt about everything next to it.

## The answer

**Crew are not assigned to flights. They are assigned to pairings** — multi-day sequences of duties that
begin and end at a home base. One flight typically carries several crew on different pairings, and one
pairing spans several flights. The relationship is many-to-many, so the count of affected pairings has no
reason to equal the count of affected flights.

Three mechanisms push the pairing count above the flight count:

**1. One flight, multiple pairings.** Cockpit and cabin crew frequently sit on different pairings. A
single delayed flight can therefore touch two or more rotations at once.

**2. Downstream duties.** A crew member arriving late misses the next leg of their own pairing. That next
leg may be an *undelayed* flight, which then becomes newly at risk. The disruption spreads through the
roster, not the timetable.

**3. Positioning and deadheading.** Crew travel as passengers to reposition. A delay on flight A can
strand a crew member who was deadheading to operate flight B, so B's pairing is disrupted even though B
was on time and had no crew aboard A.

Add duty-time limits — a delay can push a rotation past its legal limit and force a replacement — and the
asymmetry compounds. In real operations the pairing count is usually *higher* than the flight count, not
equal to it.

## What we will and will not claim

The `9` is only defensible if our data makes each pairing traceable. So:

- The synthetic roster in [`12-synthetic-data-plan.md`](12-synthetic-data-plan.md) models pairings
  explicitly, with legs, base, and crew-to-pairing links — not a flat crew-to-flight column.
- The cascade view (Phase 2) renders the pairing graph so a judge can **count the nine themselves**.
  Each edge is labelled with its mechanism: onward duty, second pairing on the same flight, or
  positioning.
- If the seeded scenario does not produce exactly nine traceable pairings, the UI shows whatever number
  it does produce. We never hardcode a headline figure to match a slide.
- Where a precise count is not traceable, the language is **"multiple crew rotations at risk"** rather
  than a specific integer.

The submitted deck says 9 and cannot be changed. The build's job is to make that number *earned* — which
is a better outcome than defending it verbally.

## Scope boundary, unchanged

Crew handling is **coordination and display only**. We surface which pairings are affected and why. We do
**not** validate duty-time legality or generate legal replacement rosters — that is a hard regulated
domain requiring certified logic, and claiming it would be the kind of overreach this document exists to
avoid. See `docs/DECISIONS.md`.

## Data model implications

```
crew ──< crew_pairing_assignment >── pairing ──< pairing_leg >── flight
```

- A `pairing` has an id, base, start and end, and ordered legs.
- A `pairing_leg` links a pairing to a flight, with a role: `operating` or `positioning`.
- Impact query: given delayed flights, find pairings with an affected leg, then walk forward to
  subsequent legs whose connection time is now infeasible.

That forward walk is the whole trick, and it is a recursive SQL query over `pairing_leg` — no graph
database required. Schema detail belongs in [`11-data-model.md`](11-data-model.md); this document owns the
reasoning.
