# 37. Phase 3 — Reasoning Agents

**Demo claim: "the model plans and explains; it never decides alone."**

Phase 2 delivered a deterministic recovery that completes without any model. Phase 3 adds three
reasoning agents — Planner, Explainer, Report Generator — behind the **existing** authorization path.
No agent may directly perform an external operational write.

## Scope

| Deliverable | Owner | Enters assurance? |
| --- | --- | --- |
| LLM client layer (Groq OpenAI-compat) with fixture replay | C | — |
| Planner agent: structured plan from incident context + precedent | C | **Yes** — each task passes the existing gate |
| Explainer agent: natural-language justification of a completed plan | C | No — read-only artifact |
| Report Generator agent: executive summary of a resolved incident/group | C | No — read-only artifact |
| SQL precedent retrieval (explainable WHERE clause, no embeddings) | C | — |
| Engine integration: planner produces a second candidate alongside the playbook | A | — |
| Fixture LLM responses for the Bengaluru Storm scenario | C | — |
| API surface: `GET /incidents/{id}/explanation`, real `GET /reports/{id}` | C | — |
| Frontend: wire Report + Explanation + show generator on plans | D | — |
| End-to-end verification in all three LLM modes | C | — |

## Architectural boundaries

1. **The deterministic playbook is the FIRST plan, always.** The planner produces a second candidate.
   With `LLM_MODE=off` the system behaves exactly as it does today. No regression.

2. **No new planner, registry, tool adapter, plan representation or reasoning seam.** The planner
   returns `PlannerResponse` (already defined in `agents/contract.py`), which contains `PlanTask[]`
   validated against the closed `ActionType` enum. The existing `candidates.py` persists it as a
   `Plan` row with `generator='planner-agent'`.

3. **Every planner-proposed task passes the existing Decision Assurance Gate.** No bypass, no
   override, no new path to execution.

4. **Explainer and Report Generator are read-only artifacts.** They produce `ExplanationResponse`
   and `ReportResponse` respectively. Neither enters assurance and neither triggers any write
   beyond storing the artifact on the plan/incident.

5. **Precedent retrieval is SQL-based and explainable.** A `WHERE` clause over airport, trigger type,
   severity and outcome status. The match reason is recorded. No vector store, no embeddings.

6. **Fixture mode replays deterministic committed JSON responses.** The fixture path is the test
   oracle, not a degraded mode — it proves the integration is wired correctly without requiring an
   API key or network access.

## LLM client contract

```python
# backend/app/llm/client.py
class LLMClient:
    async def call(
        self, *, prompt: str, system: str, response_schema: type[BaseModel],
        agent_name: str, prompt_version: str,
    ) -> tuple[BaseModel, ModelCallAudit]:
        """
        LLM_MODE=live:    calls Groq, validates response against schema
        LLM_MODE=fixture: replays committed JSON keyed by (agent_name, prompt_version, scenario)
        LLM_MODE=off:     raises LLMUnavailable immediately
        """
```

## Planner integration point (Stream A budget-minimal)

In `Orchestrator.propose_tasks`:
1. After the playbook plan is created and persisted (unchanged).
2. If `LLM_MODE != off`: call `PlannerAgent.propose(ctx)`.
3. Persist the result as a second `Plan` row with `generator='planner-agent'`, `variant_key='planner'`.
4. If the call fails or returns malformed output: log, journal, continue — the playbook plan suffices.

This is ~20 lines in the engine, guarded by a mode check.

## Precedent retrieval shape

```python
@dataclass
class PrecedentMatch:
    incident_id: int
    incident_reference: str
    airport_icao: str
    trigger_type: str
    severity: str
    outcome_state: str
    match_reasons: list[str]   # e.g. ["same airport VOBL", "same trigger weather", "resolved"]
```

## Explainer/Reporter triggers

- **Explainer**: called when an incident reaches `resolved`, if `LLM_MODE != off`. Stored as
  `model_artifact` on the plan. Endpoint: `GET /incidents/{ref}/explanation`.
- **Report Generator**: called when a group reaches `resolved`, if `LLM_MODE != off`. Stored as
  `model_artifact` on the group. Endpoint: `GET /reports/{ref}`.

Both are optional: a missing artifact returns 404 with a message naming the mode, not an error.

## Verification gate

The same disruption completes in all three LLM modes:
- `off`: exactly the Phase 2 journey (1 plan variant, playbook only)
- `fixture`: 2+ plan variants (playbook + planner), explanation + report generated
- `live`: same as fixture but with a real Groq call (requires `GROQ_API_KEY`)

The existing 1570+ tests must still pass in `off` mode. The fixture path adds new contract tests.
