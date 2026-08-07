# 3. Agent Design

## Stop trying to make agents "intelligent"

The instinct is to make each agent smart. That is the wrong lever. Instead, give every agent four
things and nothing more:

| Property | Question it answers |
| --- | --- |
| **Goal** | What does it do? |
| **Tools** | What APIs can it call? |
| **Memory** | What can it remember? |
| **Constraints** | What rules can it *not* violate? |

An agent is a bounded worker, not a personality.

## Worked example — Hotel Agent

**Goal**
Find nearby accommodation.

**Tools**
- Hotel database
- Maps

**Memory**
Previous bookings.

**Constraints**
- Budget < ₹6000
- Partner hotels first

This is a far better design than:

```
LLM  →  Hotel
```

...because every part of it is inspectable. You can unit-test the budget constraint. You cannot
unit-test a vibe.

## The response contract

Every agent returns structured output. Never prose.

**Never:**

```
I think...
Maybe...
```

**Always:**

```json
{
  "status": "success",
  "confidence": 92,
  "action": "reserve_hotel",
  "reason": "Passenger has overnight delay"
}
```

This is what makes orchestration reliable. The orchestrator branches on `status`, thresholds on
`confidence`, logs `reason` for explainability, and never has to parse English.

## Suggested field semantics

| Field | Type | Notes |
| --- | --- | --- |
| `status` | `success` \| `failure` \| `skipped` \| `needs_human` | Drives orchestrator branching |
| `confidence` | integer 0–100 | Below a threshold, escalate rather than execute |
| `action` | enum, snake_case | Must match a known action; reject unknown values |
| `reason` | short string | Human-facing justification, surfaced in audit logs |

`needs_human` is worth having from day one. It is the honest escape hatch for a low-confidence or
policy-blocked decision, and it demos far better than a confidently wrong action.

## Agent roster

Confirmed roster, reconciled with the Master Blueprint
([`reference/master-blueprint.md`](reference/master-blueprint.md)):

| Agent | Goal | LLM? |
| --- | --- | --- |
| Prediction Agent | Estimate delay probability from conditions | No — rules engine |
| Planner Agent | Produce an ordered recovery task list | **Yes** |
| Flight Recovery Agent | Rebook and reroute affected passengers | No |
| Hotel Agent | Find and reserve accommodation within budget | No |
| Transport Agent | Arrange ground transfers | No |
| Communication Agent | Dispatch email, SMS, push | Optional (wording only) |
| Finance Agent | Compute compensation and duty of care | No — regulatory rules |
| Crew Agent | Coordinate and display crew reassignment | No |
| Connection Agent | Identify at-risk onward connections | No |
| Gate / Resource Agent | Reassign gates and stands | No |
| Analytics Agent | Aggregate incident metrics | No |
| Learning Agent | Record outcomes; surface precedent | No |
| Explainer / Report Agent | Justify plans; generate executive summaries | **Yes** |

**Only three of thirteen use the LLM.** That ratio is the architecture working as intended — and it is
the single most useful fact to have ready when a judge asks whether this is "just a ChatGPT wrapper".

Two bounding notes:

- **Finance Agent must never use the LLM.** Compensation is regulatory and cited — see
  [`13-compensation-and-policy.md`](13-compensation-and-policy.md). A model computing statutory
  entitlements is a liability, not a feature.
- **Crew Agent coordinates; it does not validate legality.** Duty-time legality is a hard regulated
  domain and is explicitly out of scope. See the crew note in [`DECISIONS.md`](DECISIONS.md).

## Constraints are not suggestions

Constraints must be enforced **outside** the model, in code, after the plan is produced. A validation
layer sits between the planner's output and execution:

```
Planner output  →  schema validation  →  policy validation  →  execute
                        │                      │
                     reject                 reject
```

If the planner proposes a ₹9000 hotel, the Hotel Agent's constraint check rejects it. The model is
never the last line of defence.
