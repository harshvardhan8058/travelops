# 19. Jurisdiction Resolution and Versioned Policy Packs

How regulatory intelligence scales beyond India without a rewrite.

## The question

Mentor review, on the DGCA CAR citation:

> It's India-specific. How does the regulatory intelligence module scale to other jurisdictions? Is it a
> rules engine, a RAG over legal texts, or hardcoded logic?

Answer: **a rules engine, with RAG alongside it for citation and explanation — never for calculation.**
Adding a jurisdiction is authoring a data file, not editing code.

## The pipeline

```
Trip Context
(origin, destination, carrier nationality, ticket point of sale, date)
      │
      ▼
Jurisdiction Resolver
      │  determines which regimes apply, and precedence
      ▼
Versioned Policy Pack        ◄── authored + compliance-reviewed, per jurisdiction
(YAML rules + source document + effective dates)
      │
      ▼
Deterministic Rules Engine
      │  computes entitlements. No model. Ever.
      ▼
Cited Explanation Layer      ◄── RAG retrieves the clause text
(amount + rule id + document version + quoted clause)
```

The load-bearing idea: **the jurisdiction is data, the engine is generic.** Nothing in the engine knows
the word "DGCA". It evaluates whatever pack the resolver hands it.

## Jurisdiction Resolver

Regimes attach to different facts, and more than one can apply.

| Regime | Triggered by |
| --- | --- |
| DGCA CAR (India) | Departure from India, or Indian carrier |
| EU 261/2004 | Departure from EU, or arrival into EU on an EU carrier |
| UK 261 | Same, post-Brexit UK |
| US 14 CFR / DOT | US domestic and departures |
| Montreal Convention | International carriage, baggage and delay damages |

When several apply, the resolver returns them ranked by the pack's declared precedence and the engine
computes the **most favourable to the passenger**, which is the near-universal legal default. The output
names every regime considered — including those that lost — because an auditor will ask.

```json
{
  "applicable": [
    { "pack": "in-dgca-car-3m4", "version": "2026.02", "precedence": 1 },
    { "pack": "intl-montreal",   "version": "1999.1",  "precedence": 2 }
  ],
  "resolution_basis": "departure_country=IN, carrier_country=IN",
  "selected": "in-dgca-car-3m4",
  "selection_rule": "most_favourable_to_passenger"
}
```

## Policy pack format

One directory per jurisdiction, versioned, with the source document alongside the rules.

```
policy_packs/
├── in-dgca-car-3m4/
│   ├── pack.yaml            # metadata, effective dates, precedence
│   ├── rules.yaml           # deterministic rules
│   ├── source.pdf           # the primary document, as published
│   └── extracted.md         # Docling output, chunked for retrieval
├── eu-261-2004/
└── intl-montreal/
```

```yaml
# pack.yaml
id: in-dgca-car-3m4
jurisdiction: IN
authority: Directorate General of Civil Aviation
document: CAR Section 3, Series M, Part IV
version: "2026.02"
effective_from: 2026-02-01
effective_to: null
currency: INR
applies_when:
  any_of:
    - departure_country: IN
    - carrier_country: IN
```

```yaml
# rules.yaml — excerpt
- id: duty_of_care.meals
  when:
    delay_minutes: { gte: 120 }
  entitlement:
    type: meals_refreshments
  force_majeure_exempt: false      # duty of care survives force majeure
  cite: "§3.1"

- id: compensation.cancellation_short_notice
  when:
    event: cancellation
    notice_hours: { lt: 336 }
    cause_class: { not_in: [weather, atc, security] }
  entitlement:
    type: cash
    amount_inr: 5000
    basis: one_way_basic_fare_plus_fuel_surcharge_capped
  cite: "§3.2(a)"
```

Rules are declarative and unit-testable. Every rule carries `cite`, so no entitlement can be produced
without a reference. Version pinning means a decision made in March is replayable against March's rules
even after an amendment — which is what makes the audit trail real rather than decorative.

## Where RAG belongs — and does not

**Does:** retrieve the clause behind a computed entitlement, quote it, surface the surrounding context,
and let the Explainer write readable prose grounded in the retrieved text. Also drafts candidate rules
when onboarding a new jurisdiction, for a human to review.

**Does not:** decide amounts, decide applicability, or authorise anything. A model that computes
statutory compensation is a liability. The number comes from the rules engine; retrieval only explains
where it came from.

This split is the reason the answer to "rules engine or RAG?" is "both, in strictly separate roles."

## Ingestion — and the one new dependency

Legal PDFs become structured text with **Docling** (or MarkItDown), both on the Coforge suggested
open-source list. Adopted on merit: we need a repeatable path from a published PDF to chunked,
citable text with clause structure preserved. Hand-typing regulation into YAML does not scale to five
jurisdictions and cannot be re-run when a document is amended.

```
source.pdf  →  Docling  →  extracted.md  →  clause chunks  →  Chroma (optional)
                                        └→  human authors rules.yaml, cites clause ids
```

For the MVP, retrieval over a single pack can be plain SQL/keyword lookup over clause chunks — the
vector store is only worth adding once multiple packs are in play.

## Onboarding a new jurisdiction

1. Add the primary document. 2. Run extraction. 3. Author `rules.yaml` with citations. 4. Declare
`applies_when` and precedence. 5. Write rule test cases. 6. Compliance review. 7. Ship the directory.

**No application code changes.** That is the scalability claim, and it is narrow enough to be true.

## Honest limits

- Only the India pack will be authored and tested for the hackathon. EU 261 ships as a **structural
  proof** — resolver, precedence, and a handful of rules — to demonstrate the mechanism, not as a
  compliance-complete implementation.
- Rule authoring is human work requiring legal review. We are not claiming automated legal
  interpretation, and should not.
- Exact DGCA figures must be verified against the current published CAR before implementation. See
  [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md). Cite or leave blank — never invent a rupee amount.
- UI surface: the policy citation card in [`21-design-system.md`](21-design-system.md).
