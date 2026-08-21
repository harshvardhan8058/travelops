# 29. Kickoff — Exact Prompts for Six Kiro Accounts

Copy-paste operating instructions. Read [`28-parallel-workstreams.md`](28-parallel-workstreams.md) for the
ownership model; this document is the execution script.

## The sequencing that decides your throughput

```text
WAVE 0   one session only          ~2-4h    scaffold + contracts + fixtures
   │
   ▼
WAVE 1   all six in parallel       days     features, no waiting, no collisions
   │
   ▼
DAILY    integrate, review, merge
```

Wave 0 exists because six sessions cannot create the same `docker-compose.yml`. Skipping it does not save
time — it converts saved hours into merge conflicts and contract drift.

**After Wave 0 lands on `main`, all six streams are genuinely independent.** That is the state you want.

---

## Wave 0 — bootstrap (one session, before anything else)

Deliverables, all from existing specs:

| Area | Output |
| --- | --- |
| Root | `docker-compose.yml`, `Makefile`, `.env.example`, `.gitignore` |
| Backend | `pyproject.toml` (uv), full `app/` module tree, `main.py` health endpoints, `config.py` with fail-closed validation |
| Schema | SQLAlchemy models per [`11-data-model.md`](11-data-model.md) + first Alembic migration |
| Contracts | `events/types.py`, `agents/contract.py` discriminated union, assurance record shape |
| Gate config | `config/assurance.v1.yaml` with version and hash |
| Frontend | Vite + TS + Tailwind with tokens from [`21-design-system.md`](21-design-system.md), shadcn theme overridden, generated API types |
| Fixtures | One JSON response per endpoint in [`26-implementation-contracts.md`](26-implementation-contracts.md), plus `data/fixtures/bengaluru_storm.yaml` |

**Exit gate:** `docker compose up` starts API + Postgres + Redis + frontend; `/health/ready` returns
dependency status; the frontend renders an empty Ops Board against fixtures; `alembic upgrade head` runs
clean.

Merge Wave 0 to `main` before opening Wave 1 sessions. Everything below assumes it is present.

---

## Universal preamble — prepend to all six prompts

```text
Project: TravelOps AI (team SkyForge AI), Coforge TechCon 2026.
Repo: harshvardhan8058/travelops. Wave 0 scaffold is already on main.

Before writing code, read:
  .kiro/steering/travelops.md      (loaded automatically - these rules are binding)
  docs/26-implementation-contracts.md
  docs/16-folder-structure.md
  docs/25-evaluation-readiness.md  (my definition of done)

Non-negotiable rules for every stream:
- 1 orchestrator + 3 reasoning agents + 10 deterministic services. Never "13 agents".
- Execution is authorised by the deterministic Decision Assurance Gate, never by an
  LLM confidence score. `confidence` is not in any execution contract.
- Deterministic services must never import an LLM client.
- Every external provider needs a fixture/offline implementation.
- Every data surface carries provenance: real | simulated | synthetic | fixture | unavailable.
- Missing safety config fails closed. Never silently degrade.
- UI: graphite + instrument cyan only. No purple/violet/indigo, no gradients, no glows,
  no glassmorphism, no emoji icons. Tokens only, never colour literals.
- Operational numbers render in tabular monospace.

I own ONLY these paths: <PATHS>
Do not create or modify anything outside them. If you need a change elsewhere, tell me
and I will raise it with the owning stream. Never touch migrations/, models/, the
generated API client, policy_packs/, docker-compose.yml, Makefile, or .kiro/steering/
unless my stream owns it.

Work on branch: <BRANCH>
Commit small working increments. Run tests before each commit. Never push to main.
```

---

## Account 1 — Stream A · Core

```text
<UNIVERSAL PREAMBLE>

PATHS:  backend/app/orchestrator/, backend/app/events/, backend/app/config.py,
        backend/app/main.py, backend/app/api/, backend/tests/unit/orchestrator/
BRANCH: stream/a/orchestrator

Also read: docs/01-architecture.md, docs/02-disruption-flow.md

Build the workflow control plane:
1. Incident state machine with the exact canonical states from
   docs/26-implementation-contracts.md: detected, assessing, planning, assuring,
   awaiting_approval, executing, resolved, blocked, failed. Illegal transitions
   raise and map to HTTP 409 INVALID_STATE_TRANSITION.
2. Redis Streams event bus using the typed events already defined in events/types.py.
   Consumers idempotent by event_id.
3. Orchestrator engine: task ordering, dependency resolution, parallel dispatch of
   independent tasks, retries, and idempotency-key enforcement on every mutation.
4. orchestrator/limits.py: max workflow steps and per-action timeout, from config.
   Exceeding a limit blocks rather than loops.
5. API routers for the endpoints in docs/26 section "API surface". Return the
   provenance contract on every response that carries external or seeded data.

Definition of done: injecting the bengaluru_storm fixture opens exactly one incident,
walks the state machine to a terminal state, and writes an ordered immutable record
per step. Unit tests cover illegal transitions, idempotent replay, and limit breaches.

Do NOT implement the six assurance checks (Stream B) or any domain service (Stream D).
Call their interfaces and let them be stubs for now.
```

## Account 2 — Stream B · Assurance + Policy

```text
<UNIVERSAL PREAMBLE>

PATHS:  backend/app/assurance/, backend/app/policy/, policy_packs/,
        backend/tests/unit/assurance/, backend/tests/unit/policy/
BRANCH: stream/b/assurance-gate

Also read: docs/18-decision-assurance-gate.md, docs/19-jurisdiction-and-policy-packs.md,
docs/13-compensation-and-policy.md, and policy_packs/in-moca-charter-2019/2019.02/
(rules.yaml and test_cases.yaml are your specification - 40 rules, 23 cases).

Build in this order:
1. The six checks as pure functions, no I/O: evidence completeness, source freshness,
   entity validation, policy compliance, conflict detection, action risk tier.
   Each returns PASS | WARN | FAIL plus a machine-readable reason code.
2. Fail-closed aggregation exactly as specified: unknown action/rule/missing config is
   FAIL; any FAIL yields needs_human; high risk always yields needs_human; a WARN may
   yield execute_flagged ONLY where versioned config explicitly permits that warning for
   that low-risk reversible action. No global soft-failure bypass.
3. Immutable evaluation record including all six check states, blocking reasons,
   evidence refs, risk tier, config version AND config hash.
4. Policy pack loader with the status ladder: draft, official_guidance_dated, approved,
   retired. POLICY_MODE=verified must REJECT in-moca-charter-2019 with
   PACK_NOT_VERIFIED_ELIGIBLE. Rules flagged excluded_from_evaluation never evaluate.
5. Jurisdiction-neutral rules engine. Applicability is tri-state: applicable,
   not_applicable, undetermined. A missing required fact is undetermined, never false.
6. Make every case in the pack's test_cases.yaml pass, including the fail-closed ones.

Critical: a weather trigger must NEVER auto-exempt compensation. The exemption requires
evidence that the cause was external AND unavoidable despite all reasonable measures.
Missing that evidence produces needs_human. Test case
`cancellation_weather_without_reasonable_measures_evidence` exists to prove this.

Definition of done: all 23 pack test cases pass; verified mode rejects the charter pack;
the 24-hour cancellation rule never evaluates.
```

## Account 3 — Stream C · Data + Providers

```text
<UNIVERSAL PREAMBLE>

PATHS:  backend/app/models/, backend/migrations/, backend/app/providers/, data/,
        backend/tests/contract/
BRANCH: stream/c/data-providers

Also read: docs/11-data-model.md, docs/10-data-sources.md, docs/12-synthetic-data-plan.md

You are the ONLY stream permitted to create migrations. Two streams generating
migrations produces unorderable heads.

Build:
1. Loaders for real public data: OurAirports airports + runways for the 10-airport set.
   Archive the snapshot and record its hash. Runway headings matter - crosswind needs them.
2. Weather provider: Aviation Weather Center METAR/TAF live implementation AND a committed
   fixture implementation behind the same interface. Normalise units at the boundary
   (store knots, not km/h). Return the provenance contract with observation age.
3. Flight status simulator: deterministic state machine, no external feed.
4. Synthetic generators with fixed seed 20260807, working BACKWARDS from the fixture
   targets in docs/12: 8 affected flights, ~600 passengers, 22 at-risk connections,
   11 candidate hotels, exactly 9 traceable crew pairings. Model pairings explicitly
   with legs and roles (operating vs positioning) - not a flat crew-to-flight column.
   Make hotel capacity deliberately insufficient for at least one allocation.
5. Notification provider: console, mailtrap and SMTP implementations. Real sends go only
   to an allowlist from env. Everything else records as simulated.
6. Commit the dataset dump. Never regenerate during a demo.

Definition of done: `make seed` produces a byte-identical dataset; a recursive query over
pairing legs returns exactly 9 affected pairings for the storm fixture, each traceable to a
mechanism; the weather provider passes contract tests in both live and fixture modes.
```

## Account 4 — Stream D · Deterministic Services

```text
<UNIVERSAL PREAMBLE>

PATHS:  backend/app/services/, backend/tests/unit/services/
BRANCH: stream/d/services

Also read: docs/03-agent-design.md, docs/06-ai-vs-deterministic.md,
docs/22-crew-pairing-model.md

Build the ten deterministic services. None may import an LLM client. Each is typed,
unit-tested, and returns a result plus evidence references.

Priority order (build the first four first, they carry the Stage 2 demo):
1. Delay Risk - wind, crosswind vs runway heading, visibility, ceiling, precipitation.
   Returns a risk INDEX (0-100) and LEVEL (low/elevated/high/severe) plus named
   contributing factors and a rule version. Never call it a probability - it is not
   calibrated. Thresholds come from config.
2. Connection - identify itineraries whose onward segment is no longer feasible.
3. Crew Impact - walk pairing legs forward from affected flights. Report affected
   pairings with the mechanism for each: onward duty, second pairing, or positioning.
   Coordination and display ONLY. Never validate duty-time legality.
4. Communication - render approved templates, dispatch through the notification provider.
Then: Flight Recovery, Hotel (budget + partner preference from config), Transport,
Compensation (calls Stream B's policy engine, never computes law itself),
Gate/Resource, Analytics/Learning.

Definition of done: each service has unit tests covering thresholds and boundaries;
Delay Risk output is reproducible for identical input; no service imports the LLM client
(add a test that asserts this).
```

## Account 5 — Stream E · Frontend Shell

```text
<UNIVERSAL PREAMBLE>

PATHS:  frontend/src/design/, frontend/src/components/ui/, frontend/src/api/,
        frontend/src/features/ops-board/, frontend/src/features/timeline/
BRANCH: stream/e/ops-board

Also read: docs/21-design-system.md (tokens/aesthetics) and
docs/27-ui-specification.md screens 1 and 6.

Work entirely against the committed fixture JSON. Do not wait for the backend.

Build:
1. Tailwind token layer from docs/21 exactly: base #0B0F14, surface #111821,
   accent #3FC9DE, state ok/warn/crit/info. Override the shadcn theme - do not accept
   its defaults. One grep for "#" in src/ should return only the token file.
2. Shared primitives: <StateBadge>, <RiskChip>, <ProvenanceDot>, <MonoValue>,
   <StateRail>, <EmptyState>, <ErrorState>, <WhyPopover>, <ModeChip>.
   Every status renders through StateBadge. Every operational number through MonoValue
   with tabular numerals (JetBrains Mono).
3. App shell: 56px icon rail, 52px top bar with clock, LLM_MODE chip, POLICY_MODE badge
   and provider health, plus the persistent 380px Decision Timeline rail that appears on
   EVERY route.
4. Ops Board: network strip (weather + risk + observation age turning amber past the
   freshness limit), flight board table at 34px rows, active incident cards.
5. Decision Timeline: streaming entries with actor badge, expandable evidence.
6. Persistent blocked-actions bar when anything awaits approval.

Definition of done: all five states implemented on every surface (loading skeleton with
no layout shift, designed empty, populated, error with correlation ID, degraded banner);
keyboard reachable with visible focus; WCAG AA; legible at 1920x1080 from three metres;
zero purple/gradient/glow anywhere.
```

## Account 6 — Stream F · Frontend Workspace

```text
<UNIVERSAL PREAMBLE>

PATHS:  frontend/src/features/incident/, frontend/src/features/assurance/,
        frontend/src/features/policy-citation/, frontend/src/features/cascade/,
        frontend/src/features/reports/
BRANCH: stream/f/recovery-workspace

Also read: docs/21-design-system.md and docs/27-ui-specification.md screens 2, 3, 4, 5, 7.
Import primitives from Stream E - do not duplicate them. If a primitive is missing, ask
me and I will request it from Stream E.

Work against the committed fixture JSON.

Build in this order:
1. Recovery Workspace: three columns. Evidence (weather used, risk factors, affected
   entities, retrieved precedent) | Plan with a generator chip stating
   "Planner - groq - llama-3.3-70b - prompt v1" OR "Fallback playbook - deterministic",
   never ambiguous | Assurance panel.
2. Assurance panel: all six checks with PASS/WARN/FAIL as icon + word + colour, reason
   codes, evidence refs, and the config version AND hash always visible. When the
   decision is needs_human it becomes an approve/reject panel with a MANDATORY reason
   field.
3. Approval Queue at /assurance. No bulk-select for high-risk items - approving eight
   cash payouts in one click defeats the gate.
4. Policy and Citation: pack status banner at the top rendering the pack's REAL status
   (never a manual override), entitlement breakdown showing the actual formula
   e.g. "least_of(cap 7500, 4200 + 800) = 5000", citation card, and the cause-comparison
   toggle recomputing the same incident as weather vs crew rostering.
5. Cascade view: SVG node-link graph. Edges MUST be labelled with the propagation
   mechanism - operating, onward duty, second pairing, positioning. Counts come from the
   API, never hardcoded. A judge must be able to count nine pairings and read why.
6. Executive Report: metrics from records only, generated narrative clearly attributed.

Definition of done: same as Stream E - all five states, keyboard reachable, WCAG AA,
projector-legible, no colour literals.
```

---

## Daily rhythm

| When | Action |
| --- | --- |
| Session start | `git pull --rebase origin main` before touching anything |
| During | Commit small working increments; push your branch often |
| Slice complete | Open a PR titled `[<LETTER>] <slice>`; another stream reviews |
| End of day | **Everything mergeable is merged.** `main` must always run |

Review the PR **file list first**. If it touches paths the stream does not own, that is the
finding — before reading any code.

## Pairing four people to six accounts

| Person | Accounts |
| --- | --- |
| Harshvardhan Sharma | A · Core |
| Karthikeyan D | B · Assurance + Policy |
| Harshvardhan Jha | C · Data + Providers |
| Sabyasachin Biswal | D · Services |
| Whoever has capacity | E · Frontend shell |
| Same person as E | F · Frontend workspace |

E and F are the natural double-up: one frontend mindset, cleanly separated directories, and both work
against fixtures so neither blocks on backend progress.

## Getting maximum output per account

- **Scope every prompt.** A session told "build the backend" wanders. One told "build the six checks as
  pure functions with unit tests" ships.
- **Keep `LLM_MODE=fixture` while developing.** Live inference during iteration burns quota for no benefit.
- **Let each session own its tests.** A stream that writes its own tests does not need another stream to
  verify it, which removes a synchronisation point.
- **Do not ask a session to re-read the whole docs set every turn.** The steering file loads automatically;
  point at the two or three specific documents that matter.
- **When a stream finishes early**, it does not wander into another stream's paths. It picks the next slice
  from its own column in [`14-hackathon-plan.md`](14-hackathon-plan.md), or writes tests, or takes a review.

## When a stream is blocked

State the blocker in the PR or to the owning stream, then **switch to fixture-backed work in your own
paths.** Never sit idle and never fix it by editing someone else's files. Frontend streams are never
blocked by definition, because fixtures exist.
