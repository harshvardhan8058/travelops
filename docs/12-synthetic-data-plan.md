# 12. Synthetic Data Plan

Resolves backlog item #11.

Synthetic data is a deliberate prototype boundary. Under the current budget, coverage and access
constraints, no suitable live hotel inventory or passenger/PNR source has been validated. Real PII is
neither needed nor permitted. Provider interfaces make these sources replaceable later.

## What is real vs generated

| Data | Source | Why |
| --- | --- | --- |
| Airports, runways | Real — OurAirports | Public domain, and runway headings must be accurate for crosswind |
| Weather observations | Real — aviationweather.gov / Open-Meteo | The live trigger is the credible part of the demo |
| Flight schedules | Planned real source — AIKosh; **synthetic fallback** | Do not label real until file/schema/licence are archived and loader test passes |
| Flight *status* changes | Simulated | Deterministic and reproducible; live feed unavailable to the team |
| Passengers, bookings | Synthetic | Real PII is intentionally excluded |
| Hotels, inventory | Synthetic | No suitable source validated under constraints |
| Historical incidents | Synthetic fixture | Seeds retrieval; not evidence of real-world performance |
| Policy rules | Demo fixture until primary source review; then reviewed pack | Never convert secondary commentary directly into executable law |

The mix matters for the demo narrative: airports/runways and live weather may be independently
verifiable; schedules are real only after source validation; every simulated, synthetic and fixture
record is labelled. This is a stronger position than calling all non-passenger data real.

## Determinism

Every generator takes a **fixed seed**. Same seed, same dataset, byte for byte.

```
SEED = 20260807
```

This is NFR-1 (reproducibility) at the data layer. If the dataset shifts between runs, the demo shifts
between runs, and you cannot tell whether a behaviour change came from your code or your data.

Commit the generated dataset as a SQL dump or CSV set. Do not regenerate it on the demo machine.

## Volumes

The single-flight and cascade figures are parent/child fixtures:

- **Phase 1 child flight:** one disrupted flight with a deliberately small passenger subset for fast
  end-to-end testing.
- **Phase 2 incident group:** 8 flights, ~600 passengers, 22 at-risk connections, 11 candidate hotels
  and exactly 9 traceable crew pairings.

Do not reuse older 180-passenger/47-connection/3-hotel numbers as if they describe the same scenario.
They remain historical reference only. The canonical fixture targets are below.

| Entity | Count | Notes |
| --- | --- | --- |
| Airports | **10** | BLR DEL BOM HYD MAA CCU COK GOI AMD PNQ |
| Runways | ~25 | Real, from OurAirports — needed for crosswind |
| Flights | ~400 | One operating day across the ten airports |
| Passengers | ~12,000 | Enough to populate the network realistically |
| Bookings | ~10,000 | Some hold multi-segment itineraries |
| Booking segments | ~14,000 | ~30% connecting |
| Crew members | ~200 | Rosters across the network |
| Hotels | ~45 | 3–6 per airport; **11 within range of BLR** |
| Ground transport vendors | ~15 | Coach and taxi capacity per airport |
| Historical incidents | ~150 | With plans, actions and outcomes, to seed retrieval |
| Weather observations | ~30 days | Backfilled real history from Open-Meteo |

### Cascade targets

The demo scenario must produce these numbers. Generate backwards from them rather than hoping
randomness cooperates:

| Target | Value |
| --- | --- |
| Flights disrupted by the BLR storm | 8 |
| Passengers affected | ~600 |
| Connections at risk | 22 |
| Hotels in range | 11 |
| Crew rotations affected | 9 |

~150 historical incidents is the figure to get right. Fewer and retrieval returns nothing relevant;
many more and you are generating fiction at a scale that starts to look like the product.

## Generation approach

### Passengers

Use a faker library with an Indian locale so names and phone formats are plausible. Constraints:

- Emails must be **non-routable** except for a handful of real test inboxes. Use a reserved domain such
  as `@example.com`, or better, a per-passenger alias into one inbox you control.
- Phone numbers should come from a documented reserved/fictional range and are **never dialled** — SMS
  is simulated per [`10-data-sources.md`](10-data-sources.md).
- Tier distribution should be skewed, not uniform: roughly 80% standard, 13% silver, 5% gold, 2%
  platinum. A uniform split makes tier-based prioritisation look arbitrary in the demo.
- Give ~3% `has_special_needs = true`. It creates a visible reason for the planner to prioritise
  specific passengers, which is a good moment in a demo.

⚠️ Nominate 2–3 real inboxes you control for live email during the demo. Everything else logs.

### Bookings and connections

Connections are the point. Generate them deliberately rather than at random:

1. Pick ~30% of bookings to be multi-segment.
2. For each, set the second segment to depart **45–180 minutes** after the first is scheduled to arrive.
3. That connection window is what determines whether a delay breaks the itinerary.

For each Phase 1 fixture booking, generate a second segment 45–180 minutes after the scheduled arrival.
Select a small, explicit subset as at risk and assert that count in fixture validation. Phase 2's group-
level target is 22 at-risk connections. Generate backwards from the target; never rely on random chance.

### Hotels

Per major airport, generate 3–6 hotels varying along the axes the Hotel service's constraints read:

| Attribute | Distribution | Purpose |
| --- | --- | --- |
| `rate_inr` | ₹2,500 – ₹9,500 | Some must exceed the ₹6,000 cap so the constraint visibly bites |
| `is_partner` | ~40% true | Makes "partner hotels first" a meaningful preference |
| `distance_km` | 1 – 25 | Gives the agent a real trade-off against price |
| `total_rooms` | 20 – 200 | Capacity must be able to run out |

Deliberately make capacity insufficient for at least one requested allocation. A trivial all-success
recovery demonstrates nothing; a controlled shortfall exercises prioritisation and the
`needs_human`/partial-resolution path without pretending a legal entitlement was computed.

### Historical incidents

These seed the retrieval layer, so they must be varied along dimensions retrieval can discriminate on.

For each generated incident, produce a full record: trigger conditions, prediction, plan, actions with
costs, and an outcome.

- Vary trigger type: weather, technical, crew, ATC.
- Vary severity and passenger counts.
- **Vary outcomes.** Around 70% resolved successfully, 20% partially, 10% failed.

That last point is the one people get wrong. If every historical incident succeeded, `incident_outcome`
carries no signal and retrieval cannot prefer strategies that worked — the learning loop in
[`05-memory-and-rag.md`](05-memory-and-rag.md) becomes decorative.

Include at least one deliberately planted precedent that closely matches the demo scenario: a storm at
a metro airport, resolved successfully, hotels-first ordering. When the planner retrieves it live and
you can point at it on screen, the memory architecture stops being a claim.

### Weather backfill

Pull ~30 days of real history from Open-Meteo for the airport set, then attach the generated historical
incidents to genuinely bad-weather days. Incidents anchored to real storms are internally consistent —
someone checking the conditions against the delay will find they agree.

## The demo seed scenario

One scripted scenario, committed as a fixture, matching
[`02-disruption-flow.md`](02-disruption-flow.md):

```yaml
scenario: bengaluru_storm
airport: VOBL          # Bengaluru
flight: AI203
injected_conditions:
  wind_speed_kt: 24    # ~45 km/h
  visibility_m: 800
  precipitation: rain
expected:
  risk_index_min: 75
  risk_level: high
  incident_group:
    flights_affected: 8
    passengers_affected_approx: 600
    at_risk_connections: 22
    candidate_hotels: 11
    crew_pairings_affected: 9
  hotel_capacity_shortfall: true
```

Note that the transcript's "wind 45" is km/h while METAR reports knots — 45 km/h is roughly 24 kt. Unit
confusion between the two is an easy and embarrassing bug; store knots in
`weather_observation.wind_speed_kt` and convert only for display.

`hotel_capacity_shortfall: true` is intentional, per the hotels section above.

## Deliverables

| Script | Output |
| --- | --- |
| `load_reference.py` | Airports and runways from OurAirports CSV |
| `load_schedules.py` | Flights from the AIKosh dataset |
| `backfill_weather.py` | ~30 days of Open-Meteo history |
| `generate_passengers.py` | Passengers, bookings, segments |
| `generate_hotels.py` | Hotels and capacity |
| `generate_history.py` | Historical incidents with plans, actions, outcomes |
| `seed_demo_scenario.py` | The `bengaluru_storm` fixture |
| `dump_dataset.sh` | Committed SQL dump — the artefact the demo actually loads |

Loaders (real data) are separated from generators (synthetic) on purpose: real sources can be refreshed
independently, and the boundary between real and fabricated stays legible in the repo.

Python is settled for loaders and generators because the backend stack is Python/FastAPI. Keep these
scripts deterministic, typed, and separate from migrations.

## Rules

1. **Never** put real personal data in the repository, including your own or teammates'.
2. Synthetic records must be visibly synthetic on inspection — `PAX-00001`, `example.com`.
3. Commit the generated dataset; never regenerate during a demo.
4. Real and synthetic data must be separable by table, so any claim about what is real stays checkable.
5. State plainly in the presentation which data is synthetic. Being asked and having a clean answer is
   far better than being caught.
