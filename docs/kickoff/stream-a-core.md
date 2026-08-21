# Account 1 — Stream A · Core

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream A — Core. You own the deterministic control plane.

READ FIRST (in this order):
  .kiro/steering/travelops.md          - loaded automatically; these rules are binding
  docs/26-implementation-contracts.md  - API surface, state machine, invariants
  docs/01-architecture.md              - where the orchestrator sits
  docs/02-disruption-flow.md           - the end-to-end path you are wiring
  docs/25-evaluation-readiness.md      - my definition of done

I OWN ONLY THESE PATHS:
  backend/app/orchestrator/
  backend/app/events/
  backend/app/api/
  backend/app/config.py
  backend/app/main.py
  backend/app/cli.py
  backend/app/llm/
  backend/tests/unit/orchestrator/
Do not create or modify anything outside them. If you need a change elsewhere, tell me and
I will raise it with the owning stream. Never touch backend/app/models/, migrations/,
policy_packs/, backend/app/services/, backend/app/assurance/, frontend/, docker-compose.yml
or .kiro/steering/.

BRANCH: stream/a/orchestrator
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - app/orchestrator/state.py     TRANSITIONS table + assert_transition() are COMPLETE
  - app/orchestrator/limits.py    step budget enforcement is COMPLETE
  - app/config.py                 fail-closed mode resolution is COMPLETE
  - app/events/types.py           nine typed events are COMPLETE
  - app/api/health.py             health/ready/system-mode are COMPLETE
  - app/api/fixtures_router.py    serves fixtures; you replace endpoints one at a time

YOUR WORK, IN THIS ORDER:

1. Redis Streams event bus in app/events/bus.py.
   Publish and consume the existing typed events from app/events/types.py.
   Consumers must be idempotent by event_id. Do not add fields to the event types.

2. Orchestrator engine in app/orchestrator/engine.py (currently NotImplementedError).
   - open_incident(): create an incident, or return the existing active one. The partial
     unique index uq_incident_active_per_thing makes a duplicate a database error rather
     than a race - catch it and return the existing incident.
   - propose_tasks(): deterministic fallback playbook FIRST. Reasoning agents arrive in
     Phase 3; with LLM_MODE=off the fallback must still produce a usable plan.
   - assure(): call app.assurance.gate.evaluate (Stream B). Until B lands, treat an
     unimplemented gate as a hard block, never as a pass.
   - execute(): refuse when assurance.executable is False. When the gate returned
     needs_human, require an approved human_decision row for that same evaluation.
   - advance(): the run loop, honouring the state machine and limits.

3. Replace fixture endpoints with real ones, one at a time, in app/api/.
   The response SHAPES are contractual - fixtures/api/*.json and the frontend both depend
   on them. Keep them byte-compatible. Move each endpoint out of fixtures_router.py as you
   implement it.

4. Implement the CLI stubs in app/cli.py: inject and demo-reset.
   Injecting the same scenario twice must not open a second incident.

5. Prompt files in app/llm/prompts/ as versioned .md files (planner.v1.md etc). Never
   inline a prompt string in Python. Read app/llm/prompts/README.md first.

NON-NEGOTIABLE:
  - Execution is authorised ONLY by the Decision Assurance Gate. There is no other path.
  - Never gate on an LLM-reported confidence value. It is not in any contract.
  - Every mutation carries an Idempotency-Key; a replay returns the original result.
  - Every step appends to decision_log with actor, correlation_id and evidence.
  - Missing safety config fails closed. Never silently degrade.
  - Every response carrying external or seeded data includes the provenance contract.

DEFINITION OF DONE:
Injecting the bengaluru_storm fixture opens exactly one incident, walks the state machine
to a terminal state with LLM_MODE=off, and writes an ordered immutable record per step.
Unit tests cover illegal transitions, idempotent replay, and limit breaches.

Start by reading the files listed above, then tell me your plan for step 1 before coding.
```
