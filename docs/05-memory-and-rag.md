# 5. Memory and Retrieval

## Conversation history is not memory

The default mistake is to treat the chat transcript as the system's memory. It is not. A transcript
grows without bound, contains no outcome data, and cannot answer "did this actually work last time?"

## Incident memory

Store structured incident records instead:

```
Incident Memory
    ↓
Previous Recovery
    ↓
Cost
    ↓
Outcome
    ↓
Feedback
```

Each record captures what happened, what was decided, what it cost, and whether it worked. That last
field is the one that makes the system improve.

## How the planner uses it

Later, the Planner asks:

```
Have we solved similar disruptions?
```

RAG retrieves:

```
Storm
Delhi
July 2026
Recovery succeeded
Hotels first
```

The planner now has precedent, and its plan is grounded in an outcome that actually occurred rather
than in a plausible-sounding invention. **The planner learns from history.**

## Retrieval is a hard requirement, not a nice-to-have

You cannot send:

- 1000 flights
- 500 passengers
- 50 hotels

...on every request. The context window will not hold it, the latency would be unacceptable, and the
cost would be absurd. Retrieval exists to select the handful of records that are actually relevant to
*this* disruption.

This is also the primary defence against hallucination. If the model is only shown real hotels from
the database, it has much less room to invent one.

## Suggested incident record shape

```json
{
  "incident_id": "INC-2026-0714-BLR-01",
  "trigger": { "type": "weather", "airport": "BLR", "wind": 45, "visibility": 800 },
  "prediction": {
    "risk_index": 87,
    "risk_level": "high",
    "rule_version": "delay-risk-v1",
    "factors": ["visibility_below_threshold", "crosswind_elevated"]
  },
  "plan": ["notify_passengers", "check_connections", "reserve_hotels", "reassign_gate"],
  "execution": [
    { "action": "reserve_hotels", "status": "success", "cost_inr": 4200 }
  ],
  "outcome": { "resolved": true, "passengers_reaccommodated": 180, "total_cost_inr": 51000 },
  "feedback": { "operator_rating": 4, "notes": "Hotels-first ordering worked well" }
}
```

## Memory tiers

| Tier | Holds | Used for |
| --- | --- | --- |
| Working state | The in-flight incident's current status | Orchestration, deduplication |
| Incident memory | Completed incidents with outcomes and costs | Retrieval for planning; learning |
| Reference / knowledge | Policies, hotel inventory, compensation rules | Grounding — prevents invented facts |

Reference data is the RAG corpus that stops the model inventing hotels, flights, and rules. Incident
memory is what lets it prefer strategies that have previously worked.

## State ownership

A single question decides whether this design holds up: **who owns `hotel_booked`?**

If two agents can both attempt a booking, you will double-book. Every mutable fact needs exactly one
owner and a single source of truth. Agents read widely; they write only to what they own. See the
state management entry in [`07-risks-and-mitigations.md`](07-risks-and-mitigations.md).
