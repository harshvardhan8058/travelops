# Phase 2 — Stream C delivered surface

What Stream C landed for Phase 2, what other streams can now build against, and the decisions
that shaped it. Written for the reviewer who wants to check a number rather than read a plan.

Owner: Stream C (data, providers, services).

## Migration ledger

Applied in order against PostgreSQL 16. Table count 37 → 41.

| Revision | Adds | Tables after |
| --- | --- | --- |
| `0004_incident_group_flight` | `incident_group_flight` | 38 |
| `0005_plan_candidates_and_hash` | 5 columns + 2 CHECKs + 3 indexes on `plan` | 38 |
| `0006_plan_approval` | `plan_approval`, `plan_approval_tier`, `action.plan_approval_id` | 40 |
| `0007_cascade_graph_and_impact` | `disruption_edge`, `cascade_snapshot`, `passenger_impact`, `hotel_inventory_hold`, `pairing_impact.depth` | 41 |

Two rules are enforced by the database rather than by convention, and both were verified by
attempting the illegal insert against real Postgres:

- `plan_approval_tier` has `CHECK (risk_tier IN ('low','medium'))`. A plan approval **cannot**
  record coverage of high risk. This is P2-D3 as a storage guarantee.
- `disruption_edge` has `CHECK ((derived_from_action_id IS NULL) <> (derived_from_prediction_id
  IS NULL))`. An edge **cannot** exist without naming the recorded row it came from.

## What A, B and D can build against

| Surface | Where | Notes |
| --- | --- | --- |
| Group membership | `incident_group_flight`; `group_affected_flights()`, `group_affected_flight_ids()` | Declared data. 8 rows: 1 primary, 6 `affected_departure`, 1 `affected_arrival`. |
| Plan candidates | `plan.selection_state` / `selected_at` / `selected_by` / `variant_key` | Existing rows default to `candidate`, so Phase 1 behaviour is unchanged. A partial unique index gives one selected plan per incident. |
| Plan hash | `plan.plan_hash`; `app/db/plan_identity.py` | `compute_plan_hash(tasks, generator=, prompt_version=)`, 32 hex chars. `approval_covers(...)` returns `(bool, reason)` for the four P2-D3 conditions. |
| Plan approval | `plan_approval` + `plan_approval_tier`; `action.plan_approval_id` | Separate from `human_decision` so `assurance_id` stays NOT NULL. |
| Service input contracts | `app/services/contracts.py` | `SERVICE_INPUT_SPECS`, `required_facts_for()`, `missing_facts_for()`. Lets dispatch fail in preflight with a message naming a column. |
| Passenger cohorts | `app/services/passenger_impact.py`; `passenger_impact` table | `PassengerCohortFacts` in, `PassengerCohort` out. Weights live in `business_constraint`. |
| Cascade graph | `app/services/cascade_graph.py` | `project_graph()`, `project_and_record()`, `graph_payload()`. |
| Blast radius | `app/services/blast_radius.py` | `compose_blast_radius()`, `blast_radius_payload()`. Composition only. |
| What-if | `app/services/what_if.py` | `evaluate_what_if()`, `what_if_payload()`. Zero-write. |
| Hotel | `app/services/hotel.py` | `HotelSearchService` (`find_hotel_options`), `HotelAllocationService` (`reserve_hotel_block`), `load_hotel_options()`. |

Fixture additions are **additive**: `graph` and `blast_radius` on
`fixtures/api/incident_group_detail.json`, and `rollup_status` on
`fixtures/api/incident_groups.json`. Every key the console already read is unchanged, and a
contract test asserts that. `rollup_status` is a sibling of `rollups` rather than a member
because Stream D types `rollups` as `Record<string, number | string>` and a boolean inside it
would have broken that type for a cosmetic gain.

## Decisions worth arguing with

**Group membership is declared, not inferred.** The tempting query is
`flight.origin_icao = group.airport_icao` plus a delay threshold. It returns seven of the eight
flights, because UK 705 arrives into VOBL rather than departing it. Those seven still produce
**nine** pairings — so the headline survives — while the `onward_duty` mechanism silently
disappears and PAIR-E1 is relabelled `operating`. A wrong number gets caught in review; a right
number reached the wrong way does not. Proved in
`test_a_departures_only_cascade_still_reports_nine_but_loses_a_mechanism`.

**The graph is edges over existing rows. There is no node table.** Nodes are addressed
`kind:identifier`, the same vocabulary as `evidence_refs`, so a node *is* a `flight`, `pairing` or
`booking` row. A node table would duplicate those and need syncing, and the first time it drifted
the graph and the incident list would disagree with nothing to say which was right.

**Root-cause edges name a `prediction`, not an `action`.** The weather is not an action anyone
took. The delay-risk assessment is the recorded row tying a flight to the event, so that is what
the edge points at. This is why `disruption_edge` has two provenance columns and an exclusive-arc
CHECK instead of one NOT NULL action id.

**`cascade_snapshot` is append-only and nothing is denormalised onto `incident_group`.** A mutable
rollup column drifts from the rows it summarises, and once it has, nothing in the system can say
which of the two is correct. A history of computations can always be checked against the actions
it names. Guarded by `test_no_rollup_column_was_added_to_incident_group`.

**Hotel availability is `total_rooms - sum(active holds)`.** `hotel.available_rooms` is
deliberately not decremented. A counter loses updates under concurrency and cannot be replayed —
after a reset there is no way to show *why* a property had six rooms left.

**Crew expansion is separated by depth.** `direct` is the Phase 1 answer and stays exactly nine at
every expansion depth; `downstream` is additive. A single flat list would let a config value move
the one number a reviewer can verify by hand. Asserted at depths 1, 2, 3 and 5.

**Blast radius reports completeness, never confidence.** Completeness is countable — eight of eight
flights assessed. Confidence would be a probability, and nothing here is calibrated against
observed outcomes. One uncheckable figure sitting next to five checkable ones takes the
credibility of all six.

## Refused, deliberately

- **Seat availability and capacity.** No such column exists. Connection alternatives are
  `schedule_feasible_only`: "this departure is late enough to be reachable", never "a seat exists".
- **Party or PNR grouping.** `booking.pnr` is unique and there is one passenger per booking.
  Inferring families from surnames would be fabrication.
- **Special-needs sub-categories.** The schema has one boolean. Splitting it into wheelchair,
  medical or unaccompanied minor would invent a distinction the data does not carry — and those are
  exactly the categories where being wrong is worst.
- **Monetary valuation of a passenger.** Tier participates only because an operator put a weight on
  it in `business_constraint`, with an audit trail.
- **Duty-time legality.** Unchanged from Phase 1: `crew_member.duty_hours_limit` is never read, and
  an AST test over `crew_impact.py` enforces it.
- **Open-Meteo / historical provider expansion.** Cut as instructed, before any core feature.

## The numbers, and where each comes from

| Figure | Value | Source |
| --- | --- | --- |
| Declared member flights | 8 | `incident_group_flight` |
| Passengers | 604 | `booking_segment` against declared flights |
| Connections at risk | 22 | union of distinct `booking_id` from recorded Connection actions |
| Crew rotations (direct) | 9 | recorded Crew Impact actions, deduplicated by reference |
| Candidate hotels | 11 | `hotel` at `VOBL` |
| Rooms required / secured / short | 87 / 71 / 16 | 174 passengers ÷ 2, against the 6 properties inside the ₹6,000 cap |
| Seed rows / digest | 2093 / `fa9564fc4afefc5d` | `build_seed_plan()`, reproduced inside the built image |

The seed digest changed from `70fbdf8947c638e5` because the dataset grew by ten rows: eight
`incident_group_flight` rows and two `business_constraint` rulesets (passenger priority weights,
crew expansion bound). Both are the point of the increment rather than incidental.
