# Open Questions

Decisions that are **not mine to make**. Each one is currently either blocking or has been assumed —
and every assumption I made is recorded here so nothing is silently baked in.

Answer the blocking section and the rest of the design can be completed. The assumptions section only
needs your attention where I guessed wrong.

---

## Blocking — cannot proceed without these

### B1. What is the hackathon, and when?

Nothing can be sequenced without this.

- Name / organiser / theme?
- Submission deadline?
- How many days of actual working time?
- Team size, and who does what? (frontend / backend / ML / presentation)

**Why it blocks:** the 7-day execution plan (#27) and demo script (#28) are pure fiction without dates
and headcount. It also determines how much of the design is worth building at all — a 24-hour hackathon
and a two-week one are different projects.

### B2. What are the judging criteria?

If the brief states weightings — innovation, technical depth, business viability, demo quality — send
them verbatim.

**Why it blocks:** this decides where to spend effort. Judges scoring "technical depth" reward the
multi-agent orchestration and the fallback behaviour. Judges scoring "demo quality" reward the
dashboard. Building for the wrong one is the most expensive mistake available.

### B3. Ops-facing or passenger-facing?

I assumed **airline operations controller** ([`09-requirements.md`](09-requirements.md)).

The alternative — a passenger-facing app that rebooks *you* when your flight breaks — is a different
product with a different UI, different data, and a different pitch.

**Why it blocks:** it invalidates or confirms FR-24 to FR-28 and the entire UI direction.

### B4. Do you have a Groq API key yet?

And have you confirmed the free tier is active on your account?

**Why it blocks:** [`10-data-sources.md`](10-data-sources.md) puts the binding limit at roughly 100K
tokens/day — about 25–50 planning calls. If that is wrong for your account, the caching and fixture
strategy needs rethinking.

### B5. Language and stack?

Not decided anywhere. My default recommendation, for reasons rather than taste:

| Layer | Recommendation | Why |
| --- | --- | --- |
| Backend | Python + FastAPI | Groq SDK, ML tooling, and data generation all live here |
| Frontend | React + Vite | Fastest path to a live dashboard |
| Database | Postgres + pgvector | One system for records and embeddings |
| Queue / events | In-process async, or Redis | Kafka is unjustifiable at this scale |

**Why it blocks:** folder structure (#7), coding standards, and the generator scripts in
[`12-synthetic-data-plan.md`](12-synthetic-data-plan.md) all depend on it.

⚠️ Tell me if your team is stronger in Node/TypeScript — that changes the recommendation, and team
familiarity beats theoretical fit during a hackathon.

---

## Assumptions I made — correct me if wrong

| # | Assumption | Where | Impact if wrong |
| --- | --- | --- | --- |
| A1 | Primary actor is an ops controller | [09](09-requirements.md) | Large — see B3 |
| A2 | One disrupted flight at a time; no cascading | [09](09-requirements.md) | Medium — cascading is a better story but much harder state management |
| A3 | India-focused: Indian airports, ₹, DGCA rules | throughout | Medium — changes datasets and compensation rules |
| A4 | Weather is the primary disruption trigger | [09](09-requirements.md) | Low — technical/crew/ATC triggers can be added |
| A5 | Hotel budget cap is ₹6,000 | [03](03-agent-design.md) | Low — config value, taken from your transcript |
| A6 | Bookings/payments simulated, never real | [09](09-requirements.md) | Low — near-certainly correct |
| A7 | 384-dimension embeddings from a local model | [11](11-data-model.md) | Low — but needs deciding, see D2 |
| A8 | Demo runs locally, not deployed | implied | Medium — affects #21, #22 |

---

## Decisions needed, not blocking yet

### D1. Which airports?

I sized for ~40, centred on Indian metros. Bengaluru (`VOBL`) is the worked scenario and is
conveniently one of the four metros in
[DGCA's published on-time performance data](https://sansad.in/getFile/loksabhaquestions/annex/184/AS122_Qh5wnY.pdf?source=pqals).

Confirm, or give me the list you want.

### D2. Embedding model?

Groq does not serve embeddings, so this needs its own answer. Options:

- **Local sentence-transformer** (384-dim, free, no network) — my recommendation
- Hosted embedding API — costs money or adds another key
- **Skip vectors entirely** and retrieve by structured filtering on trigger type, severity and airport

The third option is worth genuinely considering. With only ~150 historical incidents, a `WHERE`
clause on trigger type and severity may retrieve better precedent than cosine similarity, and it is
explainable in a way embeddings are not. Semantic retrieval earns its place at a scale you will not
reach during a hackathon.

### D3. Real DGCA compensation figures

[`11-data-model.md`](11-data-model.md) has the `compensation_rule` table but **no real values**. These
must come from the actual DGCA Civil Aviation Requirement on denied boarding, cancellation and delay.

I can research these if you want — say the word. I left them empty rather than inventing numbers,
because fabricated compensation figures in a table with a `regulation_ref` column is exactly the kind
of plausible-looking fiction that gets a project caught out.

### D4. Demo email inboxes

[`12-synthetic-data-plan.md`](12-synthetic-data-plan.md) needs 2–3 real addresses you control for live
email during the demo. Everything else logs. Don't send them here — just confirm you have them and use
them in local config.

### D5. Deployment target

Local-only is fine and lowest-risk. If you want it hosted, free options exist but need choosing, and
free tiers cold-start slowly, which is unpleasant mid-demo.

### D6. Scope ambition

The backlog ([`08-blueprint-backlog.md`](08-blueprint-backlog.md)) contains a digital twin, a
simulation engine, and a timeline replay engine. My view: **replay is worth it** — it is nearly free
given `decision_log` already exists — while a digital twin and simulation engine are product-scale
features that will consume the whole hackathon.

Confirm you're happy deferring those, or tell me one of them *is* the demo.

---

## What you offered to provide

You said you'd supply data. Ranked by how much it would help:

1. **The hackathon problem statement / brief.** Highest value by a distance. It answers B1, B2, B3 and
   probably B6 at once, and it may reveal that the entire framing needs to shift.
2. **`TravelOps_AI_Master_Blueprint.txt` and `TravelOps_AI_Startup_Blueprint.docx`** — the two files
   from the original chat. Contents are unrecoverable from the transcript. There may be decisions in
   them I have unknowingly contradicted.
3. **Any datasets you've already found**, especially anything Indian-aviation-specific.
4. **Team skills.** Determines B5 and how much frontend to attempt.

Paste them into chat and I'll fold them in. Files can go under `docs/reference/`.

---

## Where I got to on my own

For the record, so you know what is already settled and needn't be discussed:

| Backlog item | Status |
| --- | --- |
| #12 Free APIs and datasets | ✅ Resolved — [10](10-data-sources.md) |
| #3 Database schema | ✅ Resolved — [11](11-data-model.md) |
| #11 Synthetic data plan | ✅ Resolved — [12](12-synthetic-data-plan.md) |
| Requirements spec | ✅ New — [09](09-requirements.md) |

The three findings that changed the design:

1. **No free hotel API covers Indian airports.** Amadeus' test tier carries
   [24 hotels, in London and New York](https://github.com/amadeus4dev/data-collection). Hotels must be
   synthetic — this was forced, not chosen.
2. **No usable free live flight-status feed.** AviationStack allows 100 requests *per month*; OpenSky
   returns positions rather than delay status. The flight feed must be simulated — which is better for
   demo reproducibility anyway.
3. **Weather data is excellent and free.** `aviationweather.gov` provides METAR/TAF with no API key, and
   its fields match the Prediction Agent's inputs exactly. The disruption trigger can be genuinely
   real, which is the most convincing part of the demo.
