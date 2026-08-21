# Account 3 — Stream C · Data, Providers & Services

Paste everything inside the block. Nothing to edit.

**Give this stream your second highest token limit.** It has roughly twenty implementation
units — eleven services, four providers with fixture twins, the loaders and the generators —
and the generators in particular take several correction rounds because they must work
backwards to hit exact target counts.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream C of four — Data, Providers & Services. You own everything below the control
plane: the schema, the seeded dataset, every boundary to the outside world, and the ten
deterministic services that do the actual work. None of your code may touch a language
model.

READ FIRST (in this order):
  .kiro/steering/travelops.md          - binding rules
  docs/11-data-model.md                - the schema you own
  docs/12-synthetic-data-plan.md       - what to generate and the exact fixture targets
  docs/10-data-sources.md              - which sources are validated and which are not
  docs/03-agent-design.md              - the 1 + 3 + 10 taxonomy and why services are not agents
  docs/06-ai-vs-deterministic.md       - what a model may never decide
  docs/22-crew-pairing-model.md        - the reasoning behind the crew cascade
  docs/28-parallel-workstreams.md      - who owns what across the four accounts
  data/fixtures/bengaluru_storm.yaml   - the scenario your generators must satisfy
  backend/app/services/base.py         - the ServiceResult contract you return
  backend/app/providers/base.py        - the Protocols you implement against

There are reusable procedures in .kiro/skills/. Use them instead of inventing your own:
  implement-service  - the required shape of a service, its evidence refs and its tests
  add-provider       - how to add a provider with a live and a fixture implementation
  verify-before-commit - the exact checks to run before every commit
  open-stream-pr     - branch, title and review conventions

I OWN ONLY THESE PATHS:
  backend/app/models/
  backend/app/db/
  backend/app/providers/
  backend/app/services/
  backend/app/memory/
  backend/migrations/
  backend/alembic.ini
  data/
  fixtures/
  backend/tests/unit/services/
  backend/tests/contract/
I may READ the whole repository. I may WRITE only inside those paths. If a change is needed
elsewhere, tell me and I will raise it with the owning stream.

The other three streams own, and I never edit:
  Stream A  backend/app/{orchestrator,events,api,agents,llm,observability,schemas}/,
            config.py, main.py, cli.py, docker-compose.yml, Makefile, .kiro/, docs/
  Stream B  backend/app/assurance/, backend/app/policy/, policy_packs/, config/
  Stream D  all of frontend/

I may EXTEND backend/tests/unit/test_crosswind.py as I build Delay Risk. Its existing
assertions are frozen, and so are the other shared guard tests: the four remaining files
directly under backend/tests/unit/, plus backend/tests/contract/test_container_runtime_paths.py
which sits INSIDE a directory I own. I am the one stream that must consciously treat a file it
owns as frozen.
test_no_llm_in_services.py constrains my own code and covers app/services/, app/assurance/,
app/policy/ and app/orchestrator/. If it fails, my import is wrong; I never edit the test to
permit it.

I AM THE ONLY STREAM PERMITTED TO GENERATE MIGRATIONS. Two streams autogenerating produces
unorderable heads, which is the classic way parallel work dies. Streams A, B and D send me
schema requests; I write the migration.

I also own fixtures/api/*.json, which are CONTRACTUAL. Stream A's real endpoints must stay
byte-compatible with them and Stream D renders them directly. Changing a fixture shape
breaks two other streams, so it is a deliberate, announced change - never a quick edit to
make my own code pass.

BRANCH: stream/c/data-services
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main. Never merge my own PR.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - all 33 models in app/models/ are COMPLETE and verified
  - migrations/versions/0001_initial_schema.py is COMPLETE and renders valid Postgres DDL
    for 34 tables. Generate NEW revisions on top; never edit it.
  - app/db/session.py and base.py are COMPLETE
  - app/providers/base.py Protocols are COMPLETE - implement against them, do not change them
  - app/services/base.py ServiceResult contract is COMPLETE
  - app/services/delay_risk.py: crosswind_component_kt and headwind_component_kt are
    COMPLETE and tested in backend/tests/unit/test_crosswind.py. Only execute() is a stub.
  - all ten service files exist as stubs with required-behaviour docstrings
  - fixtures/api/*.json exist as the contractual response shapes
  - ./fixtures is mounted read-only into the api and web containers at /fixtures. Both
    frontend/scripts/sync-fixtures.mjs and backend/app/api/fixtures_router.py resolve to
    /fixtures/api. tests/unit/test_container_runtime_paths.py guards this - if it fails,
    the container mount is the problem, not the test.

=== PHASE 1 — DATA FOUNDATION. Everything else depends on this. ===

1. Public reference loaders in data/loaders/.
   OurAirports airports + runways for the ten-airport set. Archive the snapshot and record
   its hash. Runway true headings are essential - crosswind scoring is meaningless without
   them, and the crosswind function is already written and waiting for real headings.

2. Weather provider in app/providers/weather/. TWO implementations behind the existing
   Protocol: live (Aviation Weather Center METAR/TAF, no API key) and fixture (committed
   snapshot). Normalise units AT THE BOUNDARY: store knots, metres, feet. Never km/h - a
   45 km/h reading mistaken for 45 kt would silently invalidate every risk score. Return
   the provenance contract including observation age.

3. Synthetic generators in data/generators/, fixed seed 20260807, working BACKWARDS from
   the targets in data/fixtures/bengaluru_storm.yaml:
     8 affected flights, ~604 passengers, 22 at-risk connections, 11 candidate hotels,
     and EXACTLY 9 traceable crew pairings.
   Model pairings explicitly: pairing -> pairing_leg -> flight, with role operating or
   positioning. A flat crew-to-flight column would make the 8-flights/9-rotations claim
   indefensible. Each affected pairing must be attributable to exactly one mechanism:
   operating, onward_duty, second_pairing or positioning.
   Make hotel capacity deliberately insufficient for at least one allocation, so partial
   allocation and prioritisation are actually exercised rather than assumed.
   THIS IS THE HARDEST PART OF THE STREAM. Expect to iterate. Write the assertion that
   counts 9 pairings before you write the generator that satisfies it.

4. Flight status simulator in app/providers/flight_status/. Deterministic state machine.
   No external feed - none has been validated under our constraints.

5. Notification provider in app/providers/notifications/: console, mailtrap and SMTP.
   Real sends go ONLY to the DEMO_RECIPIENT_ALLOWLIST. Everything else writes a
   notification row with delivery_mode=simulated. Three real emails and 601 simulated is
   honest; implying all 604 were delivered is not.

6. Commit the dataset dump. Never regenerate during a demo.
   Expose seed and reset functions for Stream A's CLI to import. Send them the signatures;
   do not edit app/cli.py yourself.

=== PHASE 2 — THE FOUR SERVICES THAT CARRY THE STAGE 2 DEMO. Do these next. ===

7. Delay Risk (app/services/delay_risk.py execute()).
   Use the existing crosswind function - do not rewrite it. Combine wind, crosswind against
   runway heading, visibility, ceiling and precipitation into a risk INDEX 0-100 and a
   LEVEL (low/elevated/high/severe), plus named contributing factors and RULE_VERSION.
   It is NOT a probability. Nothing here is calibrated against observed outcomes, so
   "87% chance of delay" would be an unearned claim. Thresholds come from config; never
   hardcode a number.

8. Connection. Compare the revised arrival of the delayed segment against the scheduled
   departure of the next segment on the same booking, allowing minimum connection time.
   Return exact booking and segment references so the count of 22 is traceable, not
   asserted.

9. Crew Impact. Walk pairing legs FORWARD from affected flights. Report each affected
   pairing with the mechanism that put it at risk: operating, onward_duty, second_pairing
   or positioning. That mechanism becomes the edge label in Stream D's cascade graph, which
   is what lets a reviewer read why nine rotations are affected by eight flights.
   SCOPE BOUNDARY: coordination and display ONLY. Never validate duty-time legality and
   never generate a legal replacement roster. Getting that subtly wrong is worse than not
   doing it, and claiming it would invite a question we cannot answer.

10. Communication. Render approved templates and dispatch through the notification provider
    from step 5. Real sends only to the allowlist; everything else records as simulated.

=== PHASE 3 — THE REMAINING SIX. Defer these if quota or time runs short. ===

11. Compensation FIRST among the deferred six, because it is what makes Stream D's policy
    screen live. It assembles facts and calls Stream B's policy engine. It must NEVER
    compute an entitlement itself and never infer a legal outcome from trigger_type. It
    returns whatever the engine returns, including needs_human when a fact is missing.

12. Then Flight Recovery, Hotel (budget cap and partner preference from business_constraint
    rows, never literals), Transport, Gate/Resource, and Analytics/Learning.
    ANALYTICS IS SPECIAL: aggregate from recorded rows only. Never invent a metric the
    records cannot support, and never describe gate outcomes as ground truth - they are
    policy decisions, not observed reality.

NON-NEGOTIABLE:
  - NOTHING under app/services/ may import groq, openai, anthropic, litellm, ollama or
    app.llm. An AST test enforces this (tests/unit/test_no_llm_in_services.py). This
    boundary is the architecture, not a style preference.
  - A service does not decide whether it is allowed to run. Stream A's orchestrator asks
    the Decision Assurance Gate first, then dispatches to me.
  - Every service returns ServiceResult with evidence_refs and provenance_kind.
  - Deterministic means reproducible: identical input yields identical output.
  - Thresholds, budgets and limits come from config or the database. No magic numbers.
  - Every provider needs a fixture/offline implementation. An unavailable vendor API must
    never be able to block a checkpoint demo.
  - Every datum carries provenance: real | simulated | synthetic | fixture | unavailable.
  - Passengers are synthetic and visibly so on inspection: PAX-00001, @example.com. There
    is no code path that stores real personal data.
  - Schedules are SYNTHETIC and must be labelled so, unless you download the AIKosh file,
    archive it with its licence, and a loader contract test passes. Do not call them real
    before that.
  - Provider errors are typed (unavailable, timeout, rate_limited, invalid_response,
    forbidden). Never map a failure to silent success.

DEFINITION OF DONE:
`make seed` produces a byte-identical dataset for seed 20260807. A recursive query over
pairing_leg returns exactly 9 affected pairings for the storm fixture, each traceable to a
mechanism. The weather provider passes contract tests in both live and fixture modes. The
four Phase 2 services have unit tests covering their thresholds and boundary conditions,
Delay Risk output is reproducible for identical input, and the no-LLM-import test passes.

Start by reading docs/12-synthetic-data-plan.md and the storm fixture, then tell me your
plan for the pairing generator - that is the part most likely to go wrong, so I want to see
it before any code is written.
```
