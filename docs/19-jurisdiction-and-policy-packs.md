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
└── in-moca-charter-2019/          # real, encoded example
    └── 2019.02/
        ├── pack.yaml              # identity, scope, status, verified eligibility
        ├── applicability.yaml     # required facts + applicability rules
        ├── rules.yaml             # deterministic entitlements, each with source refs
        ├── test_cases.yaml        # expectations and fail-closed cases
        ├── review.yaml            # reviewer, open questions, per-rule sign-off
        ├── source-metadata.yaml   # URL, date, hash, supersession notes
        └── source.pdf             # archived original, once redistribution is confirmed
```

`conflict_rules.yaml` is added only when an authorised reviewer defines overlap handling. Until then
`pack.yaml` sets `conflict_rules_defined: false` and overlaps resolve to `needs_human`.

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

## Pack status ladder

A pack's status—not its existence—determines what the system may claim.

| Status | Source quality | May compute? | May be called current law? |
| --- | --- | --- | --- |
| `draft` | Anything, including commentary | No | No |
| `official_guidance_dated` | Official publication, but secondary or superseded-suspect | Yes, labelled | **No** |
| `approved` | Current primary regulation + SME sign-off | Yes | Yes |
| `retired` | Superseded | Replay only | No |

`POLICY_MODE` maps directly onto it: `demo` loads a fictional fixture, `charter` loads
`official_guidance_dated`, `verified` loads only `approved`. The loader rejects any pack whose
`verified_mode_eligible` is false when running in verified mode.

## India: current state

Two packs exist in the design, at different rungs of that ladder.

**`in-moca-charter-2019` — encoded, `official_guidance_dated`.** Built from the Ministry of Civil Aviation
Passenger Charter (February 2019) supplied by the team. Real, citable figures for delay care, cancellation
compensation bands, denied-boarding percentages and caps, baggage and cargo liability, and refund timing.
It carries a visible UI label and cannot reach verified mode, because the charter self-describes as general
guidance and secondary sources indicate later CAR revisions. See
[`13-compensation-and-policy.md`](13-compensation-and-policy.md).

**`in-dgca-car-3m4` — not yet created.** Requires the current primary CAR, its revision and effective
metadata, amendment history and an authorised SME review.

The important property: moving from charter to verified changes **pack data and status only**. The
resolver, engine, citation card and UI do not change. That is the scalability claim, demonstrated on the
jurisdiction we actually have.

## Hackathon scope

1. Ship the generic loader, resolver contract, rules engine and citation card.
2. Run Stage 2 and Stage 3 in `charter` mode against the encoded 2019 pack, clearly labelled.
3. Promote to `verified` when the primary CAR, resolved supersession questions and SME sign-off exist.
4. A second jurisdiction is optional structural proof only after the India flow works end to end. Do not
   claim compliance completeness for EU/UK/US/Montreal regimes.

## Fail-safe behaviour

| Condition | Result |
| --- | --- |
| Pack missing, draft, expired or hash mismatch | `needs_human`; no authoritative result |
| Pack is `official_guidance_dated` in verified mode | Load rejected; `PACK_NOT_VERIFIED_ELIGIBLE` |
| Rule marked `excluded_from_evaluation` | Skipped; supersession notice surfaced |
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
