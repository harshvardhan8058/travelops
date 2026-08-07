# 13. Compensation and Policy Rules (DGCA)

Resolves open question **D3**. These are the real regulatory rules, researched rather than invented.

> **Legal basis:** DGCA Civil Aviation Requirements, **Section 3, Series M, Part IV** — *Facilities to
> be provided to passengers by airlines due to denied boarding, cancellation of flights and delays in
> flights*. Issued under the Aircraft Act, 1934.
>
> Confirmed as [legally binding and enforceable against airlines](https://www.ibanet.org/passenger-rights-indigo-flight-disruptions)
> by the International Bar Association, and referenced by name in a
> [2026 government statement to Parliament](https://www.ndtv.com/india-news/no-plan-for-automatic-flight-delay-compensation-20-lakh-hit-in-2025-centre-11859629).
>
> ⚠️ **Verify against the CAR PDF before the demo.** Figures below come from legal commentary and press
> reporting, not from the primary document — I could not fetch the DGCA PDF directly. The *structure*
> is well corroborated; exact rupee values should be checked against the current CAR revision.
>
> *Content from external sources was rephrased for compliance with licensing restrictions.*

---

## The finding that changes the demo

**Weather delays do not attract cash compensation. They still attract duty of care.**

Force majeure — genuinely unforeseeable circumstances outside the airline's control — exempts an
airline from *monetary compensation*. It does **not** exempt the airline from its
[basic duty of care](https://www.timesnownews.com/travel/flight-delayed-or-cancelled-due-to-heavy-rain-heres-the-compensation-airlines-dont-want-you-to-know-about-article-155014308):
meals, refreshments, hotel accommodation and transfers.

This matters directly, because your worked scenario is a **storm**.

The original transcript's example computed a cash compensation figure for a weather delay. Under the
real rules that is **wrong** — cash owed for a genuine weather event is **₹0**, while hotel and meal
costs are still mandatory. Getting this right is a meaningful credibility win: it is exactly the kind
of domain nuance that separates a system built by someone who read the regulation from one that
invented a plausible number.

### Force majeure is not a blanket excuse

The IBA analysis is emphatic that airlines must prove circumstances were *truly* unforeseeable, and
that **internal planning failures do not qualify**. In the December 2025 IndiGo disruption, regulators
held that crew rostering failures were within the airline's control, so the force majeure defence did
not apply.

This is a genuine design requirement, not trivia. `trigger_type` determines whether cash is owed:

| Trigger | Force majeure? | Cash compensation | Duty of care |
| --- | --- | --- | --- |
| Weather | Yes | Not owed | Owed |
| ATC / airspace | Usually | Not owed | Owed |
| Security | Usually | Not owed | Owed |
| Technical / maintenance | No | Owed | Owed |
| Crew rostering | **No** — settled by regulator | Owed | Owed |
| Vendor failure | Generally no | Owed | Owed |

Given that [A4](DECISIONS.md) lists crew and maintenance as future triggers, this table is what makes
those extensions meaningful rather than cosmetic — the same delay costs the airline very different
amounts depending on cause.

---

## Delay: duty of care

Thresholds per the CAR:

| Delay | Entitlement |
| --- | --- |
| More than 2 hours | Free meals and refreshments |
| More than 6 hours, **or** crossing into nighttime | Hotel accommodation and transfers |

Two implementation notes:

- **"Crossing into nighttime" is a separate trigger from the 6-hour rule.** A 3-hour delay that pushes
  departure past night hours triggers hotel entitlement even though it is under 6 hours. A naive
  `delay_hours > 6` check misses this, and it is precisely the sort of case a judge might probe.
- The relevant hours must be configurable, not hardcoded — consistent with
  [A5](DECISIONS.md).

## Cancellation

Where cancellation occurs within two weeks of departure, the passenger is entitled to a full refund
**plus** monetary compensation of **₹5,000 – ₹10,000**, varying by route length / block time.

Commonly reported banding (**verify against the CAR**):

| Block time | Compensation cap |
| --- | --- |
| Up to 1 hour | ₹5,000 |
| 1 – 2 hours | ₹7,500 |
| Over 2 hours | ₹10,000 |

The payable amount is the **lesser** of the cap and the booked one-way basic fare plus airline fuel
charge. That "whichever is less" construction is important: it means compensation is a function of
both the cap *and* the fare, so the calculator needs fare data, not just the delay duration.

## Denied boarding (overbooking)

| Alternate flight arranged | Compensation |
| --- | --- |
| Departs within 1 hour of original | None |
| Within 24 hours | 200% of basic fare + fuel charge, capped ~₹10,000 |
| Beyond 24 hours | 400% of basic fare + fuel charge, capped ~₹20,000 |

Corroborated by [Economic Times reporting on the DGCA conditions](https://economictimes.indiatimes.com/industry/transportation/airlines-/-aviation/dgca-outlines-conditions-for-compensation-to-passenger-denied-boarding/printarticle/92203436.cms)
and [DGCA enforcement action against an airline for withholding it](https://timesofindia.indiatimes.com/business/india-business/dgca-fines-akasa-air-for-denying-compensation-to-offloaded-passengers/articleshow/116633115.cms).

Denied boarding is **out of MVP scope** — the disruption trigger is weather, not overbooking. Recorded
for completeness and because the rules table should be structurally capable of holding it.

## International flights

India is a signatory to the **Montreal Convention, 1999**, permitting compensation up to roughly
**USD 6,400** for proven damages, with a two-year claim window. Applies where origin or destination is
outside India.

Out of scope — the airport set in [D1](DECISIONS.md) is domestic.

## CAR amounts are floors, not ceilings

Indian consumer forums have held that CAR entitlements are **minimums, not limits**. Passengers may
additionally claim under the Consumer Protection Act, 2019 for deficiency of service.

Implication for the model: compensation output should be labelled as the **statutory minimum**, not as
a final settlement figure. Anything else overstates what the system knows.

---

## Seed data for `compensation_rule`

Populating the table from [`11-data-model.md`](11-data-model.md). Values marked ⚠️ need verification
against the current CAR revision.

```sql
-- Duty of care: applies regardless of cause, including force majeure
INSERT INTO compensation_rule
  (min_delay_minutes, max_delay_minutes, amount_inr, includes_meal, includes_hotel,
   applies_under_force_majeure, regulation_ref, effective_from) VALUES
  (120,  360,  0, TRUE,  FALSE, TRUE, 'CAR S3 Sr.M Pt.IV - meals >2h',            '2019-01-01'),
  (360, NULL,  0, TRUE,  TRUE,  TRUE, 'CAR S3 Sr.M Pt.IV - hotel+transfer >6h',   '2019-01-01');

-- Cash compensation: cancellation within two weeks. NOT payable under force majeure.
-- amount_inr is a CAP. Payable equals LEAST(cap, basic_fare + fuel_charge)
INSERT INTO compensation_rule
  (event_type, block_time_max_minutes, amount_inr, is_cap,
   applies_under_force_majeure, regulation_ref, effective_from) VALUES
  ('cancellation',   60,  5000, TRUE, FALSE, 'CAR S3 Sr.M Pt.IV - cancellation',   '2019-01-01'), -- ⚠️
  ('cancellation',  120,  7500, TRUE, FALSE, 'CAR S3 Sr.M Pt.IV - cancellation',   '2019-01-01'), -- ⚠️
  ('cancellation', NULL, 10000, TRUE, FALSE, 'CAR S3 Sr.M Pt.IV - cancellation',   '2019-01-01'); -- ⚠️
```

This requires three columns beyond the original schema:

```sql
ALTER TABLE compensation_rule
    ADD COLUMN event_type                  TEXT NOT NULL DEFAULT 'delay',
    ADD COLUMN block_time_max_minutes      INTEGER,
    ADD COLUMN applies_under_force_majeure BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN is_cap                      BOOLEAN NOT NULL DEFAULT FALSE;
```

`applies_under_force_majeure` is the column that encodes the central finding. Without it the
calculator cannot distinguish a storm from a crew failure, and will over-pay on every weather event.

## Calculator logic

Deterministic code. Never the LLM — see [`06-ai-vs-deterministic.md`](06-ai-vs-deterministic.md).

```
entitlements(delay_minutes, trigger_type, departure_time, fare) →

  is_force_majeure = trigger_type IN ('weather', 'atc', 'security')

  duty_of_care:
      delay > meal_threshold                        → meals
      delay > hotel_threshold OR crosses_night_hours → hotel + transfer

  cash:
      if is_force_majeure                           → ₹0, reason = 'force majeure exemption'
      elif event = cancellation AND notice < 14 days → LEAST(cap_for_block_time, fare + fuel)
      elif event = denied_boarding                   → 200% / 400% banding, capped
      else                                          → ₹0

  returns { meals, hotel, transfer, cash_inr, statutory_minimum: true, regulation_refs[] }
```

Always return the `regulation_refs` alongside the amount. That is what turns "₹0 compensation" from
something that looks like a bug into a defensible, cited decision — and it is the difference between
the Finance Agent being trusted and being questioned.

## Why this is a demo asset

When a judge asks why the system paid no compensation for a 6-hour storm delay, the answer is:

> Weather is force majeure under CAR Section 3 Series M Part IV, so no cash compensation is owed. Duty
> of care still applies, so we reserved hotels for 180 passengers and issued meal vouchers — ₹X total.
> Had the same delay been caused by crew rostering, cash compensation *would* be owed, because
> regulators have held that rostering failures are within airline control.

That answer demonstrates real domain grounding, hits **Relevance** and **Feasibility** in the judging
criteria, and is impossible to fake.

## Open items

- ⚠️ Download the actual CAR PDF from dgca.gov.in and verify every rupee figure and threshold.
- ⚠️ Confirm the definition of "nighttime" hours used by the CAR.
- ⚠️ Confirm whether the cancellation banding is by block time or by route distance — sources differ.
- Decide whether the demo mentions Consumer Protection Act exposure. Accurate, but arguably beyond
  scope for a 7-minute pitch.
