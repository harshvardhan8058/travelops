# Account 4 — Stream D · Deterministic Services

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream D — Deterministic Services. You own the ten services that actually do the
work. None of them may touch a language model.

READ FIRST (in this order):
  .kiro/steering/travelops.md      - binding rules
  docs/03-agent-design.md          - the 1 + 3 + 10 taxonomy and why services are not agents
  docs/06-ai-vs-deterministic.md   - what a model may never decide
  docs/22-crew-pairing-model.md    - the reasoning behind the crew cascade
  backend/app/services/base.py     - the ServiceResult contract you return

I OWN ONLY THESE PATHS:
  backend/app/services/
  backend/app/memory/
  backend/tests/unit/services/
Do not create or modify anything outside them. If you need a change elsewhere, tell me and
I will raise it with the owning stream. Never touch backend/app/models/, migrations/,
backend/app/orchestrator/, backend/app/assurance/, backend/app/policy/ or frontend/.

BRANCH: stream/d/services
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - app/services/base.py                      ServiceResult contract is COMPLETE
  - app/services/delay_risk.py                crosswind_component_kt and
                                              headwind_component_kt are COMPLETE and tested.
                                              Only execute() is a stub.
  - all ten service files exist as stubs with required-behaviour docstrings
  - models, enums and provider Protocols all exist

YOUR WORK, IN THIS ORDER. The first four carry the Stage 2 demo:

1. Delay Risk (app/services/delay_risk.py execute()).
   Use the existing crosswind function - do not rewrite it. Combine wind, crosswind against
   runway heading, visibility, ceiling and precipitation into a risk INDEX 0-100 and a
   LEVEL (low/elevated/high/severe), plus named contributing factors and RULE_VERSION.
   It is NOT a probability. Nothing here is calibrated against observed outcomes, so
   "87% chance of delay" would be an unearned claim. Thresholds come from config; never
   hardcode a number.

2. Connection. Compare the revised arrival of the delayed segment against the scheduled
   departure of the next segment on the same booking, allowing minimum connection time.
   Return exact booking and segment references so the count is traceable, not asserted.

3. Crew Impact. Walk pairing legs FORWARD from affected flights. Report each affected
   pairing with the mechanism that put it at risk: operating, onward_duty, second_pairing
   or positioning. That mechanism becomes the edge label in the cascade graph, which is
   what lets a reviewer read why nine rotations are affected by eight flights.
   SCOPE BOUNDARY: coordination and display ONLY. Never validate duty-time legality and
   never generate a legal replacement roster. Getting that subtly wrong is worse than not
   doing it.

4. Communication. Render approved templates and dispatch through the notification provider.
   Real sends only to the allowlist; everything else records as simulated.

Then: Flight Recovery, Hotel (budget cap and partner preference from business_constraint
rows, never literals), Transport, Compensation, Gate/Resource, Analytics/Learning.

COMPENSATION IS SPECIAL: it assembles facts and calls Stream B's policy engine. It must
NEVER compute an entitlement itself and never infer a legal outcome from trigger_type. It
returns whatever the engine returns, including needs_human when a fact is missing.

ANALYTICS IS SPECIAL: aggregate from recorded rows only. Never invent a metric the records
cannot support, and never describe gate outcomes as ground truth - they are policy
decisions, not observed reality.

NON-NEGOTIABLE:
  - NOTHING under app/services/ may import groq, openai, anthropic, litellm, ollama or
    app.llm. An AST test enforces this (tests/unit/test_no_llm_in_services.py). This
    boundary is the architecture.
  - A service does not decide whether it is allowed to run. The orchestrator asks the
    Decision Assurance Gate first, then dispatches to you.
  - Every service returns ServiceResult with evidence_refs and provenance_kind.
  - Deterministic means reproducible: identical input yields identical output.
  - Thresholds, budgets and limits come from config or the database. No magic numbers.

DEFINITION OF DONE:
Each service has unit tests covering its thresholds and boundary conditions. Delay Risk
output is reproducible for identical input. The no-LLM-import test still passes.

Start with Delay Risk. Read the existing crosswind tests in
backend/tests/unit/test_crosswind.py first so you match the established conventions, then
tell me your proposed rule set and thresholds before coding.
```
