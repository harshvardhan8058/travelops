# Account 1 — Stream A · Core & API

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream A of four — Core & API. You own the deterministic control plane: the
orchestrator, the event bus, every real HTTP endpoint, the CLI, the reasoning agents and
the shared project configuration.

READ FIRST (in this order):
  .kiro/steering/travelops.md          - loaded automatically; these rules are binding
  docs/26-implementation-contracts.md  - API surface, state machine, invariants
  docs/01-architecture.md              - where the orchestrator sits
  docs/02-disruption-flow.md           - the end-to-end path you are wiring
  docs/28-parallel-workstreams.md      - who owns what across the four accounts
  docs/25-evaluation-readiness.md      - my definition of done

There are reusable procedures in .kiro/skills/. Use them instead of inventing your own:
  add-api-endpoint      - the required shape for a new endpoint, including response_model
  verify-before-commit  - the exact checks to run before every commit
  open-stream-pr        - branch, title and review conventions

I OWN ONLY THESE PATHS:
  backend/app/orchestrator/
  backend/app/events/
  backend/app/api/
  backend/app/agents/
  backend/app/llm/
  backend/app/observability/
  backend/app/schemas/
  backend/app/config.py
  backend/app/main.py
  backend/app/cli.py
  backend/app/errors.py
  backend/pyproject.toml
  backend/Dockerfile
  backend/.dockerignore
  backend/.python-version
  backend/tests/unit/orchestrator/
  backend/tests/e2e/
  backend/tests/unit/*.py          - the shared guard tests; see the rule below
  docker-compose.yml
  Makefile
  .env.example
  .gitignore
  README.md
  .kiro/
  docs/
  scripts/
I may READ the whole repository. I may WRITE only inside those paths. If a change is needed
elsewhere, tell me and I will raise it with the owning stream.

The other three streams own, and I never edit:
  Stream B  backend/app/assurance/, backend/app/policy/, policy_packs/, config/
  Stream C  backend/app/{models,db,providers,services,memory}/, backend/migrations/,
            data/, fixtures/
  Stream D  all of frontend/

I own .kiro/steering/travelops.md and .kiro/skills/. Both change the behaviour of all four
sessions, so I only edit them with the team's agreement, never unilaterally mid-slice.

THE SHARED GUARD TESTS. The .py files directly under backend/tests/unit/ - not the
per-stream subdirectories - are cross-stream invariant guards: test_no_llm_in_services.py,
test_state_machine.py, test_contracts.py, test_config_fail_closed.py,
test_container_runtime_paths.py, test_crosswind.py. They are what stop an architectural
boundary being crossed by accident.
  - Any stream may ADD a guard test.
  - NO stream may weaken or delete an existing assertion, including me. If a guard test
    fails, the code is wrong, not the test. Relaxing one is a whole-team decision.
  - test_crosswind.py is delegated to Stream C to EXTEND as it builds Delay Risk. Its
    existing assertions are still frozen.

BRANCH: stream/a/core
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main. Never merge my own PR.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - app/orchestrator/state.py     TRANSITIONS table + assert_transition() are COMPLETE
  - app/orchestrator/limits.py    step budget enforcement is COMPLETE
  - app/config.py                 fail-closed mode resolution is COMPLETE
  - app/events/types.py           nine typed events are COMPLETE
  - app/api/health.py             health/ready/system-mode are COMPLETE
  - app/schemas/provenance.py     the provenance contract is COMPLETE
  - app/api/fixtures_router.py    serves nine fixture endpoints; you replace them one at a
                                  time as the real implementation lands
  - docker-compose.yml            api, web, postgres and redis all start and are healthy.
                                  ./fixtures is mounted read-only into both api and web.
                                  Do not change the mounts without re-running
                                  tests/unit/test_container_runtime_paths.py

YOUR WORK, IN THIS ORDER:

1. Redis Streams event bus in app/events/bus.py.
   Publish and consume the existing typed events from app/events/types.py.
   Consumers must be idempotent by event_id. Do not add fields to the event types.

2. Orchestrator engine in app/orchestrator/engine.py (currently NotImplementedError).
   - open_incident(): create an incident, or return the existing active one. The partial
     unique index uq_incident_active_per_thing makes a duplicate a database error rather
     than a race - catch it and return the existing incident.
   - propose_tasks(): deterministic fallback playbook FIRST. Reasoning agents arrive in
     step 6; with LLM_MODE=off the fallback must still produce a usable plan.
   - assure(): call app.assurance.gate.evaluate (Stream B). Until B lands, treat an
     unimplemented gate as a hard block, never as a pass.
   - execute(): refuse when assurance.executable is False. When the gate returned
     needs_human, require an approved human_decision row for that same evaluation.
   - advance(): the run loop, honouring the state machine and limits.

3. Replace fixture endpoints with real ones, one at a time, in app/api/.
   The response SHAPES are contractual - fixtures/api/*.json and Stream D's screens both
   depend on them. Keep them byte-compatible. Move each endpoint out of fixtures_router.py
   as you implement it, and delete the fixture route in the same commit so there is never
   a period where both exist.
   Every new endpoint MUST declare a Pydantic response_model. The fixture routes return
   Any, which is why the current OpenAPI document renders "string" for their schemas. Fix
   that as you go; do not carry the pattern forward. See the add-api-endpoint skill.
   If a response shape genuinely has to change, that is a request to Stream C, who own
   fixtures/. Do not edit the fixture yourself to match your code.

4. Implement the CLI stubs in app/cli.py: inject and demo-reset.
   Injecting the same scenario twice must not open a second incident.
   Stream C owns the seed and reset internals - import the functions they expose, and ask
   them for a signature change rather than editing data/ yourself.

5. Structured logging and correlation IDs in app/observability/. Every request, event and
   decision carries the same correlation_id end to end, and the UI surfaces it on errors.

6. Reasoning agents in app/agents/ and prompt files in app/llm/prompts/ as versioned .md
   files (planner.v1.md etc). Never inline a prompt string in Python. Read
   app/llm/prompts/README.md first. An agent proposes; it never authorises.

NON-NEGOTIABLE:
  - Execution is authorised ONLY by the Decision Assurance Gate. There is no other path.
  - Never gate on an LLM-reported confidence value. It is not in any contract.
  - Every mutation carries an Idempotency-Key; a replay returns the original result.
  - Every step appends to decision_log with actor, correlation_id and evidence.
  - Missing safety config fails closed. Never silently degrade.
  - Every response carrying external or seeded data includes the provenance contract.
  - The API states which generator produced a plan: model and prompt version, or
    deterministic fallback. A judge must never have to guess whether a model was involved.

DEFINITION OF DONE:
Injecting the bengaluru_storm fixture opens exactly one incident, walks the state machine
to a terminal state with LLM_MODE=off, and writes an ordered immutable record per step.
Unit tests cover illegal transitions, idempotent replay, and limit breaches. Every endpoint
I have moved out of fixtures_router.py has a response_model and its OpenAPI schema is no
longer "string".

Start by reading the files listed above, then tell me your plan for step 1 before coding.
```
