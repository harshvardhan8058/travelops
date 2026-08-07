# 7. Technical Challenges and Mitigations

The twelve failure modes this architecture has to survive.

---

## 1. Hallucinations

Groq may invent hotels, flights, or rules.

**Mitigation:** use RAG. Ground every factual claim in retrieved records. Never trust the model
blindly — validate proposed entities against the database before acting on them. If the planner
references a hotel ID that does not exist, reject the plan.

---

## 2. Infinite agent loops

```
Planner  →  Hotel  →  Planner  →  Hotel  →  Planner  →  ...
```

**Mitigation:**
- Max recursion depth
- Max iterations
- Timeouts

All three, enforced by the orchestrator rather than by any individual agent.

---

## 3. Context window

You cannot send 1000 flights, 500 passengers, and 50 hotels on every request.

**Mitigation:** retrieval. Select only the records relevant to the current incident. See
[`05-memory-and-rag.md`](05-memory-and-rag.md).

---

## 4. Cost

Even with Groq's free tier, excessive calls hit rate limits.

**Mitigation:**
- Cache responses
- Use deterministic code where possible
- Only invoke the LLM when reasoning is required

---

## 5. Latency

Sequential chains accumulate delay:

```
Agent1  →  Agent2  →  Agent3  →  Agent4
```

**Mitigation:** run independent tasks in parallel. Serialise only true dependencies.

---

## 6. Data quality

Weather, flight, and hotel sources may disagree with each other.

**Mitigation:** validation. Define precedence rules for conflicting sources, and treat disagreement
as a signal in its own right rather than silently picking the first response.

---

## 7. State management

Who owns `hotel_booked`? What happens if two agents try to book simultaneously?

**Mitigation:** a single source of truth. One owner per mutable fact. Agents read broadly, write only
to state they own. Idempotency keys on any booking-style operation.

---

## 8. Explainability

A judge will ask: *why did the AI reroute?*

**Mitigation:** logs. Persist the trigger, the retrieved context, the plan, the reason field from each
agent, and the outcome. An unexplainable decision is a failed decision in this domain, regardless of
whether it was correct.

---

## 9. Prompt drift

Different prompts produce different answers.

**Mitigation:** standardise prompts. Version them as files in the repo, review changes, one prompt per
agent.

---

## 10. Reproducibility

A hackathon demo should behave consistently.

**Mitigation:** `temperature` 0–0.2 for planning. Fixed seed data. A scripted scenario that has been
run end-to-end more than once before demo day.

---

## 11. API failure

Groq times out. So will the weather API.

**Mitigation:** retry with backoff, plus a fallback. The deterministic playbook must be able to run
without the model. A degraded recovery is acceptable; no recovery is not.

---

## 12. Security

Never let the LLM execute SQL, delete rows, or call APIs directly without validation.

**Mitigation:** the model emits an *intent*. Code that you wrote validates the intent against a
whitelist of permitted actions, checks it against policy, and then performs the operation. There is no
path from model output to a database write that does not pass through validation.

---

## Summary table

| # | Risk | Primary mitigation |
| --- | --- | --- |
| 1 | Hallucinations | RAG grounding + entity validation |
| 2 | Infinite loops | Recursion depth, iteration caps, timeouts |
| 3 | Context window | Retrieval instead of bulk context |
| 4 | Cost / rate limits | Caching, deterministic code, minimal LLM calls |
| 5 | Latency | Parallel execution of independent tasks |
| 6 | Data quality | Source precedence and validation rules |
| 7 | State management | Single source of truth, one owner per fact, idempotency |
| 8 | Explainability | Structured decision logs with reasons |
| 9 | Prompt drift | Versioned, standardised prompts |
| 10 | Reproducibility | Low temperature, fixed seed data, rehearsed scenario |
| 11 | API failure | Retry with backoff + deterministic fallback |
| 12 | Security | Intent validation; no direct model access to data or APIs |
