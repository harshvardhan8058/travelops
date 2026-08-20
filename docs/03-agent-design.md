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

## Worked example — Hotel service

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

## Structured response contracts

All three reasoning agents return validated JSON, but they do **not** share one action-shaped payload.
They share envelope fields and each has purpose-specific response fields. The Planner wire shape is:

```json
{
  "status": "success",
  "reason": "Plan generated from current incident and one matched precedent",
  "evidence_refs": ["incident:INC-...", "precedent:INC-..."],
  "payload_type": "planner.v1",
  "tasks": [
    {"action": "check_connections", "target_refs": ["flight:AI203"], "depends_on": []}
  ]
}
```

| Contract | Validated payload | Enters Assurance Gate? |
| --- | --- | --- |
| `PlannerResponse` | ordered `PlanTask[]`: known action enum, targets, inputs, dependencies | **Each task does** |
| `ExplanationResponse` | explanation text plus citation/evidence references | No—read-only artifact |
| `ReportResponse` | sections, metric references and summary | No—read-only artifact |

Shared envelope fields are `status`, `reason`, `evidence_refs`, `payload_type` and model-call audit
metadata. Only Planner tasks contain executable action enums. The exact discriminated-union contract is
in [`16-folder-structure.md`](16-folder-structure.md); the orchestrator never parses prose or overloads
an `action` field to carry reports/explanations.

> **`confidence` is absent.** If a model emits one, store it separately as `model_self_report` for
> diagnostic comparison and never branch on it. Every Planner task passes schema/entity validation and
> the deterministic [`Decision Assurance Gate`](18-decision-assurance-gate.md) before execution.

An agent may return `needs_human` to request review, but that does not authorise or reject an action;
the gate and immutable operator decision control execution.

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

## Constraints are not suggestions

Constraints must be enforced **outside** the model, in code, after the plan is produced. A validation
layer sits between the planner's output and execution:

```
Planner output  →  schema validation  →  policy validation  →  execute
                        │                      │
                     reject                 reject
```

If the planner proposes a ₹9000 hotel, the Hotel service's constraint check rejects it. The model is
never the last line of defence.
