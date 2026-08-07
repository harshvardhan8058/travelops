# 9. Requirements Specification

Derived from the design conversation and constrained by what the researched data sources can actually
deliver ([`10-data-sources.md`](10-data-sources.md)).

> **Assumptions are marked ⚠️.** Each one is a decision I made to keep the spec coherent, not
> something you confirmed. Every ⚠️ is repeated in [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) — read that
> before treating this document as settled.

## Problem statement

When weather or an operational fault disrupts a flight, recovery today is manual: a controller works
the phones to rebook passengers, find hotel rooms, protect onward connections, and reassign gates. It
is slow, inconsistent between controllers, and unauditable after the fact.

TravelOps AI detects a likely disruption before it lands, produces a recovery plan, executes the
deterministic parts automatically, and records why every decision was made.

## Primary actor

✅ **Confirmed: operations-first.** The primary user is an airline **Operations Controller** working an
operations control centre dashboard. A passenger portal is a secondary interface, built only if time
allows.

| Actor | Role | Priority |
| --- | --- | --- |
| Operations Controller | Reviews and approves recovery plans; the human in the loop | **Primary** |
| Airline Operations Manager | Oversight across incidents | Secondary |
| Executive | Reads generated incident and cost reports | Secondary |
| Customer Support | Handles passenger queries | Secondary |
| Passenger | Receives notifications; portal is optional | Secondary |
| System (autonomous) | Detects, predicts, plans, executes | — |

## Scope

**In scope**

- Weather-driven delay prediction for a fixed set of airports
- Automated recovery planning for a single disrupted flight
- Simulated execution: hotel reservation, gate reassignment, connection checks
- Real email notification; simulated SMS and push
- Incident history with outcome recording, and retrieval of precedent
- Operator dashboard with plan approval and a decision audit trail

**Out of scope**

- Real bookings or payments of any kind
- **Crew duty-time legality validation** — a hard regulated domain. Crew *reassignment coordination and
  display* is in scope; checking legality is not
- Multi-airline interlining
- Live flight status ingestion — simulated, per [`10-data-sources.md`](10-data-sources.md)
- Mobile apps
- Baggage tracing

✅ **Resolved — cascading disruption is required.** A single weather event must propagate across many
flights:

```
Storm → 8 flights → 600 passengers → 22 connections → 11 hotels → 9 crew changes → transport → report
```

This replaces the earlier single-flight assumption. Consequences: an incident-group concept is needed,
crew and ground transport become first-class entities, and hotel capacity contention becomes real
rather than theoretical. See [`DECISIONS.md`](DECISIONS.md).

## Functional requirements

### Ingest and prediction

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-1 | Poll current airport weather (METAR) for a configured airport set | Must |
| FR-2 | Retrieve forecast conditions (TAF / Open-Meteo) to enable pre-emptive detection | Must |
| FR-3 | Maintain flight schedule and status from seeded real schedules plus a local simulator | Must |
| FR-4 | Compute a delay probability from airport conditions, without invoking an LLM | Must |
| FR-5 | Emit a typed `HIGH_RISK_DELAY` event when probability exceeds a configured threshold | Must |
| FR-6 | Suppress duplicate events for an already-open incident on the same flight | Must |

FR-6 is easy to forget and breaks the demo loudly: a weather poll every 60 seconds will otherwise open
a new incident every 60 seconds.

### Planning

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-7 | Generate a recovery plan as an ordered task list via Groq | Must |
| FR-8 | Return the plan as schema-validated JSON; reject and retry malformed output | Must |
| FR-9 | Retrieve comparable past incidents and supply them as planning context | Must |
| FR-10 | Validate every planned task against policy before execution | Must |
| FR-11 | Fall back to a deterministic playbook when the LLM is unavailable | Must |
| FR-12 | Escalate to a human when confidence falls below a threshold | Should |

### Execution

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-13 | Find hotels near the airport within budget and reserve (simulated) | Must |
| FR-14 | Identify onward connections at risk of being missed | Must |
| FR-15 | Reassign gate / stand (simulated) | Should |
| FR-16 | Calculate compensation deterministically from a rules table | Must |
| FR-17 | Dispatch passenger notifications across channels | Must |
| FR-18 | Execute independent tasks in parallel | Should |
| FR-19 | Make every execution action idempotent via an idempotency key | Must |

FR-16 is deliberately code, never the model — see [`06-ai-vs-deterministic.md`](06-ai-vs-deterministic.md).

### Memory and explainability

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-20 | Persist each incident with trigger, plan, actions, costs and outcome | Must |
| FR-21 | Record an operator feedback signal on resolved incidents | Should |
| FR-22 | Produce a human-readable justification for any chosen plan | Must |
| FR-23 | Expose a full chronological decision log per incident | Must |

### Operator interface

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-24 | Live dashboard of flights, risk levels and open incidents | Must |
| FR-25 | Incident detail view showing plan, actions and reasoning | Must |
| FR-26 | Approve / reject a proposed plan | Should |
| FR-27 | Manually trigger a disruption scenario for demonstration | Must |
| FR-28 | Replay an incident timeline | Could |

FR-27 exists purely for the demo and is a Must for that reason. You cannot wait for real weather during
judging.

## Non-functional requirements

| ID | Requirement | Target | Source |
| --- | --- | --- | --- |
| NFR-1 | Reproducibility — identical scenario yields materially identical plan | `temperature` 0–0.2, fixed seed | Risk 10 |
| NFR-2 | End-to-end latency, trigger to notifications dispatched | < 30 s | Risk 5 |
| NFR-3 | Total running cost | ₹0–₹500 | Stated budget |
| NFR-4 | LLM token consumption | Within ~100K tokens/day | Groq free tier |
| NFR-5 | Graceful degradation — recovery completes with the LLM down | No unhandled failure | Risk 11 |
| NFR-6 | Auditability — every automated action traceable to a trigger and a reason | 100% of actions | Risk 8 |
| NFR-7 | Loop safety — bounded recursion, iterations and time per incident | Hard caps enforced | Risk 2 |
| NFR-8 | No PII — synthetic passenger data only | No real personal data | Ethics / practicality |
| NFR-9 | Attribution — CC-BY 4.0 credit where required | Visible in UI | Open-Meteo terms |
| NFR-10 | Setup time for a new developer | < 30 min from clone to running | Team velocity |

NFR-4 deserves emphasis: at roughly 2–4K tokens per planning call, ~100K tokens/day is about 25–50
planning calls. A single afternoon of iterative debugging will exhaust it. Fixture-based development
is a requirement, not a nicety.

NFR-9 is a genuine licence obligation, not boilerplate. Open-Meteo's free tier is CC-BY 4.0 and
non-commercial.

## Constraints

| Constraint | Consequence |
| --- | --- |
| No free hotel inventory API exists | Hotel data synthetic; booking simulated |
| No usable free live flight status API | Flight state machine simulated locally |
| Open-Meteo free tier is non-commercial | Hackathon-only; blocks commercialisation as-is |
| BTS training data is US-domestic | Transfer assumption must be stated, not hidden |
| Groq free tier ~100K tokens/day | Caching and retrieval are mandatory |
| Budget ₹0–₹500 | Free tiers only; no paid infrastructure |

## Success criteria

The demo succeeds if a judge can watch this happen and follow every step:

1. Real current weather is displayed for a live airport.
2. A storm is injected; a flight's risk crosses the threshold and an incident opens.
3. A recovery plan appears, with the past incident that informed it shown alongside.
4. Tasks execute: hotels reserved, connections flagged, gate reassigned, compensation computed.
5. A real email arrives in an inbox on screen; 180 notifications appear as dispatched records.
6. The judge asks *why* — and the answer is on screen, not improvised.
7. Groq is disabled, the scenario re-runs, and recovery still completes via the fallback playbook.

Step 7 is the one that separates this from a demo that merely calls an LLM. Deliberately showing the
system survive its own AI failing is a stronger argument than any successful plan.

## Traceability

| Design doc | Requirements it governs |
| --- | --- |
| [01 Architecture](01-architecture.md) | FR-5, FR-6, NFR-7 |
| [02 Disruption flow](02-disruption-flow.md) | FR-1 → FR-19 |
| [03 Agent design](03-agent-design.md) | FR-8, FR-12, FR-13 |
| [04 Groq strategy](04-llm-strategy-groq.md) | FR-7, FR-11, NFR-1, NFR-4, NFR-5 |
| [05 Memory and RAG](05-memory-and-rag.md) | FR-9, FR-20, FR-21 |
| [06 AI vs deterministic](06-ai-vs-deterministic.md) | FR-4, FR-14, FR-16 |
| [07 Risks](07-risks-and-mitigations.md) | NFR-1, NFR-2, NFR-5, NFR-6, NFR-7 |
| [10 Data sources](10-data-sources.md) | FR-1, FR-2, FR-3, NFR-3, NFR-4, NFR-9 |
