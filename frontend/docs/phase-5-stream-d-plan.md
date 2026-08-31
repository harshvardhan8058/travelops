# Phase 5 — Scenario Builder and Passenger disruption view (Stream D)

Frontend only. Two new routes, both under `frontend/**`, no backend, database, policy, assurance,
execution or Phase 4 logic touched.

## Why this document exists

`docs/27-ui-specification.md` lists eight screens and, under **Deliberately not built**, names a
"Passenger-facing portal" alongside chat interfaces, mobile layout and a light theme. A Scenario
Builder is not in that inventory either. `docs/**` is Stream A's and `docs/28-parallel-workstreams.md`
is additionally SHARED, so this stream cannot amend either to legitimise two screens it has just
built. The divergence is recorded here, in a D-owned file, for Stream A to rule on rather than left
for someone to discover from the route table.

Two points in favour of keeping them:

- The console has already grown from the eight documented routes to thirteen, so extension has
  precedent and the inventory is behind the code rather than the code being ahead of a decision.
- Neither screen weakens a documented boundary. Both are read-only against the real backend: the
  builder sends nothing and the passenger view fetches nothing. Removing them changes no operational
  behaviour, which is not true of anything else added since Phase 2.

The counter-argument is the one docs/27 already makes: each costs days and neither answers one of the
controller's four questions. That is a product call, not a technical one.

## What was built

| Route | Screen | Reads |
| --- | --- | --- |
| `/scenarios/new` | Scenario Builder | proposed contracts in `features/scenario-builder/scenarioContracts.ts` |
| `/passenger/:bookingRef` | Passenger disruption view | proposed contracts in `features/passenger/passengerContracts.ts` |

The builder is three steps — template, disruption details, validate and preview — with `Create
scenario` and `Create & run` at the end. The passenger view carries trip and flight status, what
happened, passenger impact, available options, what TravelOps is doing, and the approval or
next-step state.

## The mock seam

No endpoint serves either screen. The proposed contracts live in the **consuming feature**, not in
`@/api/types`, because that module documents contracts the backend actually publishes and a
speculative shape sitting beside a real one is how a reviewer comes to believe an endpoint exists.
When the endpoints land, the interfaces move to `@/api/types`, the sample constant is deleted, and
each screen changes by one import — the passenger view already routes its sample through react-query
so the swap is a single `queryFn`.

The `fixtures/api/**` channel was deliberately **not** used. It is Stream C's, it is contractual with
Stream A's endpoints, and `VITE_USE_FIXTURES` disables every write affordance in the product.

## What these screens refuse to do

Both inherit the rules the rest of the console holds, and the reasons are recorded in each file:

1. **No fabricated state transition.** `Create` and `Create & run` build the exact request body and
   render it as *prepared, not sent*, naming the endpoint that would receive it. Reporting a created
   scenario with an invented id is the defect `api/client.ts` refuses for every write.
2. **No figure only the engine can produce.** The preview names passengers, connections, crew pairings
   and candidate hotels as computed on run, rather than guessing them. The only aggregate either
   screen computes is the length of a list the operator typed.
3. **No money and no probability on the passenger screen.** An entitlement is computed by the policy
   engine from a reviewed pack; a rupee figure rendered from a mock would be a locally computed
   entitlement. The screen renders the contract's own note about where the figure comes from.
4. **Nothing is confirmed that a person has not approved.** A rebooking awaiting a gate reads as
   awaiting a gate.
5. **A command is offered only when it works.** The equivalent CLI line appears only while the draft
   still matches a scenario this repository actually seeds, and is withdrawn on any divergence.

## Design system

Everything reuses `components/ui`. One primitive was added — `StepRail`, sibling to `StateRail` —
because a wizard has a state `StateRail` has no concept of: reachable but not yet valid. Rendering
"not started" and "started and wrong" identically is what makes a Next button appear to do nothing.

## Verification

- `npm run test` — 187 tests, 6 files (+89 over Phase 4: 57 builder, 32 passenger)
- `typecheck`, `lint`, `tokens:check`, `format:check`, `build` — all clean
- `verify:console` — **12/12, unchanged**. The two new routes were deliberately not added to `ROUTES`;
  adding them is a one-line change if the gate should cover them, which would make it 14/14.
- Both new routes were driven in the same headless browser at 1920×1080 with the gate's own checks —
  no runtime error, nothing below WCAG AA, no horizontal overflow — and the builder was driven through
  all three steps to a prepared request.

That browser pass earned its keep: it caught `uppercase` on a wrapper that case-transformed the
request id, so `scn-1a2b3c4d` displayed as `SCN-1A2B3C4D`. A source-scan guard in
`scenarioDraft.test.ts` now fails on that shape in either screen.
