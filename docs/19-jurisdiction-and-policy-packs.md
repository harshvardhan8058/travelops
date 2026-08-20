# 19. Jurisdiction Resolution and Versioned Policy Packs

How regulatory intelligence scales beyond India without hardcoding law or asking an LLM to decide it.

## Short answer to the mentor

**Both rules and retrieval, in separate roles:** a jurisdiction-neutral deterministic rules engine
calculates from a reviewed, versioned policy pack. Retrieval locates and displays the supporting clause.
It never decides applicability, computes an amount or authorises execution.

## Pipeline

```text
Trip Context
  → Jurisdiction Resolver
  → reviewed applicable pack(s) + pack-specific conflict rules
  → Deterministic Rules Engine
  → Decision Assurance Gate
  → Cited Explanation
```

The engine knows operators such as comparisons, date windows, capped formulas and evidence presence. It
does not know the word “DGCA.” Jurisdiction-specific rules, applicability and conflict handling live in
packs.

## Trip Context

Applicability cannot be resolved from origin and destination alone. The resolver accepts a typed context:

- complete itinerary: every origin, destination, connection, scheduled/actual timestamp and travel date
- operating and marketing carrier plus carrier country
- ticket place/date of contracting, fare components and currency when a rule requires them
- passenger/ticket eligibility facts declared by the pack
- event type, actual impact, notice timestamp and alternatives offered
- passenger refund/rerouting choice and acceptance timestamps
- operational cause evidence, foreseeability/avoidability facts and reasonable measures
- source/evidence timestamps and provenance

Each pack declares which fields it requires. A missing required field makes that pack's applicability
`undetermined`, never false and never guessed.

## Resolver output

```json
{
  "candidates": [
    {
      "pack": "in-dgca-car-3m4",
      "version": "pending",
      "status": "undetermined",
      "basis": ["departure_country=IN"],
      "missing": ["verified_pack", "operating_carrier_country"]
    }
  ],
  "selected": [],
  "conflicts": [],
  "decision": "needs_human"
}
```

No global “most favourable to the passenger” rule is assumed. Applicability, overlap, precedence and
conflict resolution can differ by regime and legal context; they must be declared in reviewed pack
metadata with their source basis. If two applicable packs conflict and no reviewed resolution rule
exists, the result is `needs_human`.

## Policy-pack format

```text
policy_packs/
└── in-dgca-car-3m4/
    └── <version>/
        ├── pack.yaml              # identity, scope, effective dates, review state
        ├── applicability.yaml     # required facts + applicability rules
        ├── conflict_rules.yaml    # only reviewed overlap/precedence rules
        ├── rules.yaml             # deterministic entitlements
        ├── test_cases.yaml        # examples and edge cases from review
        ├── review.yaml            # reviewer, date, approval, comments
        ├── source.pdf             # archived primary document, if redistribution permits
        ├── source.sha256
        └── extracted.md           # clause-structured extraction
```

Illustrative metadata—**not a verified DGCA pack**:

```yaml
id: in-dgca-car-3m4
version: pending-primary-source
jurisdiction: IN
authority: Directorate General of Civil Aviation
status: draft                    # draft | reviewed | approved | retired
effective_from: null
required_context:
  - itinerary
  - operating_carrier
  - event
  - notice
  - cause_evidence
source:
  url: null
  sha256: null
```

Illustrative rule structure—amount intentionally omitted until source review:

```yaml
- id: cancellation.short_notice
  status: draft
  when:
    all:
      - fact: event.type
        op: eq
        value: cancellation
      - fact: event.notice_minutes
        op: lt
        value_from: pack.parameters.cancellation_notice_window
  entitlement:
    type: cash
    formula_from: pack.formulas.cancellation_cap
  source_clause_refs: []
```

The loader rejects `status != approved` in `POLICY_MODE=verified`. Every executable verified rule must
have source clauses, test cases, a reviewer and an immutable pack hash.

## Rule-engine boundary

“Add a jurisdiction without code changes” is true **only for rules expressible in the supported DSL**.
A new legal concept may require a new operator or context field; that is a reviewed engine change with
new tests. The honest scalability claim is:

> Most jurisdiction onboarding is versioned policy data and review, while the engine remains stable for
> rules already covered by its DSL.

## Retrieval boundary

**Retrieval does:**

- locate the exact clause(s) referenced by an already-selected rule
- show nearby definitions and context
- supply grounded text to the Explainer
- help a human draft candidate pack rules for review

**Retrieval never:**

- selects jurisdiction or resolves a legal conflict
- invents a missing rule/amount
- computes an entitlement
- changes a draft rule to approved
- authorises an action

For one India pack, clause lookup can be SQL/full-text search. Chroma is optional only when corpus size
makes it useful.

## Ingestion

```text
official source document
  → hash and archive
  → Docling extraction
  → clause segmentation
  → human rule authoring
  → SME/compliance review
  → tests
  → approved pack
```

[Docling](https://github.com/docling-project/docling) is a candidate open-source extractor selected on
merit for structured PDF conversion. The extracted text is not the legal source; the archived primary
document and its hash are.

## Hackathon scope

1. Ship the generic loader, resolver contract, rules engine and citation card.
2. Develop against a conspicuous `DEMO_POLICY_FIXTURE` while source review is pending.
3. Replace it with one verified India pack when the primary CAR and rule-review sheet are available.
4. A second jurisdiction is optional structural proof only after the India flow works end to end. Do not
   claim compliance completeness for EU/UK/US/Montreal regimes.

## Fail-safe behaviour

| Condition | Result |
| --- | --- |
| Pack missing, draft, expired or hash mismatch | `needs_human`; no authoritative result |
| Required trip fact missing | pack applicability `undetermined` |
| Multiple packs conflict without reviewed conflict rule | `needs_human` |
| Rule lacks source clause | loader rejects pack |
| Explanation retrieval fails after calculation | result may remain, but citation UI reports source unavailable and external action is blocked if citation is required |

## User input required

The current DGCA primary document, amendment history, revision metadata and a review by an authorised
aviation/legal SME are outside the public build context. The exact acquisition and handoff format is in
[`24-input-acquisition.md`](24-input-acquisition.md). Until supplied, code can prove the architecture but
must not claim a legally verified entitlement.

*External source information was summarized and rephrased for licensing compliance.*
