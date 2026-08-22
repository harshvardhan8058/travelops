# Phase 2 integration — the whole journey, end to end

What was wired together to make Bengaluru Storm run from disruption to resolved through one console,
and the decisions that came out of doing it. Written for the reviewer who wants to check a claim
rather than read a plan.

Streams A, B, C and D had each landed their Phase 2 work and each suite was green. This document is
mostly about what that turned out not to prove.

## The journey

```
GET  /api/v1/incident-groups                              8 declared flights, 604 passengers
POST /api/v1/incident-groups/{ref}/run                     opens 8 incidents, advances each
GET  /api/v1/incident-groups/{ref}/plan-assurance          one plan evaluation per member
POST /api/v1/incident-groups/{ref}/plans/{id}/approval     low/medium coverage, bound to a hash
POST /api/v1/assurance/{id}/decision                       per-action approval, unchanged
POST /api/v1/incident-groups/{ref}/run                     -> 8 resolved
GET  /api/v1/incident-groups/{ref}                         cascade graph + blast radius
POST /api/v1/incident-groups/{ref}/what-if                 bounded, zero-write re-evaluation
GET  /api/v1/incident-groups/{ref}/replay                  ordered fold over immutable records
```

The two Wave 0 fixture routes for `/incident-groups` were deleted in the same commit that made them
real, so there is never a period where two implementations of one path exist.

## What integration found that unit tests could not

Four bugs. None was visible from inside the component that caused it, and each produced a plausible
number rather than an error — which is why they are worth recording.

**The hotel search looked in the wrong city.** The disruption airport was resolved by counting how
often each ICAO appeared across the flights in scope and taking the maximum. On a single flight that
always ties — 6E 2134 is VOBL to VIDP, one each — and the tie-break picked VIDP. The search then
reported *0 properties within the rate cap*, which is indistinguishable from every hotel in
Bengaluru being full. Now read from `incident_group.airport_icao`, because the group declares where
the disruption is.

**One of eight flights had no risk assessment.** Delay risk read the flight's origin weather. UK 705
is VAAH to VOBL: the storm is at its destination, there is no observation for Ahmedabad, and the
cascade graph drew seven root-cause edges for eight declared flights — a flight in the picture with
nothing explaining why it was there. Now assessed against the group's airport.

**The blast radius reported one flight's rooms as the group's.** It took the most recent hotel
action's payload. Eight flights draw on one finite inventory, so the last allocation to run sees
only what is left: the screen said *9 rooms required, 0 short* for a disruption needing 303 rooms
against 71 available. Now summed across the group.

**A service asking for a person was treated as a service that broke.** `reserve_hotel_block` secures
71 of 87 rooms for the primary flight and reports `needs_human` with a named shortfall. The engine
blocked the incident, which abandoned the 71 rooms *and* stopped the connection, crew and
notification work for 604 passengers because 32 of them lacked a bed.

The fix is a distinction the engine did not previously draw: a `needs_human` result **that carries
provenance** is an outstanding item, and the plan continues around it; a `failure`, a skip, or a
refusal with `provenance_kind = "unavailable"` still blocks. The discriminator is
`_carries_evidence`, and it is why an unimplemented service still stops the plan instead of being
quietly filed as something for a person to look at later. The incident reaches `resolved` with
`tasks_needing_human` in its metrics and the outstanding action named in the resolve summary.

Fixing that surfaced a deadlock immediately behind it. `plan_task.state = needs_human` was doing
double duty: the gate holding a task and a service surfacing a decision both landed there and need
opposite handling. `_pending_approval` keyed off the task state, so after the hotel shortfall the
engine waited forever for a decision on an evaluation that had already said `execute` and whose
action had already run. "Pending approval" is now defined by the **evaluation's** decision.

## Cross-stream contract conflicts, and how they were settled

**Two plan hashes.** Stream B's `PlanUnderReview.hash()` (16 chars, over group + tasks) and Stream
C's `plan_identity.compute_plan_hash` (32 chars, over generator + prompt version + tasks) were both
candidates for `plan.plan_hash`. Two identities for one plan means the first time they disagree the
approval either covers work nobody reviewed or refuses work someone did. **B's is the one**, because
it is what the approval gate compares. `app/db/plan_identity.py` was deleted rather than left as a
tempting second option.

**v2 config made every action refuse.** `AssuranceConfig` is `extra="forbid"`, so the `plan:` and
`what_if:` sections v2 adds made the file unparseable for the *action* gate — which fails closed, so
switching to v2 would have refused every action in the system. Both sections are now declared on
`AssuranceConfig` as opaque mappings it never reads, keeping `extra="forbid"` doing its real job of
catching a typo in a safety setting. Typed as `dict` rather than `PlanConfig` on purpose: if the
action gate could see the plan limits it could start consulting them.

**`may_approve_plan` made plan approval unreachable.** It refuses a plan whose own decision is
`execute`, on the grounds that nothing at plan level awaits a human — correct for the question it
asks. But P2-D3 exists so a plan approval can cover *action*-level holds, and a plan can be
admissible in aggregate while several of its tasks are each held. An approval is now permitted when
either the plan itself requires a human, or the plan is admissible and carries at least one coverable
held task. The evidence rule is untouched: a task blocked on evidence makes `tasks_authorised` FAIL,
which puts a non-risk check in `blocking`, which `may_approve_plan` refuses — so the second branch
cannot be used to approve around failed evidence.

**`covered_task_ids` held the wrong ids.** They were populated from `task_evaluation_ids`. Stream B
compares `task.task_id`, so every coverage check would have failed with a confusing "not in the
approval's task list".

## The console

The Cascade Explorer used to build its own graph from the `flights` and `crew_pairings` arrays. That
put a second implementation of "which rows are related" in the browser, tested against nothing.
Topology now arrives from `GET /incident-groups/{id}` with every edge naming the `action:` or
`prediction:` row it was read from, and the frontend positions and filters it — 46 nodes and 46 edges
on the seeded scenario. Connections and rooms became real nodes rather than untraversable counts,
because the projection can point at the action that found each one.

Three quality problems that only a rendered screen shows:

- **Depth-2 labels overlapped into an unreadable smear.** 22 bookings and 9 rotations on one row.
  Labels are now drawn for the trigger and the flights — the spine an operator reads — and on
  selection for everything else. The name is still on each node's `aria-label` and every record is
  in the table below.
- **The blast-radius headline read "303 rooms, 232 rooms".** Two different quantities as the same
  noun. Each figure now carries its dimension's own short name.
- **`->` was a tofu box.** `Inter` and `JetBrains Mono` are webfonts; without them the fallback has
  no U+2192 or U+20B9. A box where an arrow should be reads as a rendering fault and undermines
  every figure beside it, so display strings are ASCII. A contract test asserts the API emits
  neither character.

`npm run typecheck` was `tsc --noEmit` against a root config with `"files": []` and only project
references — it checked nothing and exited 0 while the app had eleven type errors. It is now
`tsc -b`.

## What is verified, and how

| Claim | How |
| --- | --- |
| The whole journey, no stubs | `tests/contract/test_real_group_journey.py`, 24 tests, real app + real Postgres |
| 8 / 604 / 22 / 11 / 9 | asserted in that file and re-checked through the browser |
| Plan approval covers medium, never high, never failed evidence | `tests/contract/test_plan_approval_flow.py`, 11 tests against the real engine and tables |
| What-if writes nothing | row census over every table in `Base.metadata` before and after |
| Console renders at 1920×1080 | `agent-browser` against the real API: no horizontal overflow, 0 page errors, focus outline present |
| Fixture and API are one contract | `test_incident_group_fixture.py` compares the fixture's fields to `GroupDetailResponse` |

The seeded scenario holds exactly one action for a person — `notify_passengers`, high risk — so a
plan approval covers nothing on it. That is why the approval mechanism has its own contract test
with a medium-risk hold: verifying it only through the demo dataset would have passed while the
feature was entirely broken.

## Still true, and deliberately so

- The **per-flight incident is the unit of authorisation.** The group opens eight of them and
  advances each through its own gate. `GroupOrchestrator` adds no authority.
- **Group rollups are derived**, never stored on `incident_group`, and `rollup_status.is_complete`
  is on screen whether it is true or false.
- **303 rooms required against 71 available.** The shortfall is the point of the scenario and it is
  reported, not smoothed.
- `evaluate_entitlements`, `record_outcome`, `arrange_ground_transport`, `rebook_passengers` and
  `reassign_gate` have no service and refuse explicitly. They are outside the Phase 2 critical path.
