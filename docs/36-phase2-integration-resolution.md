# Phase 2 integration — resolving four parallel implementations

Streams A, B, C and D each landed Phase 2 work, and two integrations were attempted in parallel
(#52/#53 on `main`, and #54 from `stream/integration/phase2`). This records how the overlap was
resolved and what running the journey end to end found that four green suites had not.

Nothing here was resolved by taking one side wholesale.

## Ownership, and what was dropped

`main` is authoritative for architecture. Where the two implementations overlapped, `main`'s was
better owned and in three places better *reasoned*, so the parallel versions were deleted rather
than merged:

| Surface | Kept | Deleted |
| --- | --- | --- |
| Group API | `api/incident_groups.py`, `api/plans.py`, `api/replay.py`, `api/actors.py` | `api/groups.py` |
| Group schemas | `schemas/cascade.py`, `schemas/plans.py`, `schemas/replay.py` | `schemas/groups.py` |
| Group orchestration | `orchestrator/group.py` + derived `group_state.py` | `orchestrator/group_engine.py` |
| Plan approval | `orchestrator/plan_approval.py`, `plan_assurance.py`, `candidates.py` | `orchestrator/plan_lifecycle.py` |

Three of `main`'s decisions are better than the alternative and are worth stating, because each was
a live disagreement:

**Two config paths, not one.** The parallel version repointed `assurance_config_path` at v2. That
works for the action gate but silently moves `config_version` on every new `assurance_evaluation`
from `assurance-v1` to `assurance-v2` — and a Phase 1 record must stay interpretable under the
semantics it was decided by. `main` keeps `assurance_config_path` on v1 and adds a separate
`plan_config_path` for v2. Two identities, each recorded on the decision it governed. As a
consequence, the `plan`/`what_if` fields the parallel version added to `AssuranceConfig` were
reverted: they are unnecessary once the action gate never loads v2.

**The engine knows nothing about plan approvals.** The parallel version taught `execute()` a second
authorisation route. `main` instead has the approval service write a `human_decision` with
`scope='plan'` per covered evaluation (migration `0008`), so a plan approval arrives at the existing
`_human_decision(evaluation_id)` lookup as an ordinary decision. One seam instead of two, and
`test_phase2_guards` asserts the engine never imports `plan_approval`.

**A derived group state machine.** `group_state.py` derives group state from member states rather
than storing it, and encodes the rule the parallel version lacked: **seven resolved of eight is
`blocked`, not `resolved`**, and a group is never `failed` as a unit.

`app/db/plan_identity.py` was restored: `main`'s `candidates.py` uses `compute_plan_hash` to stamp
`plan.plan_hash` on candidate plans, while Stream B's `PlanUnderReview.hash()` is what binds an
approval. Two hashes for two different jobs — the stored candidate's identity and the approval's
binding — which is defensible, but it is a divergence a future change should not deepen without
deciding deliberately.

## What the journey found that the component suites did not

Seven bugs. Every one produced a plausible number rather than an error, and none was visible from
inside the component that caused it.

**The hotel search looked in the wrong city.** `_origin_airport` took the first member flight's
`origin_icao` with `LIMIT 1` and no `ORDER BY` — non-deterministic, and simply the wrong airport for
an arrival, since UK 705 flies VAAH to VOBL while its passengers are stranded in Bengaluru. The
search then reported *0 properties within the rate cap*, which is indistinguishable from every hotel
being full. Now read from `incident_group.airport_icao`.

**One of eight flights had no risk assessment.** `_assess_delay_risk` read the flight's origin
weather. There is no observation for Ahmedabad, so the cascade drew seven root-cause edges for eight
declared flights — a flight in the picture with nothing explaining why it was there. Now assessed
against the group's airport.

**`hotel_inventory_hold` was never written.** `place_holds` existed with no caller, so
`load_hotel_options` — which computes availability as `total_rooms` minus active holds — always saw a
full hotel. Eight flights each allocated the same 71 rooms and the shortfall never appeared. The
ledger is the entire reason availability is derived rather than a mutated counter, and a ledger
nothing writes to *is* a counter that never decrements.

**The blast radius carried no accommodation figures.** `compose_blast_radius` accepts a
`hotel_payload` and no caller passed one, so a disruption 232 rooms short reported nothing about
rooms. Fixed by adding `group_hotel_totals()` to Stream C's `services/hotel.py` and calling it from
the group API — the summing lives in the service because `test_phase2_guards` forbids aggregating an
action payload in the transport layer, and rightly: "just sum the counts" is how 22 distinct at-risk
bookings become 176.

**A service asking for a person was treated as a service that broke.** `reserve_hotel_block` secures
71 of 87 rooms and reports `needs_human` with a named shortfall. `_step_executing` blocked the
incident, abandoning the rooms already held *and* stopping the connection, crew and notification work
for 604 passengers because 32 lacked a bed. The engine now distinguishes a refusal that **carries
provenance** from one that does not: a `failure`, a skip, or a `provenance_kind="unavailable"`
refusal still blocks, so an unimplemented service is never quietly filed as an outstanding item.

Fixing that exposed a deadlock behind it. `plan_task.state = needs_human` meant both "the gate is
holding this" and "a service wants a person", and `_pending_approval` keyed off the task state — so
after the shortfall the engine waited forever for a decision on an evaluation that had already said
`execute` and whose action had already run. "Pending approval" is now defined by the **evaluation's**
decision.

**`opened_incident_ids` lied on a repeat open.** `open_group` is idempotent by construction, but it
reported every member incident as newly opened, so a second click told the operator it had opened
eight incidents again. The cascade was intact and the report of it was false, which is worse than a
visible error because nothing looks wrong.

**`reserve_hotel_block` was registered but never planned.** The adapter was in `STAGE2_ADAPTERS` and
absent from the playbook, so no room was ever held and the cascade showed no accommodation edge.
Added behind `find_hotel_options`, as its own step: search commits nothing, allocation takes rooms
off the market, and those are two decisions with two pieces of evidence.

## Console

Kept `main`'s server projection (`layoutServerGraph`) and its screens. Added the two capabilities
whose endpoints existed with no caller, so the journey could not be driven from the browser at all:

- **`GroupRunControl`** — open and advance, with the completeness banner always visible. It states
  "approving nothing — each flight keeps its own gate", because that is the architecture.
- **`WhatIfPanel`** — controls built from the server's `levers_available`, with `wrote_rows`,
  `permitted`, `seed` and the verbatim `boundary_note` all on screen.

Four rendering defects only a real screen at 1920×1080 shows, all fixed:

- **Edge captions overlapped into a solid band.** `GraphEdge` captioned every edge unconditionally,
  and `layoutServerGraph` falls back to `edge_kind` when there is no mechanism, so eight converging
  edges read `ROOT CAUSE ROOT CAUSE ROOT CAUSE…`. Captions now draw only on emphasis, and never when
  the "mechanism" is just the edge's own kind — that label carried no information anyway.
- **Node captions overlapped** across 22 booking and 9 pairing nodes sharing one depth row. Captions
  now draw for the trigger and the flights, and on selection for the rest; names stay on
  `aria-label` and every record is in the table below.
- **Flight sublabels ran together** at eight across. Drawn only when the row is short or the node is
  selected; the route and delay are both in the hop expansion and the flights table.
- **`->` rendered as a tofu box.** `Inter` and `JetBrains Mono` are webfonts and the fallback has no
  U+2192 or U+20B9. Display strings are ASCII, and a contract test asserts the API emits neither.

`npm run typecheck` was `tsc --noEmit` against a root config with `"files": []` and only project
references: it checked nothing and exited 0 while the app had eleven type errors. Now `tsc -b`.

## Verification

| Claim | How |
| --- | --- |
| The whole journey, no stubs | `tests/contract/test_real_group_journey.py` — 21 tests, real app + real Postgres |
| 8 / 604 / 22 / 11 / 9 | asserted there and re-checked through the browser |
| 303 rooms required, 232 short, 71 held | asserted from the blast radius and from `hotel_inventory_hold` |
| Plan approval never covers high risk | the approval preview, asserted through the API |
| What-if writes nothing | row census over every table in `Base.metadata` before and after |
| Console at 1920×1080 | `agent-browser` against the real API on five routes: no horizontal overflow, 0 page errors, 0 unrenderable glyphs |
| Demo path | `scripts/verify_demo.py` 13/13 against the live API |

`pythonpath = ["..", "."]` was added to `backend/pyproject.toml`. `data.generators` was importable
only because the first `app.db.seed` import happened late enough for a conftest `sys.path` insertion
to have run; adding a conftest to `tests/contract/` moved that import earlier and the accident
stopped holding. The shared real-app harness now lives in `tests/contract/conftest.py` so two
contract tests cannot drift apart on what "nothing is stubbed" means.
