# 4. LLM Strategy (Groq)

## What Groq is good for

- Planning
- Summarisation
- Reasoning
- Structured outputs
- Fast responses

## What Groq must not be used for

- Database filtering
- Sorting
- Searching 10,000 rows
- Calculations
- Business rules

**Write code for those.** Every one of them is a solved problem with a deterministic, testable,
zero-cost implementation. Routing them through a model buys nothing and adds latency, cost,
non-determinism, and a hallucination surface.

## The core discipline

> Only invoke the LLM when *reasoning* is required.

Reasoning means: weighing incommensurable options, producing a novel plan, or explaining a decision
in natural language. Everything else is a lookup, a filter, or arithmetic.

## Structured output, always

The planner returns JSON, never prose:

```json
{
  "tasks": [
    "notify_passengers",
    "check_connections",
    "reserve_hotels",
    "reassign_gate"
  ]
}
```

Enforce this with a schema. Validate before execution. Reject and retry on malformed output rather
than attempting to salvage it with string parsing.

## Determinism settings

For anything in the planning path:

| Setting | Value | Why |
| --- | --- | --- |
| `temperature` | 0 – 0.2 | A hackathon demo must behave consistently across runs |
| `max_tokens` | Bounded per call | Prevents runaway generation and cost |
| Prompt | Standardised, versioned | Avoids prompt drift (see below) |

## Prompt drift

Different prompts produce different answers. If prompts are edited ad hoc during development, the
system's behaviour becomes unreproducible and you cannot tell whether a change in output came from
your code or from your wording.

Mitigation: keep prompts as **versioned artefacts** in the repo, not inline string literals scattered
through the codebase. One prompt per agent, one file, changes reviewed.

## Cost and rate limits

Even on Groq's free tier, excessive calls hit rate limits. Three strategies, in order of impact:

1. **Use deterministic code wherever possible.** The cheapest LLM call is the one you did not make.
2. **Cache responses.** Identical context should not be re-planned.
3. **Only invoke the LLM when reasoning is required.**

## Latency

Sequential chains are slow:

```
Agent1  →  Agent2  →  Agent3  →  Agent4
```

Run independent tasks in **parallel**. In the recovery plan example, `notify_passengers`,
`reserve_hotels`, and `reassign_gate` have no dependency on one another and should fan out
concurrently. Only genuinely dependent steps should serialise.

## Failure handling

Groq will time out. Plan for it:

- **Retry** with backoff on transient failures.
- **Fallback** to a deterministic default plan. For a delay over N hours, the templated playbook
  (notify → check connections → reserve hotels) is a reasonable non-AI baseline.
- Never let an LLM timeout mean passengers are not notified.

## Security boundary

Never let the LLM:

- Execute SQL
- Delete rows
- Call APIs directly

...without validation. The model proposes; validated code disposes. The LLM emits an intent
(`reserve_hotel`), and a code path that you wrote decides whether that intent is permissible and
then performs it.
