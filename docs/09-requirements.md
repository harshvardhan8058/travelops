# 9. Requirements Specification

Derived from the design conversation and constrained by what the researched data sources can actually
deliver ([`10-data-sources.md`](10-data-sources.md)).

> **Assumptions are marked ⚠️.** Each one is a decision I made to keep the spec coherent, not
> something you confirmed. Every ⚠️ is repeated in [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) — read that
> before treating this document as settled.

## Problem statement

**Working hypothesis to validate with an airline-operations SME:** disruption recovery requires several
teams to coordinate passenger reaccommodation, hotels, connections, crew/resource impact and
communications under time pressure. Fragmented tools can make the response slower, less consistent and
harder to audit.

TravelOps AI demonstrates a bounded autonomous workflow that detects disruption risk, proposes a
recovery plan, executes permitted deterministic actions, blocks unsafe actions, and records the evidence
behind every decision. Do not present the first paragraph as measured customer research until an SME or
controller confirms it.

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

- Phase 1: deterministic recovery for one disrupted flight
- Phase 2: traceable multi-flight cascade from one airport weather event
- Simulated execution: reaccommodation, hotel, transport and gate/resource actions
- Real email to allowlisted team inboxes; simulated bulk email/SMS/push
- Incident history with outcome recording and SQL precedent retrieval
- Operator dashboard with approval, Assurance Gate and decision audit trail

**Out of scope**

- Real bookings or payments of any kind
- **Crew duty-time legality validation** — a hard regulated domain. Crew *reassignment coordination and
  display* is in scope; checking legality is not
- Multi-airline interlining
- Live flight status ingestion — simulated, per [`10-data-sources.md`](10-data-sources.md)
- Mobile apps
- Baggage tracing

✅ **Resolved — scope is phased, not contradictory.** Phase 1 proves a complete single-flight path.
Phase 2 expands the same workflow to a traceable cascade:

```
Storm → 8 flights → ~600 passengers → 22 connections → 11 hotels → 9 crew pairings → report
```

The single-flight fixture is a child case within the larger BLR incident group, not a competing
scenario. Headline counts are targets for the fixed synthetic fixture and must be computed from records,
never hardcoded into the UI.

## Functional requirements

### Ingest and prediction

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-1 | Poll current airport weather (METAR) for a configured airport set | Must |
| FR-2 | Retrieve forecast conditions (TAF / Open-Meteo) to enable pre-emptive detection | Must |
| FR-3 | Maintain flight schedules from an inspected public artifact when available, otherwise a labelled synthetic fixture; status is always simulator-driven for the MVP | Must |
| FR-4 | Compute a deterministic delay **risk index and level** from airport conditions; do not call it a probability until calibrated | Must |
| FR-5 | Emit a typed `HIGH_RISK_DELAY` event when the configured risk threshold is crossed | Must |
| FR-6 | Suppress duplicate events for an already-open incident on the same flight | Must |

FR-6 is easy to forget and breaks the demo loudly: a weather poll every 60 seconds will otherwise open
a new incident every 60 seconds.

### Planning

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-7 | Generate a recovery plan as an ordered task list via Groq | Must |
| FR-8 | Return the plan as schema-validated JSON; reject and retry malformed output | Must |
| FR-9 | Retrieve comparable past incidents through explainable SQL matching and supply them as planning context | Should |
| FR-10 | Evaluate every proposed action through schema validation and the Decision Assurance Gate | Must |
| FR-11 | Fall back to a deterministic playbook when the LLM is unavailable | Must |
| FR-12 | Block and route `needs_human` when a gate check fails, safety config is missing or action risk is high; expose failed checks and evidence | Must |

### Execution

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-13 | Find hotels near the airport within budget and reserve (simulated) | Must |
| FR-14 | Identify onward connections at risk of being missed | Must |
| FR-15 | Reassign gate / stand (simulated) | Should |
| FR-16 | Evaluate entitlements deterministically from a versioned policy pack and return pack/rule/clause references; suppress authoritative amounts when the pack is unverified | Must |
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
| FR-24 | Live dashboard of flights, risk levels, open incidents and source provenance | Must |
| FR-25 | Incident view showing plan, actions, evidence, assurance checks and reasoning | Must |
| FR-26 | Approve / reject an assurance-blocked or high-risk action with actor and timestamp | Must |
| FR-27 | Manually trigger and reset a disruption scenario for demonstration | Must |
| FR-28 | Replay an incident timeline | Should |

FR-27 exists purely for the demo and is a Must for that reason. You cannot wait for real weather during
judging.

## Non-functional requirements

| ID | Requirement | Target | Source |
| --- | --- | --- | --- |
| NFR-1 | Reproducibility — identical fixture yields materially identical plan | deterministic services exact; fixture plan exact; live LLM schema-valid | Risk 10 |
| NFR-2 | End-to-end latency, fixture trigger to notification records | < 30 s on demo machine | Risk 5 |
| NFR-3 | Total running cost | ₹0–₹500 | Stated budget |
| NFR-4 | LLM consumption | Configured to the team account's observed console limits; no hardcoded free-tier assumption | Provider risk |
| NFR-5 | Graceful degradation — recovery completes with the LLM down | No unhandled failure | Risk 11 |
| NFR-6 | Auditability | 100% of proposed actions link evidence, assurance evaluation and actor | Risk 8 |
| NFR-7 | Loop safety | Hard caps enforced; missing safety config fails closed | Risk 2 |
| NFR-8 | No PII | Synthetic passenger data only; recipient allowlist stored outside Git | Ethics / practicality |
| NFR-9 | Attribution | Provider-required attribution visible in UI and source ledger | Licence terms |
| NFR-10 | Setup time for a new developer | < 30 min from clone to seeded fixture | Team velocity |
| NFR-11 | Accessibility | WCAG AA contrast, keyboard access, status not colour-only | Usability |
| NFR-12 | Demo recovery | One-command reset and fixture/offline operation | Evaluation resilience |

NFR-4 is account-specific. Provider quotas change and may differ by account/model, so the team must
record the limits shown in the Groq console and configure local budgets below them. Fixture-based
development is mandatory regardless of the current allowance.

NFR-9 is a genuine licence obligation, not boilerplate. Open-Meteo's free tier is CC-BY 4.0 and
non-commercial.

## Constraints

| Constraint | Consequence |
| --- | --- |
| No suitable hotel-inventory source identified under budget/coverage constraints | Hotel data synthetic; booking simulated |
| No suitable free live flight-status feed validated | Flight state machine simulated locally |
| Open-Meteo free access is non-commercial and attribution-licensed | Hackathon-only; revalidate terms before product use |
| BTS training data is US-domestic | Transfer assumption must be stated, not hidden |
| Groq quotas vary by account and model | Read limits from console; fixture/off modes required |
| Budget ₹0–₹500 | Free/open components and local Docker; no dependency on paid APIs |

## Success criteria

The demo succeeds if a judge can watch this happen and follow every step:

1. Public current aviation weather or the labelled offline fixture is displayed.
2. The fixed storm scenario is injected; a flight's deterministic risk level crosses threshold once.
3. A schema-valid plan or fallback playbook appears with its evidence and matched precedent.
4. Deterministic actions execute only after assurance: hotel/connection/resource records and provenance.
5. An allowlisted real email arrives if credentials are configured; bulk notifications remain simulated.
6. A judge can open any action and see evidence, assurance checks, actor and timeline.
7. `LLM_MODE=off` completes the same core recovery.
8. If the verified India pack is present, an entitlement links to pack version, rule and source clause;
   otherwise the UI clearly labels `DEMO_POLICY_FIXTURE` and shows no authoritative amount.

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
