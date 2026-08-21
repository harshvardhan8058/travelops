# Account 3 — Stream C · Data + Providers

Paste everything inside the block. Nothing to edit.

```text
You are working on TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. The Wave 0 bootstrap is already on main and runs.

You are Stream C — Data + Providers. You own the schema, the seeded dataset, and every
boundary to the outside world.

READ FIRST (in this order):
  .kiro/steering/travelops.md          - binding rules
  docs/11-data-model.md                - the schema you own
  docs/12-synthetic-data-plan.md       - what to generate and the fixture targets
  docs/10-data-sources.md              - which sources are validated and which are not
  data/fixtures/bengaluru_storm.yaml   - the scenario your seeders must satisfy

I OWN ONLY THESE PATHS:
  backend/app/models/
  backend/migrations/
  backend/app/providers/
  backend/app/db/
  data/
  fixtures/
  backend/tests/contract/
Do not create or modify anything outside them. If you need a change elsewhere, tell me and
I will raise it with the owning stream. Never touch backend/app/orchestrator/,
backend/app/assurance/, backend/app/policy/, backend/app/services/ or frontend/.

YOU ARE THE ONLY STREAM PERMITTED TO GENERATE MIGRATIONS. Two streams autogenerating
produces unorderable heads, which is the classic way parallel work dies.

BRANCH: stream/c/data-providers
Commit small working increments. Run `cd backend && uv run pytest` before every commit.
Never push to main.

ALREADY DONE IN WAVE 0 - DO NOT REBUILD:
  - all 33 models in app/models/ are COMPLETE and verified
  - migrations/versions/0001_initial_schema.py is COMPLETE (renders valid Postgres DDL)
  - app/db/session.py and base.py are COMPLETE
  - app/providers/base.py Protocols are COMPLETE - implement against them, do not change them
  - fixtures/api/*.json exist as the contractual response shapes

YOUR WORK, IN THIS ORDER:

1. Public reference loaders in data/loaders/.
   OurAirports airports + runways for the ten-airport set. Archive the snapshot and record
   its hash. Runway true headings are essential - crosswind scoring is meaningless without
   them.

2. Weather provider in app/providers/weather/. TWO implementations behind the existing
   Protocol: live (Aviation Weather Center METAR/TAF, no API key) and fixture (committed
   snapshot). Normalise units AT THE BOUNDARY: store knots, metres, feet. Never km/h - a
   45 km/h reading mistaken for 45 kt would silently invalidate every risk score. Return
   the provenance contract including observation age.

3. Flight status simulator in app/providers/flight_status/. Deterministic state machine.
   No external feed - none has been validated under our constraints.

4. Synthetic generators in data/generators/, fixed seed 20260807, working BACKWARDS from
   the targets in data/fixtures/bengaluru_storm.yaml:
     8 affected flights, ~604 passengers, 22 at-risk connections, 11 candidate hotels,
     and EXACTLY 9 traceable crew pairings.
   Model pairings explicitly: pairing -> pairing_leg -> flight, with role operating or
   positioning. A flat crew-to-flight column would make the 8-flights/9-rotations claim
   indefensible. Each affected pairing must be attributable to one mechanism: operating,
   onward_duty, second_pairing or positioning.
   Make hotel capacity deliberately insufficient for at least one allocation, so partial
   allocation and prioritisation are actually exercised.

5. Notification provider in app/providers/notifications/: console, mailtrap and SMTP.
   Real sends go ONLY to the DEMO_RECIPIENT_ALLOWLIST. Everything else writes a
   notification row with delivery_mode=simulated. Three real emails and 601 simulated is
   honest; implying all 604 were delivered is not.

6. Implement the CLI seed and reset commands in coordination with Stream A (they own
   app/cli.py - send them the functions to call, do not edit the file yourself).

7. Commit the dataset dump. Never regenerate during a demo.

NON-NEGOTIABLE:
  - Every provider needs a fixture/offline implementation. An unavailable vendor API must
    never be able to block a checkpoint demo.
  - Every datum carries provenance: real | simulated | synthetic | fixture | unavailable.
  - Passengers are synthetic and visibly so on inspection: PAX-00001, @example.com.
    There is no code path that stores real personal data.
  - Schedules are SYNTHETIC and must be labelled so, unless you download the AIKosh file,
    archive it with its licence, and a loader contract test passes. Do not call them real
    before that.
  - Provider errors are typed (unavailable, timeout, rate_limited, invalid_response,
    forbidden). Never map a failure to silent success.

DEFINITION OF DONE:
`make seed` produces a byte-identical dataset for seed 20260807. A recursive query over
pairing_leg returns exactly 9 affected pairings for the storm fixture, each traceable to a
mechanism. The weather provider passes contract tests in both live and fixture modes.

Start by reading docs/12-synthetic-data-plan.md and the storm fixture, then tell me your
plan for the pairing generator - that is the part most likely to go wrong.
```
