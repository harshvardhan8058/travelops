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
      ├── all pass, low/medium risk          →  execute
      ├── warning + explicit low-risk policy →  execute_flagged
      └── any fail, high risk, missing config →  needs_human (blocked)
```

### The six checks

| # | Check | Question | Default on failure |
| --- | --- | --- | --- |
| 1 | **Evidence completeness** | Is every input the selected rule requires actually present? | `FAIL` |
| 2 | **Source freshness** | Is each input inside its configured max age? | `FAIL`; a specific low-risk action may downgrade this to `WARN` in versioned config |
| 3 | **Entity validation** | Do referenced flight, passenger, hotel and crew IDs exist and match current state? | `FAIL` |
| 4 | **Policy compliance** | Does the action pass every deterministic business and selected policy-pack constraint? | `FAIL` |
| 5 | **Conflict detection** | Does this contradict another pending/executed action or consume unavailable capacity? | `FAIL` |
| 6 | **Action risk tier** | `low` (notify) · `medium` (hold/reserve simulated inventory) · `high` (cash, cancellation, bulk external action) | High always blocks for human approval |

Each check returns `PASS`, `WARN` or `FAIL` plus a machine-readable reason. Aggregation is fail-closed
and ordered:

1. Missing gate configuration, unknown action type or unknown rule operator is `FAIL`.
2. Any `FAIL` produces `needs_human`; no action is executed.
3. `high` risk produces `needs_human` even when every other check passes.
4. A `WARN` may produce `execute_flagged` **only** when the versioned config explicitly allows that
   warning for that low-risk, reversible action type. There is no global "soft failure" bypass.
5. Otherwise all checks must pass. Multiple warnings never become safer by aggregation.

The full config version and hash are recorded with every evaluation, so a replay uses the same semantics
that applied when the decision was made.

```json
{
  "decision": "needs_human",
  "checks": {
    "evidence_complete": {"status": "PASS"},
    "sources_fresh": {
      "status": "FAIL",
      "reason_code": "SOURCE_STALE",
      "reason": "METAR VOBL 74m old, max 60m"
    },
    "entities_valid": {"status": "PASS"},
    "policy_compliant": {"status": "PASS"},
    "no_conflicts": {"status": "PASS"},
    "action_risk": {
      "status": "PASS",
      "tier": "high",
      "reason_code": "HUMAN_APPROVAL_REQUIRED"
    }
  },
  "blocking": ["sources_fresh", "action_risk"],
  "evidence_refs": ["metar:VOBL:2026-08-07T09:20Z", "policy-rule:verified-pack:rule-id"]
}
```

The check representation preserves `WARN` rather than collapsing it into a boolean. Action risk can
pass as a classification check while its `high` tier still blocks under the aggregation rule.

Auditable, reproducible, and explainable without a model. Rerunning the same inputs yields the same
gate result — which a confidence score never guaranteed.

## Where the model still fits

The Planner may express a preference between valid options, and the Explainer may describe *why* a plan
was chosen. Neither authorises execution. The gate does. Model output is an input to the gate, never a
substitute for it.

If the LLM emits a `confidence` field, we log it as `model_self_report` for diagnostic comparison and
ignore it for control flow. It is not calibration data: gate outcomes are policy decisions, not ground
truth. Genuine calibration requires reviewed human labels or observed operational outcomes.

## The empirical signals

Mentor review asked for human approval rates and historical error rates. Honest position: with synthetic
data we cannot claim real calibration. We can specify and later measure diagnostic signals:

| Signal | Status before implementation | How it will be measured |
| --- | --- | --- |
| Gate pass/block rate by action type | **Specified** | Aggregated from `decision_log` |
| Human approve/reject rate on `needs_human` | **Specified** | Recorded per approval; visible in analytics |
| Rule-level failure counts | **Specified** | Which check blocks most often |
| Model self-report vs gate decision | **Specified diagnostic** | Logged side by side; not described as accuracy or calibration |
| Historical error rate on real outcomes | **Unavailable** | Requires production outcomes or reviewed labels |

Do not claim that model confidence is disproved by our gate. The useful demo point is narrower: the
model's number does not authorise execution; verifiable checks do.

## Delay risk, not delay probability

The same discipline applies to the prediction surface. Unless a percentage is calibrated against
observed outcomes, calling it "94% probability" is unearned. So the UI shows a **risk index** and a
**level** (`low` / `elevated` / `high` / `severe`) with the contributing factors listed. A band we can
defend beats a decimal we cannot.

## Implementation notes

- Lives in `backend/app/assurance/`, pure functions, no I/O, fully unit-testable.
- Runs between planner output and execution, after schema validation.
- Freshness limits, risk tiers and warning exceptions come from versioned config. Missing config fails
  closed.
- Every gate evaluation records check states, reasons, evidence references, risk tier, config version,
  config hash and evaluation timestamp in its own immutable record, referenced by `action`.
- UI surface: the Assurance Gate panel in [`21-design-system.md`](21-design-system.md).
