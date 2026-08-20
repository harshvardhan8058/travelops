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
  "action": "reserve_hotel",
  "reason": "Passenger has overnight delay",
  "evidence_refs": ["flight:6E2134", "metar:VOBL:2026-08-07T09:20Z"]
}
```

This is what makes orchestration reliable. The orchestrator branches on `status`, logs `reason` for
explainability, passes `evidence_refs` to the assurance gate, and never has to parse English.

## Suggested field semantics

| Field | Type | Notes |
| --- | --- | --- |
| `status` | `success` \| `failure` \| `skipped` \| `needs_human` | Drives orchestrator branching |
| `action` | enum, snake_case | Must match a known action; reject unknown values |
| `reason` | short string | Human-facing justification, surfaced in audit logs |
| `evidence_refs` | string[] | Inputs the decision rests on. Consumed by the assurance gate |

> **`confidence` was removed from the contract.** It was previously an integer 0–100 that the
> orchestrator thresholded on. For LLM-backed agents that number is self-reported and poorly calibrated,
> and thresholding on it means the system trusts itself most when it is fluently wrong. Execution is now
> gated by the deterministic **Decision Assurance Gate** —
> [`18-decision-assurance-gate.md`](18-decision-assurance-gate.md). If a model emits a confidence value
> we log it as `model_self_report` and ignore it for control flow.

`needs_human` is worth having from day one. It is the honest escape hatch for a low-confidence or
policy-blocked decision, and it demos far better than a confidently wrong action.

## The roster — correct terminology

Earlier drafts called all thirteen components "agents", with ten of them labelled "deterministic
agents". Mentor review flagged this as agent inflation, correctly. A stateless service that computes
compensation from a rules table is a **tool**, not an agent. Calling it one invites the suspicion that
the whole system is inflated.

The accurate taxonomy is **1 orchestrator + 3 reasoning agents + 10 deterministic services**:

### 1 orchestrator

Owns workflow state, sequencing, retries and the assurance gate. Deterministic. Not an agent — it is the
control plane.

### 3 reasoning agents (LLM-backed)

| Agent | Goal | Why a model earns its place |
| --- | --- | --- |
| **Planner** | Produce an ordered recovery task list | Open-ended sequencing under competing constraints |
| **Explainer** | Justify a plan in human language | Natural-language synthesis over structured evidence |
| **Report Generator** | Executive incident summaries | Narrative aggregation |

All three return validated JSON against a typed contract. None of them executes anything.

### 10 deterministic services (tools / microservices)

| Service | Goal |
| --- | --- |
| Delay Risk | Score disruption risk from weather and operational conditions — rules engine |
| Flight Recovery | Rebook and reroute affected passengers |
| Hotel | Find and reserve accommodation within budget |
| Transport | Arrange ground transfers |
| Communication | Dispatch email, SMS, push |
| Compensation | Compute entitlements from cited regulation |
| Crew Impact | Identify affected pairings; coordinate and display |
| Connection | Identify at-risk onward connections |
| Gate / Resource | Reassign gates and stands |
| Analytics / Learning | Aggregate metrics; record outcomes; surface precedent |

**Only 3 of 14 components touch a model, and none of them can execute an action.** That is the useful
fact when a judge asks whether this is "just a ChatGPT wrapper" — and it is stronger stated precisely
than inflated.

> The submitted deck reads "13 total agents, only 3 use LLM" and cannot be changed. Verbally and in all
> future material, use the taxonomy above. Framing: *"we counted tools as agents in the deck — there are
> 3 real agents, 10 deterministic services, and 1 orchestrator."* Volunteering the correction reads as
> rigour.

Two bounding notes:

- **Compensation must never use the LLM.** Entitlements are regulatory and cited — see
  [`19-jurisdiction-and-policy-packs.md`](19-jurisdiction-and-policy-packs.md). A model computing
  statutory amounts is a liability, not a feature.
- **Crew Impact coordinates; it does not validate legality.** Duty-time legality is a hard regulated
  domain and is explicitly out of scope. See [`22-crew-pairing-model.md`](22-crew-pairing-model.md).

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
