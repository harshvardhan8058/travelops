# 28. Parallel Workstreams — Running Six Kiro Accounts

Six accounts across four people is real capacity, but parallel AI agents on one repository fail in a
predictable way: two sessions edit the same file, both are individually correct, and the merge is a mess.

This document removes that risk by assigning **exclusive file ownership** per stream. The rule is simple:

> A stream may only create or modify files inside the paths it owns. Anything outside is a request to the
> owning stream, not an edit.

## Why the steering file matters here

`.kiro/steering/travelops.md` is loaded automatically in every session in this repository. That is what
makes six independent sessions produce consistent code: the taxonomy, the assurance rules, the policy
modes, the provenance requirement and the no-purple UI rules are enforced everywhere without anyone
repeating them.

**Treat steering as shared law.** Only Stream A edits it, and only with the team's agreement, because a
change there silently changes what the other five sessions will build.

## The six streams

| Stream | Owns | Deliverable | Suggested owner |
| --- | --- | --- | --- |
| **A · Core** | `backend/app/orchestrator/`, `events/`, `config.py`, `main.py`, `.kiro/steering/` | Workflow engine, state machine, event bus, limits, config validation | Harshvardhan Sharma |
| **B · Assurance + Policy** | `backend/app/assurance/`, `backend/app/policy/`, `policy_packs/` | Six checks, fail-closed aggregation, pack loader, rules engine, charter-mode evaluation | Karthikeyan D |
| **C · Data + Providers** | `backend/app/models/`, `migrations/`, `providers/`, `data/` | Schema, Alembic, seeders, weather/flight/notification providers with fixtures | Harshvardhan Jha |
| **D · Services** | `backend/app/services/` | The ten deterministic services | Sabyasachin Biswal |
| **E · Frontend shell** | `frontend/src/design/`, `components/ui/`, `api/`, `features/ops-board/`, `features/timeline/` | Tokens, primitives, typed client, Ops Board, Decision Timeline | second session, any member |
| **F · Frontend workspace** | `features/incident/`, `features/assurance/`, `features/policy-citation/`, `features/cascade/`, `features/reports/` | Recovery workspace, assurance panel, policy citation, cascade graph | second session, any member |

Streams E and F are the two extra accounts. Frontend parallelises best because the screens are genuinely
separable once tokens and the typed client exist.

## Hard sequencing constraints

Only three real dependencies exist. Everything else runs concurrently.

```text
C (schema + migrations)  ──▶  A, B, D can persist
A (event + task contracts) ──▶  D can be invoked
C (OpenAPI generated)     ──▶  E, F have real types
```

Until those land, downstream streams work against fixtures. That is why the next section exists.

## Day one: freeze the contracts before parallelising

Six sessions building against guesses is worse than one session building slowly. Before streams split:

1. **C** writes the schema and runs the first migration.
2. **A** commits typed event and task contracts.
3. **B** commits the assurance evaluation record shape.
4. **C** generates the OpenAPI document; **E** generates the typed client from it.
5. Commit fixture JSON for every endpoint the frontend needs.

These come from [`26-implementation-contracts.md`](26-implementation-contracts.md) and
[`11-data-model.md`](11-data-model.md) — the shapes are already decided, so this is transcription, not
design. Budget half a day.

After that, E and F never wait for the backend. They build against fixtures and switch to live responses
by changing a base URL.

## Files that must never be edited in parallel

| File | Owner | Why |
| --- | --- | --- |
| `.kiro/steering/travelops.md` | A | Changes behaviour of all six sessions |
| `docker-compose.yml`, `Makefile`, `.env.example` | A | Constant conflict magnets |
| `migrations/` | **C only** | Two sessions generating migrations produces unorderable heads |
| `models/` | C | Everyone imports it |
| Generated API client | E | Regenerate, never hand-edit |
| `policy_packs/` | B | Pack hashes and review state must stay coherent |
| `docs/` | Whoever owns the subject | One doc, one owner |

If a stream needs a change in someone else's path, it opens a PR comment or a short issue. It does not
edit.

## Branch and integration model

```text
main                      always runnable
└── stream/<letter>/<slice>    e.g. stream/b/assurance-gate
```

- One branch per slice, not per stream. Short-lived, merged daily.
- PR title prefixed with the stream letter: `[B] assurance gate aggregation`.
- **Integrate daily.** A conflict found on day two costs an hour; found the day before Stage 2 it costs
  the demo.
- `git pull --rebase origin main` before every push.
- Nobody merges their own PR without one other stream reviewing it.

## Session prompt template

Each account starts its session with a scoped prompt. This is what keeps a session inside its lane:

```text
You are working on TravelOps AI, Stream <LETTER> — <NAME>.

Read first: docs/26-implementation-contracts.md, docs/16-folder-structure.md,
and the doc listed for my stream below.

I own ONLY these paths: <paths>
Do not create or modify files outside them. If a change is needed elsewhere,
tell me and I will raise it with the owning stream.

Current target: <Stage 2 deliverable from docs/25-evaluation-readiness.md>
Definition of done: the relevant gate in docs/25-evaluation-readiness.md passes.

Branch: stream/<letter>/<slice>. Commit in small, working increments.
```

Per-stream required reading:

| Stream | Read |
| --- | --- |
| A | `26-implementation-contracts.md`, `01-architecture.md`, `02-disruption-flow.md` |
| B | `18-decision-assurance-gate.md`, `19-jurisdiction-and-policy-packs.md`, `13-compensation-and-policy.md`, the pack's `rules.yaml` and `test_cases.yaml` |
| C | `11-data-model.md`, `10-data-sources.md`, `12-synthetic-data-plan.md` |
| D | `03-agent-design.md`, `06-ai-vs-deterministic.md`, `22-crew-pairing-model.md` |
| E | `21-design-system.md`, `27-ui-specification.md` screens 1 and 6 |
| F | `21-design-system.md`, `27-ui-specification.md` screens 2–5 and 7 |

## Realistic expectations

Six parallel sessions do not produce six times the output. Expect roughly **two and a half to three times**
a single stream, because integration, review and contract alignment consume the rest. That is still a large
win, and it is the difference between Stage 2 being comfortable and being a scramble.

Two failure modes to watch:

- **Divergent conventions.** Two sessions invent two different error shapes. Mitigation: the steering file
  and a single review pass on the first PR from each stream.
- **Silent scope creep.** A session decides it needs a feature outside its lane and builds it. Mitigation:
  the ownership table, and reviewing PR file lists before content.

## Suggested first slices

Everything below is independently startable once contracts are frozen.

| Stream | First slice |
| --- | --- |
| A | Compose up, health endpoints, config validation that fails closed, incident state machine |
| B | Six checks as pure functions with unit tests, then aggregation, then the pack loader |
| C | Migrations, airport/runway loader, synthetic passenger and pairing seeders, fixed seed `20260807` |
| D | Delay Risk service with unit-tested thresholds, then Connection service |
| E | Tailwind token override, `<StateBadge>`, `<MonoValue>`, `<ProvenanceDot>`, Ops Board against fixtures |
| F | Recovery workspace three-column layout and assurance panel against fixtures |

## Cost note

Six Pro accounts is meaningful spend. If you want to reduce it, streams **E and F can share one account**
sequentially, and stream **D can fold into A** once the orchestrator is stable. Four accounts is enough to
hit Stage 2 comfortably; six is enough to hit Stage 3 early.
