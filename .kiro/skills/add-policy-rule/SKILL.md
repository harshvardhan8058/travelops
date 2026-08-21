---
name: add-policy-rule
description: Add or modify a rule in a versioned policy pack, or implement the rules engine that evaluates them. Use when working on policy_packs/ or app/policy/, when encoding a regulation, or when an entitlement or compensation figure is involved.
---

# Add or change a policy rule

Regulation is **data**, never code. The engine must never contain the word DGCA.

Owner: Stream B. Design: `docs/19-jurisdiction-and-policy-packs.md`. Current pack:
`policy_packs/in-moca-charter-2019/2019.02/`.

## The rule that overrides everything else

**Never invent a figure, threshold, date or clause reference.** If the source does not state
it, the rule stays `draft` with the field empty and the evaluation returns `needs_human`.
A plausible-looking number is worse than a missing one, because nobody will question it.

## Status ladder

| Status | Meaning | May compute? | May be called current law? |
| --- | --- | --- | --- |
| `draft` | Encoded, awaiting review | No | No |
| `official_guidance_dated` | Official but secondary or possibly superseded | Yes, labelled | **No** |
| `approved` | Current primary regulation + SME sign-off | Yes | Yes |
| `retired` | Superseded | Replay only | No |

`POLICY_MODE` maps onto it: `demo` → fictional fixture, `charter` → `official_guidance_dated`,
`verified` → only `approved`. **No rule in this repository may be marked `approved` until an
authorised SME has signed `review.yaml`.**

## Required fields on every rule

```yaml
- id: cancellation.compensation.block_60_to_120
  status: draft                      # never `approved` without sign-off
  scope: all                         # all | domestic | international
  source_clause_refs: ["charter:p3:flight-cancellation:scenario-2-B"]
  interpretation: >-
    Your paraphrase of the clause. Never a long verbatim quote.
  when:
    all:
      - { fact: event.type, op: eq, value: cancellation }
      - { fact: flight.block_time_minutes, op: gt, value: 60 }
  entitlement:
    type: cash
    cap_inr: 7500
    formula: least_of_cap_and_basic_fare_plus_fuel_charge
```

A rule with no `source_clause_refs` must be rejected by the loader when the pack is
`approved`. A rule you suspect is out of date gets `status: superseded_suspected` **and**
`excluded_from_evaluation: true`, so it never evaluates.

## Facts, and what to do when one is missing

Declare every fact a rule needs. A missing required fact produces `needs_human` with the
exact field named in `missing_facts`. It never produces a default, a zero, or a guess.

Applicability is **tri-state**: `applicable`, `not_applicable`, `undetermined`. A missing fact
is `undetermined`. Collapsing unknown into false is how a system accidentally denies a
passenger an entitlement.

## Exemptions are evidence-gated

An exemption requires evidence that the cause was external **and** unavoidable despite all
reasonable measures. A weather trigger alone **never** exempts.

Test case `cancellation_weather_without_reasonable_measures_evidence` exists to prove this. If
you make it pass by inferring from `trigger_type`, you have broken the design.

## Two facts about the current pack worth remembering

- **Delay attracts no cash compensation in this instrument.** Cash exists only for
  cancellation and denied boarding. Never produce a delay payout.
- The hotel trigger requires advance notice **plus** either a 24-hour delay or a six-hour delay
  for a departure scheduled between 20:00 and 03:00. It is narrower than "over six hours".

## After any change

```bash
cd backend && uv run pytest
```

All 23 cases in the pack's `test_cases.yaml` must pass, including the fail-closed ones and
`verified_mode_rejects_this_pack`. Then add a test case for the rule you changed.
