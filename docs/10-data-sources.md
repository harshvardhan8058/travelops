# 10. Data Sources — Evaluated

This resolves backlog item #12. Every option below was checked against one question: **can it run a
demo on a ₹0–₹500 budget without a credit card?**

> **Status:** discovery research, not a validated integration ledger. Provider plans, quotas, licences and
> schemas change. Before each evaluation, record `checked_at`, official URL, observed schema, licence,
> authentication and a successful fixture/live contract test in the source ledger. AIKosh and account-
> specific Groq limits remain unvalidated.
>
> *External source content is summarized and rephrased for licensing compliance.*

## Verdict summary

| Need | Use this | Why |
| --- | --- | --- |
| Airport weather | **Aviation Weather Center Data API** | Public machine-to-machine METAR/TAF; live contract still needs testing |
| General/forecast weather | **Open-Meteo** | No-key option; attribution and current usage terms must be recorded |
| Airport & runway reference | **OurAirports CSV** | Public-domain CSV; archive exact snapshot/hash |
| Flight schedules (India) | **AIKosh candidate dataset** | Page identified; file/schema/licence must be downloaded and inspected before calling it real |
| Delay-risk research | **BTS On-Time Performance** + **DGCA OTP** | Optional research only; MVP remains deterministic and uncalibrated |
| Live flight status | **Simulate it** | No suitable feed has been validated under current budget/coverage constraints |
| Hotel inventory | **Synthetic** | No suitable Indian-airport inventory source has been identified under constraints |
| Passenger records | **Synthetic** | Real PII is neither needed nor permitted |
| Email (development) | **Mailtrap or console provider** | Safe capture; requires team account if Mailtrap is used |
| Email (demo only) | **Allowlisted Gmail/SMTP** | 2–3 controlled inboxes; credentials outside Git |
| SMS/push/bulk email | **Simulate + log** | No external bulk send during judging |
| Reasoning LLM | **Groq** | Confirm model and account quotas in the team's console |
| Vector storage | **None for MVP** | SQL retrieval; Chroma remains optional |

---

## Weather

### aviationweather.gov Data API — recommended primary

The US Aviation Weather Center publishes a [machine-to-machine Data API](https://aviationweather.gov/data/api/)
for aviation weather. It requires no API key.

Crucially, a METAR report carries [wind, visibility, runway visual range, present weather, sky
condition, temperature, dew point and altimeter setting](https://aviationweather.gov/help/data/).

Compare that against the Delay Risk service's input in [`02-disruption-flow.md`](02-disruption-flow.md):

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

1. Prefer an inspected AIKosh schedule file if the team can download it and document its schema/licence.
   Otherwise use transparently synthetic published-style schedules.
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

The AIKosh catalogue page describes a flight-schedule dataset with airline, route, scheduled-time and
days-of-operation fields. **The repository does not yet contain or validate the downloadable file,
schema or licence.** Treat it as a planned source, not real data, until the team downloads the artifact,
records its terms and a loader contract test passes. Acquisition steps are in
[`24-input-acquisition.md`](24-input-acquisition.md).

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

## Hotels — synthetic for this prototype

No suitable no-cost hotel-availability source with Indian-airport coverage, acceptable terms and a
stable demo contract has been validated. Commercial GDS/provider inventory is outside the current
budget and access.

**Therefore this prototype uses synthetic hotel inventory** and simulated reservations behind a
provider interface. This is a time-bounded design decision, not a universal claim that no free source
can exist. Reassess for a production roadmap.

See [`12-synthetic-data-plan.md`](12-synthetic-data-plan.md).

---

## Notifications

The canonical choice remains a provider interface with `console`, `mailtrap` and `gmail` modes. Use
console/Mailtrap during development. During the demo, send only to 2–3 allowlisted team-controlled
inboxes and create simulated delivery records for the rest. Provider credentials and recipients stay in
local secret configuration.

Other vendors may be evaluated later, but no notification provider is required on the deterministic
critical path.

---

## Groq — LLM limits

Groq exposes rate limits per account/model in its official console and documents supported models.
Do not encode a third-party quota estimate as a requirement. The team must record the current limits
shown for its account, configure a lower local budget, and keep fixture/off modes mandatory regardless.

Practical implications:

- Cache repeated scenario planning during development.
- Develop the UI and workflow against recorded, schema-valid responses.
- Treat live inference as a swappable provider and a demo enhancement—not the only recovery path.

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

1. **Weather may be live; flights are simulated.** A live public weather observation is a credibility
   anchor when available; the committed fixture guarantees repeatability.
2. **Hotels and passengers are entirely synthetic for this prototype.** This is the safest viable path
   under current access, PII and coverage constraints.
3. **Provider quotas are variable.** Account-specific Groq limits make fixture/offline development and
   bounded context requirements regardless of today's exact allowance.
