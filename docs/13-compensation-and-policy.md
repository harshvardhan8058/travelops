# 13. Compensation and Policy — Provisional Research Note

> **Status: not implementation-authoritative.** This document records the policy questions and the safe
> architecture. The current primary DGCA CAR, its revision/effective date, and a completed rule-review
> sheet are not yet archived in this repository. Until they are, no rupee amount, threshold, nighttime
> interpretation, applicability rule or force-majeure conclusion may be shipped as a verified legal
> entitlement.

This is technical design, not legal advice. The implementation source of truth will be a reviewed,
versioned policy pack described in [`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md),
not prose in this file.

## Target source

**DGCA Civil Aviation Requirements, Section 3, Series M, Part IV** — facilities to passengers due to
denied boarding, cancellation and delays.

Obtain it through the official [DGCA website](https://www.dgca.gov.in/) by navigating to **Regulations →
Civil Aviation Requirements → Section 3 → Series M → Part IV**. Save the PDF, the page showing its
revision/effective date, and any amendments. The Ministry of Civil Aviation also publishes a
[Passenger Charter](https://www.civilaviation.gov.in/sites/default/files/2023-01/Passenger%20Charter%20MoCA%20India%20Feb%202019%20(1).pdf),
but the Charter is supporting guidance—not a replacement for the current CAR.

Exact team action and acceptance criteria: [`24-input-acquisition.md`](24-input-acquisition.md).

## What is safe to say now

- India has passenger-protection requirements addressing denied boarding, cancellations and delays.
- The architecture will compute any entitlement deterministically from a selected, reviewed policy
  pack; an LLM never calculates or authorises it.
- Duty-of-care and cash-compensation questions can have different conditions and exemptions.
- The result must include the exact policy pack, version, rule ID, input facts and source clause.
- If source or applicability evidence is incomplete, the Assurance Gate blocks an authoritative result
  and routes it to `needs_human`.

Do **not** say a particular weather event automatically qualifies as force majeure. The legal outcome
can depend on the selected rule, foreseeability/avoidability, evidence of the actual cause, and whether
reasonable measures were taken. `trigger_type = weather` is operational context, not a legal verdict.

## Facts the calculator must receive

The old design accepted only delay, trigger and fare. That is insufficient. Policy packs may require:

| Fact family | Example fields |
| --- | --- |
| Itinerary | origin, destination, all legs/connections, scheduled/actual timestamps |
| Carriers | operating carrier, marketing carrier, carrier country |
| Ticket | place/date of contracting, passenger eligibility, basic fare, fuel surcharge, currency |
| Event | delay/cancellation/denied boarding, notice time, offered alternatives, passenger choice |
| Cause evidence | source records, timestamps, operational cause, foreseeability/avoidability facts, reasonable measures |
| Jurisdiction | departure/arrival countries, travel date, applicable pack candidates |
| Passenger outcome | actual rerouting, accommodation, meals, refund and acceptance timestamps |

Missing required facts produce `needs_human`; they are never guessed.

## Cause assessment

Represent cause and legal assessment separately:

```json
{
  "operational_cause": "adverse_weather",
  "evidence_refs": ["metar:VOBL:...", "ops_event:..."],
  "policy_assessment": {
    "rule_id": "pending_primary_source_review",
    "external_to_carrier": null,
    "foreseeable_or_avoidable": null,
    "reasonable_measures_evidenced": null,
    "decision": "needs_human"
  }
}
```

This prevents a simplistic `weather|atc|security = force_majeure` lookup from becoming fake legal logic.
A crew/maintenance cause may be useful as a contrasting fixture, but it receives the same evidence-led
policy evaluation.

## Verification matrix to complete from the primary document

| Question | Current status | Required evidence |
| --- | --- | --- |
| Delay meal/refreshment conditions | Unverified | Clause text + revision |
| Hotel/transfer conditions and any nighttime definition | Unverified | Clause text + definitions |
| Cancellation notice windows | Unverified | Clause text |
| Cancellation compensation bands and basis | Unverified | Clause text; confirm block time vs other basis |
| Lesser-of fare/cap formula and fare components | Unverified | Clause text + definitions |
| Denied-boarding bands | Out of MVP; unverified | Clause text only if added |
| External-circumstance/force-majeure test | Unverified | Clause text + reviewed interpretation |
| Applicability to carrier/route/passenger | Unverified | Scope/applicability clauses |
| Effective date and amendment history | Unverified | CAR metadata and amendment notices |
| Document redistribution permission | Unverified | DGCA terms or internal approval |

The reviewer records for each rule: clause reference, paraphrased requirement, exact input fields,
expected outputs, edge cases, source-document hash, reviewer and date. No blank cell may be converted
into executable policy.

## Safe implementation modes

| Mode | Behaviour |
| --- | --- |
| `POLICY_MODE=demo` | Runs a clearly labelled business-policy fixture to prove the engine; no legal-authority wording and no authoritative rupee figure |
| `POLICY_MODE=verified` | Loads only approved pack versions whose source hash and review status pass validation |
| Missing/invalid pack | Gate returns `needs_human`; entitlement action is blocked |

The demo can proceed in `demo` mode if the document is delayed. That preserves engineering progress
without bluffing the regulation. The Stage 3 target is to replace it with the reviewed India pack.

## Output contract

```json
{
  "status": "needs_human",
  "policy_mode": "demo",
  "jurisdiction_resolution": [],
  "entitlements": [],
  "blocking_reasons": ["verified India policy pack unavailable"],
  "evidence_refs": []
}
```

A successful verified result additionally includes `policy_pack`, `pack_version`, `pack_hash`,
`rule_id`, `source_clause_refs`, `input_facts`, calculation breakdown, amount/currency if applicable,
and `evaluated_at`.

## Demo wording

Until verification is complete:

> “The regulatory module is running in a labelled policy-fixture mode. The architecture is complete—
> jurisdiction resolution, versioned rules, deterministic calculation and clause citation—but we do
> not present provisional figures as law. The current DGCA document must pass source and SME review
> before verified mode can authorise an entitlement.”

After verification, replace only the fixture and wording; the engine and UI do not change.

*External source content referenced above has been summarized and rephrased for licensing compliance.*
