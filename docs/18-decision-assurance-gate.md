# 18. Decision Assurance Gate

Replaces the LLM self-reported confidence score as the execution gate.

## Why the old design was wrong

The original contract had every agent return `"confidence": 92`, and the orchestrator thresholded on
it. Mentor review flagged this, correctly:

> LLM self-reported confidence is known to be poorly calibrated.

A model asked to rate its own certainty produces a plausible-looking number, not a measurement. It has
no access to whether the hotel API actually responded, whether the METAR is four hours stale, or
whether the passenger record exists. It is a token prediction about confidence, not confidence.

Worse, it fails in the wrong direction: models are most fluent when hallucinating, so a fabricated plan
often scores *higher* than a cautious correct one. Gating execution on that number means the system is
most likely to act automatically exactly when it should not.

## What replaces it

A deterministic gate, computed in code, from facts the orchestrator can verify. No model involvement.

```
Proposed action
      │
      ▼
┌─────────────────────────────────────────────┐
│  Decision Assurance Gate — 6 checks         │
│                                             │
│  1  Evidence completeness                   │
│  2  Source freshness                        │
│  3  Entity validation                       │
│  4  Policy compliance                       │
│  5  Conflict detection                      │
│  6  Action risk tier                        │
└─────────────────────────────────────────────┘
      │
      ├── all pass, low risk    →  execute
      ├── any soft failure      →  execute, flagged for review
      └── hard failure          →  needs_human  (blocked)
```

### The six checks

| # | Check | Question | Failure |
| --- | --- | --- | --- |
| 1 | **Evidence completeness** | Is every input the rule requires actually present? | Hard |
| 2 | **Source freshness** | Is each input inside its max age? METAR 60m, TAF 6h, flight status 5m | Soft, hard if double |
| 3 | **Entity validation** | Do the referenced flight, passenger, hotel, crew IDs exist and match state? | Hard |
| 4 | **Policy compliance** | Does the action pass every deterministic constraint — budget, duty of care, jurisdiction? | Hard |
| 5 | **Conflict detection** | Does this contradict another pending or executed action? Double-booked room, twice-rebooked passenger | Hard |
| 6 | **Action risk tier** | `low` (notify, hold) · `medium` (rebook, reserve) · `high` (cash, cancellation, bulk >100) | High always needs human |

Each returns a boolean plus a reason string. The gate result is a record, not a scalar:

```json
{
  "decision": "needs_human",
  "checks": {
    "evidence_complete": { "pass": true },
    "sources_fresh":     { "pass": false, "reason": "METAR VOBL 74m old, max 60m" },
    "entities_valid":    { "pass": true },
    "policy_compliant":  { "pass": true },
    "no_conflicts":      { "pass": true },
    "risk_tier":         { "value": "high", "reason": "cash compensation ₹5000 × 180 pax" }
  },
  "blocking": ["risk_tier"],
  "evidence_refs": ["metar:VOBL:2026-08-07T09:20Z", "dgca_car_3_m_iv:§3.1"]
}
```

Auditable, reproducible, and explainable without a model. Rerunning the same inputs yields the same
gate result — which a confidence score never guaranteed.

## Where the model still fits

The Planner may express a preference between valid options, and the Explainer may describe *why* a plan
was chosen. Neither authorises execution. The gate does. Model output is an input to the gate, never a
substitute for it.

If the LLM emits a `confidence` field, we log it as `model_self_report` for comparison and ignore it for
control flow. Over the sprint that gives us a small calibration dataset — which is exactly the
"alternative signals" the review asked for, and worth a sentence in the demo.

## The empirical signals

Mentor review asked for human approval rates and historical error rates. Honest position: with a
seven-day build and synthetic data we cannot claim real calibration. What we can do:

| Signal | Status | How |
| --- | --- | --- |
| Gate pass/block rate by action type | **Built** | Aggregated from `decision_log` |
| Human approve/reject rate on `needs_human` | **Built** | Recorded per approval; visible in analytics |
| Rule-level failure counts | **Built** | Which check blocks most often |
| Model self-report vs gate outcome | **Built** | Logged side by side; shows the miscalibration directly |
| Historical error rate on real outcomes | **Not claimed** | Needs production data we do not have |

Showing the fourth row — a chart where model confidence has little relationship to gate outcome — is a
stronger answer to the review than any number we could invent.

## Delay risk, not delay probability

The same discipline applies to the prediction surface. Unless a percentage is calibrated against
observed outcomes, calling it "94% probability" is unearned. So the UI shows a **risk index** and a
**level** (`low` / `elevated` / `high` / `severe`) with the contributing factors listed. A band we can
defend beats a decimal we cannot.

## Implementation notes

- Lives in `backend/app/assurance/`, pure functions, no I/O, fully unit-testable.
- Runs between planner output and execution, after schema validation.
- Freshness limits and risk tiers come from config. No magic numbers.
- Every gate evaluation writes to `decision_log` with its full check record.
- UI surface: the Assurance Gate panel in [`21-design-system.md`](21-design-system.md).
