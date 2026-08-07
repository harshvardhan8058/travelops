# 10. Data Sources — Evaluated

This resolves backlog item #12. Every option below was checked against one question: **can it run a
demo on a ₹0–₹500 budget without a credit card?**

> Free tiers change often. Figures were verified in August 2026 — re-check before relying on any of
> them. Where a limit came from a third-party summary rather than the vendor, that is noted.
>
> *Content from external sources was rephrased for compliance with licensing restrictions.*

## Verdict summary

| Need | Use this | Why |
| --- | --- | --- |
| Airport weather (ops-grade) | **aviationweather.gov Data API** | Free, no key, METAR/TAF, gives exactly the fields the Prediction Agent needs |
| General/forecast weather | **Open-Meteo** | Free, no key, ~10k calls/day, global |
| Airport & runway reference | **OurAirports CSV** | Public domain, nightly dumps, no key |
| Flight schedules (India) | **AIKosh flight schedule dataset** | Government open data, India-specific |
| Delay model training | **BTS On-Time Performance** + **DGCA OTP** | BTS for volume and labelled causes; DGCA for Indian realism |
| Live flight status | **Simulate it** | No free tier is usable — see below |
| Hotel inventory | **Synthetic** | No free source covers Indian airports — see below |
| Passenger records | **Synthetic** | Real PII is neither available nor desirable |
| Email (development) | **Mailtrap** | Captures mail without delivering — safe with 600 synthetic passengers |
| Email (demo only) | **Real Gmail** | 2–3 inboxes you control |
| SMS notifications | **Simulate + log** | Twilio trial expires in 30 days |
| Reasoning LLM | **Groq** | Free tier genuinely usable; limits below |
| Vector storage | **None for MVP** | SQL retrieval instead — see D2 in [`DECISIONS.md`](DECISIONS.md) |

---

## Weather

### aviationweather.gov Data API — recommended primary

The US Aviation Weather Center publishes a [machine-to-machine Data API](https://aviationweather.gov/data/api/)
for aviation weather. It requires no API key.

Crucially, a METAR report carries [wind, visibility, runway visual range, present weather, sky
condition, temperature, dew point and altimeter setting](https://aviationweather.gov/help/data/).

Compare that against the Prediction Agent's input in [`02-disruption-flow.md`](02-disruption-flow.md):

```json
{ "airport": "BLR", "wind": 45, "visibility": 800 }
```

The fields line up exactly. This is the correct source for the prediction feature vector, and TAFs
additionally give a forecast, which is what makes *pre-emptive* disruption detection possible rather
than merely reactive.

Coverage is global for airports filing METARs, which includes Indian airports (`VOBL` for Bengaluru —
note ICAO, not IATA `BLR`).

### Open-Meteo — recommended secondary

[Open-Meteo](https://open-meteo.com/) needs no API key and aggregates models from many national
weather services. Its [terms](https://open-meteo.com/en/terms) put non-commercial free use at under
10,000 calls per day, 5,000 per hour and 600 per minute, under CC-BY 4.0.

Two caveats that matter:

- **Non-commercial only.** Fine for a hackathon; a blocker if this ever becomes a product. Attribution
  is required under CC-BY 4.0.
- It also offers [historical data back to 1940](https://open-meteo.com/), which is what lets you build
  a training set pairing past weather against past delays.

**Use Open-Meteo for forecast/historical modelling, aviationweather.gov for current airport
conditions.** The two are complementary rather than redundant.

---

## Flight data

This is where the free-tier story breaks down, and the finding changes the architecture.

| Option | Free allowance | Verdict |
| --- | --- | --- |
| [AviationStack](https://aviationstack.com/) | 100 requests **per month** | ✗ Unusable. One demo run would exhaust it |
| [AeroDataBox](https://aerodatabox.com/pricing) | Unit-based; some endpoints cost 0 units | 🟡 Cheapest real option, but polling burns units |
| [OpenSky Network](https://freeapihub.com/apis/opensky-network-api) | Free for non-commercial/research | 🟡 Gives aircraft positions, **not** delay status |
| [Amadeus Self-Service](https://developers.amadeus.com/self-service/apis-docs/guides/developer-guides/quick-start/) | Fixed monthly test quota | 🟡 Test data only; also see the shutdown note below |

Three things worth knowing:

- **OpenSky returns the wrong shape of data.** It provides live state vectors — position, altitude,
  velocity, heading, callsign. That is a tracking feed, not an operational status feed. It cannot tell
  you a flight is delayed two hours, which is the event this whole system is built around.
- **AeroDataBox has an unusual free path:** you can [contribute ADS-B data from your own receiver to
  earn API credits](https://aerodatabox.com/contribute). Interesting long-term, impractical for a
  hackathon.
- **Amadeus Self-Service is being retired.** A [2026 flight-API review](https://thunderbit.com/blog/best-flight-api-with-free-tiers)
  flags the shutdown as a migration concern. Do not build on it.

### Consequence: simulate the flight feed

Do not put a live flight API on the critical path of the demo. Instead:

1. Load **real schedules** from AIKosh (below) so flight numbers, routes and timings are authentic.
2. Drive status changes from a **local simulator** you control.

This is not a compromise — it is strictly better for a demo. You can trigger a Bengaluru storm on
command, re-run it identically for the judges, and you are not at the mercy of a rate limit or an
outage mid-presentation. It directly serves the reproducibility requirement in
[`07-risks-and-mitigations.md`](07-risks-and-mitigations.md).

Keep the flight data access behind an interface so a real API can be dropped in later without
touching the agents.

---

## Reference and historical datasets

### OurAirports — airport reference data

[OurAirports open data](https://ourairports.com/data) publishes nightly CSV dumps of airports,
runways, navaids, frequencies, countries and regions. The dataset is **public domain**, mirrored on
[GitHub](https://www.github.com/davidmegginson/ourairports-data), and totals roughly 178k rows across
six files at about 20 MB.

Runway data matters more than it first appears: crosswind limits are runway-orientation dependent, so
a genuinely credible delay-risk rule needs runway headings, not just wind speed.

### AIKosh — Indian flight schedules

India's national AI datasets portal publishes a
[flight schedule dataset](https://aikosh.indiaai.gov.in/home/datasets/details/flight_schedule.html)
covering domestic and international flights from Indian airports: flight numbers, airlines, origin and
destination, scheduled times, and days of operation.

This is the best available match for the project's setting, since the scenario is Indian airports and
₹ costs. There is also an
[aviation grievance dataset](https://aikosh.indiaai.gov.in/home/datasets/details/aviation_grievance_as_on_date.html),
which is a useful proxy for what passengers actually complain about — worth mining when designing
notification content.

### BTS On-Time Performance — delay model training

The US [Bureau of Transportation Statistics delay-cause data](https://www.transtats.bts.gov/ot_delay/ot_delaycause1.asp)
defines a flight as delayed at 15 minutes or more past schedule, and prorates delay minutes across
causes when a flight has several.

That 15-minute threshold and the cause taxonomy are worth adopting directly — they are the industry
convention, and reusing them means your numbers are defensible.

**Caveat to state openly:** BTS is US domestic. Training on it and applying it to Indian airports is a
transfer-learning assumption. Weather-to-delay physics generalises reasonably; airline-specific and
airport-congestion behaviour does not.

### DGCA — Indian on-time performance

DGCA collects monthly airline on-time performance from four metro airports — Delhi, Mumbai, Bangalore
and Hyderabad — as recorded in
[a 2025 Lok Sabha reply on airline OTP](https://sansad.in/getFile/loksabhaquestions/annex/184/AS122_Qh5wnY.pdf?source=pqals).

Bengaluru being one of the four is convenient given the worked scenario. This is monthly aggregate
data, so it cannot train a per-flight model — but it is exactly right for **calibration**: if your
model claims BLR delays at a rate wildly different from DGCA's published figure, your model is wrong.

---

## Hotels — no free source exists

This was the most consequential finding.

Amadeus is the obvious candidate, but its test environment carries
[24 hotels: 10 in London and 14 in New York](https://github.com/amadeus4dev/data-collection).

Zero coverage of Indian airports. There is no free hotel availability API that can answer "3 hotels
near BLR under ₹6000" — that data sits behind commercial GDS agreements.

**Therefore the hotel dataset must be synthetic**, and the Hotel Agent's "booking" is a simulated
write against your own database. Note that [`06-ai-vs-deterministic.md`](06-ai-vs-deterministic.md)
already marks "Book hotel (simulated)" as code — so this was always the intent. It is now confirmed as
the only option rather than a shortcut.

See [`12-synthetic-data-plan.md`](12-synthetic-data-plan.md).

---

## Notifications

| Provider | Free allowance | Verdict |
| --- | --- | --- |
| [Brevo](https://www.brevo.com/features/email-api/) | 300 emails/day, no card, no expiry | ✓ Use for email |
| [Twilio trial](https://www.twilio.com/docs/usage/trials) | ~100 SMS, ~3,000 emails, ~75 voice min; expires ~30 days | 🟡 One live demo only |
| SendGrid | Free tier discontinued | ✗ |

Brevo's free plan is [permanent and needs no credit card](https://help.brevo.com/hc/en-us/articles/208589409-About-Brevo-s-pricing-plans),
which is the property that matters. Note that SendGrid's free tier is
[reported as discontinued](https://dreamlit.ai/blog/best-sendgrid-alternatives) — a common stale
recommendation to avoid.

**Recommendation:** implement a `NotificationChannel` interface with a `ConsoleChannel` that logs, plus
a real Brevo email channel. Send genuine email to two or three of your own addresses during the demo,
and log the other 178. Nobody needs 180 real SMS to believe the system works, and the dashboard
showing 180 dispatched records is more legible to a judge than a phone buzzing.

---

## Groq — LLM limits

Groq's free tier requires no credit card. Third-party trackers report `llama-3.3-70b-versatile` at
roughly [30 requests/min, 1,000 requests/day, 12K tokens/min and 100K tokens/day](http://14678177.hamonim.com/),
with the free tier broadly described as [usable for prototyping at 30 RPM](https://www.eesel.ai/blog/groq-pricing).
Always confirm against the [official rate limit docs](https://console.groq.com/docs/rate-limits).

**The binding constraint is ~100K tokens/day, not the request count.** A planner call carrying
retrieved context might run 2–4K tokens. That is roughly 25–50 planning calls per day — plenty for a
demo, and *very* easy to burn through in an afternoon of debugging.

Practical implications:

- Cache aggressively during development. Re-running the same scenario should not re-plan.
- Keep a recorded-response fixture mode so you can develop the UI without touching Groq.
- Retrieval is a token-budget necessity, not just an accuracy one.

---

## Storage

**Decided: plain Postgres, no vector store for the MVP.** Precedent retrieval uses structured SQL
filtering on airport, trigger type, severity, weather and flight type.

At ~150 historical incidents, a `WHERE` clause retrieves better precedent than cosine similarity, and
it is explainable in a way embeddings are not — which matters when a judge asks why a particular past
incident was surfaced. Retrieval-augmented generation does not require embeddings; injecting
SQL-retrieved precedent into the planner prompt is still RAG.

Chroma plus BGE Small remain in the stack as a **stretch goal**, not a dependency. Should they be added
later, the earlier recommendation of `pgvector` is still reasonable if you prefer one system over two —
but [2026 comparisons](https://dupple.com/learn/best-vector-databases) note either is defensible at
this scale, and the decision has been made to defer both.

---

## What this means for the architecture

The research does not change the design in [`01-architecture.md`](01-architecture.md), but it does fix
three things that were open:

1. **Weather is real; flights are simulated.** The disruption *trigger* can be genuine live data, which
   is the part that impresses. The flight state machine is yours.
2. **Hotels and passengers are entirely synthetic.** Not a shortcut — the only option.
3. **The token budget is the real cost ceiling.** ~100K tokens/day shapes how much context the planner
   can be given, which makes the retrieval layer load-bearing from day one rather than a later
   optimisation.
