# 13. Compensation and Policy — Source Status and Encoded Rules

> **Status: official guidance encoded; primary CAR still outstanding.**
>
> The team supplied the **Ministry of Civil Aviation Passenger Charter, February 2019**. That is a real
> Government of India publication, so its figures are citable — a genuine upgrade over the legal
> commentary this document previously relied on. It is **not** the primary Civil Aviation Requirement,
> and the charter itself says it is general guidance and points to CAR Series M, Section 3 on the DGCA
> portal.
>
> Encoded as [`policy_packs/in-moca-charter-2019/2019.02/`](../policy_packs/in-moca-charter-2019/2019.02/)
> with status `official_guidance_dated`. It can produce cited, labelled results in `POLICY_MODE=charter`.
> It can never satisfy `POLICY_MODE=verified`.

This is technical design, not legal advice. The implementation source of truth is the versioned pack,
not prose in this file.

## What the charter changed in our understanding

Three corrections. The first two matter for the demo narrative.

### 1. Delay attracts no cash compensation at all

Our earlier framing treated a weather delay as force-majeure-exempt and concluded ₹0 cash. That reached the
right number by the wrong route. In the charter, the delay provisions contain **no monetary compensation
entitlement at all**. Delay produces:

- meals and refreshments, at a threshold that depends on block time
- beyond six hours on a domestic flight, a passenger choice of alternate flight within six hours or full
  refund
- free hotel accommodation in a specific night-departure window

Cash appears only under **cancellation** and **denied boarding**. So for a weather delay, ₹0 cash follows
primarily from the absence of a delay-compensation provision, and the force majeure and
beyond-carrier-control exemptions are a *second*, independent reason. That is a stronger answer, because
it holds even if a reviewer disputes the exemption.

### 2. The hotel trigger is narrower than we wrote

We previously documented "hotel after 6 hours, or when the delay crosses nighttime". The charter ties it
to the airline having communicated the delay **more than 24 hours in advance**, plus either a delay over
24 hours, or a delay over six hours for flights **scheduled to depart between 20:00 and 03:00**.

That is materially different, and it produces an uncomfortable edge case: a passenger delayed overnight
at short notice appears to fall outside it. That case is flagged for SME review
(`weather_delay_short_notice_no_hotel`) and must not be demonstrated until reviewed.

### 3. Meals thresholds are tiered by block time

Not a flat two hours. Two hours for block time up to 2½ hours; three hours for block time over 2½ and up
to 5 hours; four hours otherwise.

## Encoded figures

All from the charter. Every one is `status: draft` pending SME sign-off.

| Situation | Entitlement |
| --- | --- |
| Delay, block time ≤ 2½ h | Meals from 2 h |
| Delay, block time > 2½ h and ≤ 5 h | Meals from 3 h |
| Delay, block time > 5 h | Meals from 4 h |
| Domestic delay > 6 h | Alternate flight within 6 h **or** full refund, passenger's choice |
| Delay > 24 h, or > 6 h for a 20:00–03:00 departure, communicated > 24 h ahead | Free hotel |
| Cancellation, notice obligation met | Alternate flight or refund; no cash |
| Cancellation, notice obligation not met, block ≤ 1 h | Lesser of ₹5,000 and basic fare + fuel charge, **plus** full refund |
| Cancellation, block > 1 h and ≤ 2 h | Lesser of ₹7,500 and basic fare + fuel charge, plus refund |
| Cancellation, block > 2 h | Lesser of ₹10,000 and basic fare + fuel charge, plus refund |
| Denied boarding, alternate within 1 h | No compensation |
| Denied boarding, alternate within 24 h | 200% of basic fare + fuel charge, cap ₹10,000 |
| Denied boarding, alternate beyond 24 h | 400%, cap ₹20,000 |
| Denied boarding, passenger declines alternate | Full ticket refund + 400%, cap ₹20,000 |
| Baggage, domestic | ₹20,000 per passenger |
| Cargo, domestic | ₹350 per kg |
| Baggage, international | 1,131 SDR per passenger |
| Cargo, international | 19 SDR per kg |
| Refund timing | Immediate (cash), 7 days (credit card), via agent (agent booking) |

Also encoded: statutory taxes/UDF/ADF/PSF refundable even on non-refundable fares; credit shell is the
passenger's option and not a default; no processing charge on refunds; free name correction within 24 h
of booking.

Out of MVP scope, recorded only: death/bodily-injury limits (₹20,00,000 domestic, 113,100 SDR
international), disability provisions, medical-emergency facilities.

## Suspected supersession — excluded from evaluation

| Item | Concern |
| --- | --- |
| No-charge cancellation window (24 h in the charter) | Secondary reporting describes a **February 2026 amendment to CAR Series M Part II** moving this to **48 hours**. Rule is `superseded_suspected` and `excluded_from_evaluation: true` |
| All Part IV entitlement figures | Secondary sources describe a later Part IV revision (reported August 2024). The charter is February 2019 |

This is exactly why the pack cannot be `verified`. We have real figures from a real government document
and an unresolved question about whether they are current.

## Cause assessment stays evidence-led

The charter's force majeure clause requires that the circumstance was beyond the airline's control **and**
could not have been avoided even if all reasonable measures had been taken. A separate clause covers
delay clearly attributable to ATC, meteorological conditions or security risks.

Neither is a lookup on `trigger_type`. Both rules declare required evidence facts, and a missing fact
produces `needs_human` rather than an automatic exemption. The test case
`cancellation_weather_without_reasonable_measures_evidence` exists to prove this.

```json
{
  "operational_cause": "meteorological",
  "clearly_attributable": true,
  "external_to_carrier": true,
  "unavoidable_despite_reasonable_measures": null,
  "decision": "needs_human",
  "missing_facts": ["cause_evidence.unavoidable_despite_reasonable_measures"]
}
```

## Facts the calculator must receive

| Family | Fields |
| --- | --- |
| Event | type, delay/expected delay minutes, notice minutes, wait minutes |
| Flight | block time, scheduled local departure time, domestic flag |
| Fare | one-way basic fare, airline fuel charge, currency, payment method |
| Passenger | checked in on time, contact info at booking, reported for original flight, opted for alternate |
| Alternate | offered, minutes after original scheduled, airport/terminal changed |
| Carrier | operating carrier, foreign-carrier flag, country |
| Cause evidence | operational cause, clearly attributable, external to carrier, unavoidable despite reasonable measures, evidence refs |

Block time and the two fare components are the ones most likely to be missing, and both block cash
computation by design.

## Policy modes

| Mode | Behaviour |
| --- | --- |
| `demo` | Fictional fixture. Proves the engine. No real figure, no citation |
| `charter` | Loads this pack. Real figures, real citation, UI badge reads *MoCA Passenger Charter · Feb 2019 · pending CAR verification*. Excluded rules stay excluded |
| `verified` | Requires the current primary CAR, archived hash, resolved supersession and SME sign-off. **Not reachable today** |

Stage 2 and Stage 3 can run in `charter` mode. That is a real improvement on the previous position, where
we could only show a fixture.

## Demo wording for charter mode

> "Compensation here is computed by a deterministic rules engine from the Ministry of Civil Aviation
> Passenger Charter, and every figure cites its source. Cash is zero for this weather delay for two
> independent reasons: the instrument provides no monetary compensation for delay at all, and the
> beyond-carrier-control exemption applies on the evidence. Duty of care still applies, so meals and
> hotel are owed. We are labelling this as the February 2019 charter rather than the current CAR,
> because we have not yet verified it against the latest revision — the engine blocks verified mode until
> we do."

Never claim these figures are the current CAR position. Never show the 24-hour cancellation rule.

## Remaining verification matrix

| Question | Status |
| --- | --- |
| Current CAR Part IV revision and effective date | Outstanding |
| Whether any encoded figure changed in the reported Aug 2024 revision | Outstanding |
| 24 h vs 48 h no-charge cancellation window | Outstanding |
| Definitions of "one-way basic fare" and "airline fuel charge" | Outstanding |
| Hotel trigger reading, including the short-notice overnight case | Outstanding — RQ-3 |
| Evidence standard for "all reasonable measures" | Outstanding — RQ-6 |
| Overlap/precedence with Montreal or foreign-carrier rules | Outstanding — RQ-8 |
| PDF redistribution permission | Outstanding |

Open questions are tracked per-rule in
[`review.yaml`](../policy_packs/in-moca-charter-2019/2019.02/review.yaml). Acquisition steps for the
primary CAR are in [`24-input-acquisition.md`](24-input-acquisition.md).

*Source content is paraphrased rather than reproduced, for licensing compliance.*
