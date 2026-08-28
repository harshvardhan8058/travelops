# 39. G4 → Stream C: `fixtures/api/policy.json` contract change request

G4 is implemented: `GET /incidents/{ref}/policy` now serves Stream B's policy engine and the fixture
route is deleted. `docs/38-phase4-verified-policy.md` §6 records that when G4 lands, **A must match
the committed fixture byte-for-byte or C must change the fixture first** — it is a contract, not
sample data.

Two fields cannot be matched without Stream A fabricating regulatory content, so this is the request
rather than an edit. **`fixtures/api/policy.json` is Stream C's file and has not been touched.**

---

## 1 · `cause_assessment` asserts an exemption that no recorded assessment supports

The fixture states:

```json
"cause_assessment": {
  "operational_cause": "meteorological",
  "clearly_attributable": true,
  "external_to_carrier": true,
  "unavoidable_despite_reasonable_measures": true
}
```

The real endpoint returns `null` for all four. That is not a gap in the implementation — it is the
system refusing an inference it already promises never to make, in two places:

- `db/trip_context.py`: *"`cause_evidence` is deliberately not populated from `trigger_type`.
  Inferring 'external to carrier, unavoidable despite reasonable measures' from the word `weather`
  would be this system asserting a legal exemption it has no evidence for."*
- `services/compensation.py:_reason()` is the single place legal standing is rendered, and
  `docs/38` §4 calls that string a safety boundary.

`unavoidable_despite_reasonable_measures` is the operative test for a beyond-control exemption. It is
a finding about what the carrier did, not a restatement of the weather. Nothing in the dataset records
one, so the honest answer is `undetermined`.

**Requested change:** all four flags `null`, and keep the existing `note`. If the demo needs the
exemption to be *present*, the correct fix is a recorded cause assessment in the seed — Stream C's
data — not an assertion in the fixture. A `cause_evidence` block on the trip context would then flow
through unchanged; the endpoint reads whatever is recorded.

## 2 · `cause_comparison.alternative.cash_inr: 5000` needs a fact the trip context does not record

The fixture states `cash_inr: 5000` with
`formula_used: "least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000"`.

Re-running the same pack with the cause substituted returns **`outcome: "undetermined"`**, because
the charter's cancellation rule requires `cancellation.notice_obligation_met`, which
`load_trip_context` does not populate. Verified on real Postgres against the seeded storm:

```
comparison  enabled=True outcome=undetermined cash=None
            missing: ['cancellation.notice_obligation_met']
```

The fare inputs are present — the booking carries `4200` and `800` — so this is one missing fact, not
a broken path.

**Requested change, either:**

- **(a) preferred** — record `cancellation.notice_obligation_met` on the seeded booking (or expose it
  through `load_trip_context`), and the ₹5,000 figure appears by itself, computed and cited; or
- **(b)** change the fixture's `alternative` to `outcome: "undetermined"`, `cash_inr: null`, with
  `missing_facts: ["cancellation.notice_obligation_met"]`.

(a) is better for the demo: the comparison is at its most convincing when the same rules produce
"nothing owed" under a beyond-control cause and a real payable figure under an internal one. That
contrast is the point of the screen, and it is one recorded fact away.

## 3 · Additive fields, no action needed

The response also carries `generated_by: "policy-engine"`, top-level `missing_facts` and
`blocking_reasons`, and `outcome`/`missing_facts` on the comparison alternative. All additive;
Stream D's `PolicyResponse` type keeps compiling. `pack.source_hash` is `null` rather than
`"PENDING_ARCHIVAL"` — the loader exposes no archived source hash until **G3** (Stream B) records one,
and echoing a placeholder from this layer would be inventing provenance.

## 4 · Until this is resolved

The endpoint is live and correct. The divergence is confined to two fields, both in the direction of
claiming *less* than the fixture does. `backend/tests/contract/test_api_shapes.py` is Stream C's and
has not been changed; if it asserts fixture equality on those fields it will need one of the two
changes above.
