# 12. Synthetic Data Plan

Resolves backlog item #11.

Synthetic data is **not** a shortcut here. [`10-data-sources.md`](10-data-sources.md) established that
no free source provides hotel inventory near Indian airports, and no source will ever provide real
passenger records. Generation is the only route.

## What is real vs generated

| Data | Source | Why |
| --- | --- | --- |
| Airports, runways | Real — OurAirports | Public domain, and runway headings must be accurate for crosswind |
| Weather observations | Real — aviationweather.gov / Open-Meteo | The live trigger is the credible part of the demo |
| Flight schedules | Real — AIKosh | Authentic flight numbers, routes and timings |
| Flight *status* changes | Generated — simulator | No usable free live feed; also needed for reproducibility |
| Passengers, bookings | Generated | Real PII neither available nor desirable (NFR-8) |
| Hotels, inventory | Generated | No free source covers Indian airports |
| Historical incidents | Generated | Needed to seed retrieval, or the planner has no memory on day one |
| Compensation rules | Hand-authored from DGCA | Must be real to be defensible |

The mix matters for the demo narrative: everything a judge can independently verify — airports,
weather, flight numbers — is real. Everything synthetic is data that could not be real for legal or
commercial reasons, which is a defensible line to hold when asked.

## Determinism

Every generator takes a **fixed seed**. Same seed, same dataset, byte for byte.

```
SEED = 20260807
```

This is NFR-1 (reproducibility) at the data layer. If the dataset shifts between runs, the demo shifts
between runs, and you cannot tell whether a behaviour change came from your code or your data.

Commit the generated dataset as a SQL dump or CSV set. Do not regenerate it on the demo machine.

## Volumes

Sized for the worked scenario — 180 passengers on the disrupted flight, 47 at-risk connections, 3
nearby hotels — with enough surrounding traffic that the dashboard looks like an operation rather than
a single row.

| Entity | Count | Notes |
| --- | --- | --- |
| Airports | ~40 | Indian airports + a few international destinations |
| Runways | ~90 | Real, from OurAirports |
| Flights | ~600 | One operating day across the airport set |
| Passengers | ~15,000 | Enough to populate ~600 flights realistically |
| Bookings | ~13,000 | Some passengers hold multi-segment itineraries |
| Booking segments | ~18,000 | ~30% connecting, which produces the at-risk connections |
| Hotels | ~120 | 3–6 per major airport |
| Historical incidents | ~150 | With plans, actions and outcomes, to seed retrieval |
| Weather observations | ~30 days | Backfilled real history from Open-Meteo |

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

For the seeded demo flight, tune the distribution so that a two-hour delay puts roughly 47 connections
at risk — matching the scenario in [`02-disruption-flow.md`](02-disruption-flow.md). Work backwards
from the target number; do not hope randomness produces it.

### Hotels

Per major airport, generate 3–6 hotels varying along the axes the Hotel Agent's constraints actually
read:

| Attribute | Distribution | Purpose |
| --- | --- | --- |
| `rate_inr` | ₹2,500 – ₹9,500 | Some must exceed the ₹6,000 cap so the constraint visibly bites |
| `is_partner` | ~40% true | Makes "partner hotels first" a meaningful preference |
| `distance_km` | 1 – 25 | Gives the agent a real trade-off against price |
| `total_rooms` | 20 – 200 | Capacity must be able to run out |

Deliberately make total capacity near the disrupted airport **insufficient** for all 180 passengers.
A recovery where everything succeeds trivially demonstrates nothing. Partial success forces
prioritisation, which is where the system looks intelligent — and it exercises the `needs_human`
path from [`03-agent-design.md`](03-agent-design.md).

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
  delay_probability: ">= 0.85"
  passengers: 180
  at_risk_connections: 47
  hotels_available_nearby: 3
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

⚠️ **Language and stack for these scripts is not decided.** Written as `.py` above only for
concreteness — see [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

## Rules

1. **Never** put real personal data in the repository, including your own or teammates'.
2. Synthetic records must be visibly synthetic on inspection — `PAX-00001`, `example.com`.
3. Commit the generated dataset; never regenerate during a demo.
4. Real and synthetic data must be separable by table, so any claim about what is real stays checkable.
5. State plainly in the presentation which data is synthetic. Being asked and having a clean answer is
   far better than being caught.
