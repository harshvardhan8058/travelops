# 28. Parallel Workstreams — Running Four Kiro Accounts

Four accounts is real capacity, but parallel AI agents on one repository fail in a predictable way: two
sessions edit the same file, both are individually correct, and the merge is a mess.

This document removes that risk by assigning **exclusive file ownership** per stream. The rule is simple:

> A stream may only create or modify files inside the paths it owns. Anything outside is a request to the
> owning stream, not an edit.

Every stream may **read** the entire repository. Only writes are partitioned.

> **Necessary but not sufficient — read [`36-integration-protocol.md`](36-integration-protocol.md) too.**
> Phase 2 broke this rule five times, and none of the crossings was avoidable by staying more
> carefully inside a directory: each was a **seam**, a file one stream owns whose content is partly
> another stream's. Directory ownership says who may type; the seam register says how everyone else
> gets what they need without editing the file.

## Why the steering file matters here

`.kiro/steering/travelops.md` is loaded automatically in every session in this repository. That is what
makes four independent sessions produce consistent code: the taxonomy, the assurance rules, the policy
modes, the provenance requirement and the no-purple UI rules are enforced everywhere without anyone
repeating them.

**Treat steering as shared law.** Only Stream A edits it, and only with the team's agreement, because a
change there silently changes what the other three sessions will build.

## The four streams

| Stream | Owns (write access) | Deliverable |
| --- | --- | --- |
| **A · Core & API** | `backend/app/{orchestrator,events,api,agents,llm,observability,schemas}/`, `config.py`, `main.py`, `cli.py`, `errors.py`, `docker-compose.yml`, `Makefile`, `.kiro/`, `docs/`, `scripts/` | Workflow engine, event bus, the twelve real endpoints, CLI, reasoning agents, prompt files |
| **B · Assurance & Policy** | `backend/app/{assurance,policy}/`, `policy_packs/`, `config/`, `backend/tests/unit/{assurance,policy}/` | Six checks, fail-closed aggregation, pack loader, tri-state resolver, rules engine, 23 pack cases |
| **C · Data, Providers & Services** | `backend/app/{models,db,providers,services,memory}/`, `backend/migrations/`, `data/`, `fixtures/`, `backend/tests/unit/services/`, `backend/tests/contract/` | Loaders, generators, four providers with fixture twins, the ten deterministic services |
| **D · Frontend** | all of `frontend/` | Eight screens, provenance popovers, replay, command palette, keyboard model |

### Why this particular split

Three alternatives were considered and rejected:

- **Merging A and D** (backend control plane + frontend) makes one account enormous — engine, event bus,
  twelve endpoints *and* eight screens — while leaving the other three underloaded.
- **Splitting `backend/app/services/` file-by-file** across accounts spreads the ten services around to
  balance the load, but it breaks directory-level single ownership. Two sessions in one package means
  constant conflicts on `__init__.py` and shared helpers.
- **Folding B into anything else** dilutes the safety boundary. B is the code that decides whether
  anything is allowed to happen. It is the smallest stream by file count and the largest by reasoning
  depth, and it needs undivided attention.

C absorbs the old Data+Providers and Services streams because services consume models and providers
directly. One owner across that boundary removes the busiest cross-stream handshake in the project.
D absorbs both frontend streams because the shell now exists — the remaining screens are separable but
share primitives, and a single owner never has to request a new primitive from another account.

## Which account will burn the most tokens

Measured on `main` at Wave 0 completion:

| Rank | Stream | Existing LOC | Units left to build | Why it costs what it costs |
| --- | --- | --- | --- | --- |
| **1 — highest** | **D · Frontend** | 1,662 | 7 screens + popover upgrade + palette + keyboard model | TSX is the most verbose code in the repo, and UI work is inherently iterative: layout, then states, then keyboard, then contrast. Many read-modify-verify cycles per screen. |
| **2** | **C · Data, Providers & Services** | 1,652 | 11 services + 4 providers × 2 impls + generators + loaders | Roughly 20 implementation units. The generators are the hard part — they must work backwards to hit exactly 8 flights, ~604 passengers, 22 connections and **exactly 9 crew pairings**, which takes several correction rounds. |
| **3** | **A · Core & API** | 1,436 | engine + event bus + 9 endpoint replacements + CLI + agents | Moderate file count but high integration cost: every endpoint has to stay byte-compatible with a committed fixture, so each one is a write-then-diff loop. |
| **4 — lowest** | **B · Assurance & Policy** | 365 | 5 stub files, 40 rules, 23 cases | Fewest files by far, but the deepest reasoning per line. Token spend is concentrated in iterating the rule engine until all 23 cases pass, not in producing volume. |

**Put your highest token limit on the account running Stream D, and your second highest on Stream C.**

Stream B is the account to give your *best reviewer*, not your biggest quota — it will consume the least
and matter the most. If one of your four accounts has a noticeably smaller limit than the others, assign
it to B.

If D still runs out of quota, the remedy is ordering, not more scope: screens 2 and 4 (Recovery Workspace
and Approval Queue) carry the Stage 2 demo. Screens 3, 5, 7 and 8 are individually shippable afterwards,
so hitting a limit costs you a screen, never the demo.

## Hard sequencing constraints

Only three real dependencies exist. Everything else runs concurrently.

```text
C (schema + migrations)   ──▶  A, B can persist
A (event + task contracts) ──▶  C's services can be invoked
A (OpenAPI generated)      ──▶  D has real types
```

All three are **already satisfied on `main`.** Wave 0 committed the schema, the migration, the typed
event contracts, the assurance contract and a fixture for every endpoint the frontend needs. That is why
four streams can start simultaneously today rather than after a contract-freeze day.

Until an endpoint is real, D consumes the committed fixture for it. `VITE_USE_FIXTURES=true` is the
default, so **D is never blocked by the backend.**

## Files that must never be edited in parallel

| File | Owner | Why |
| --- | --- | --- |
| `.kiro/steering/travelops.md` | A | Changes behaviour of all four sessions |
| `.kiro/skills/` | A | Shared procedures; a change affects everyone |
| `docker-compose.yml`, `Makefile`, `.env.example` | A | Constant conflict magnets |
| `backend/migrations/` | **C only** | Two sessions generating migrations produces unorderable heads |
| `backend/app/models/` | C | Everyone imports it |
| `backend/app/schemas/` | A | Shared response contracts |
| `fixtures/api/*.json` | C | These are contractual; D renders them and A must match them |
| `policy_packs/` | B | Pack hashes and review state must stay coherent |
| `frontend/src/design/tokens.css` | D | Single source of colour |
| `docs/` | A | One doc, one owner |

If a stream needs a change in someone else's path, it opens a PR comment or a short issue. It does not
edit.

### The shared guard tests are frozen for everyone, including their owner

Six test files are cross-stream invariant guards. Five sit directly under `backend/tests/unit/` — as
opposed to the per-stream subdirectories — and one sits in `backend/tests/contract/`:

| Guard test | Location | Stops |
| --- | --- | --- |
| `test_no_llm_in_services.py` | `tests/unit/` | An AST check: nothing under `app/services/`, `app/assurance/`, `app/policy/` or `app/orchestrator/` importing `groq`, `openai`, `anthropic`, `litellm`, `ollama` or `app.llm` |
| `test_state_machine.py` | `tests/unit/` | An illegal incident transition becoming reachable, and `executing` becoming reachable other than from `assuring` or `awaiting_approval` |
| `test_contracts.py` | `tests/unit/` | A typed contract drifting from its specification |
| `test_config_fail_closed.py` | `tests/unit/` | Missing safety config silently degrading instead of blocking |
| `test_crosswind.py` | `tests/unit/` | The crosswind trigonometry being rewritten incorrectly |
| `test_container_runtime_paths.py` | `tests/contract/` | The `fixtures/` mount regressing and breaking `:5173`, and the datastores becoming reachable beyond loopback |

Note that the AST guard is **wider than its filename suggests**: it protects `app/assurance/`,
`app/policy/` and `app/orchestrator/` as well as `app/services/`. `app/agents/` is deliberately excluded
and is the only layer permitted to reason with a model.

The rule: **any stream may add a guard test; no stream may weaken or delete an existing assertion.** If a
guard fails, the code is wrong, not the test. Relaxing one is a whole-team decision, not a stream's.

This matters most where a guard constrains the stream that would most like to remove it. C is the stream
that would benefit from deleting `test_no_llm_in_services.py`, so C explicitly may not. Nominal ownership
sits with A for review purposes, and `test_crosswind.py` is delegated to C to *extend* as Delay Risk is
built, with its existing assertions still frozen. `test_container_runtime_paths.py` sits inside C's
`backend/tests/contract/` directory, so C is the one stream that must consciously treat a file it owns as
frozen.

### Everything else

Root and build files sit with A: `.gitignore`, `README.md`, `docker-compose.yml`, `Makefile`,
`.env.example`, `backend/Dockerfile`, `backend/.dockerignore`, `backend/.python-version`,
`backend/pyproject.toml`, `backend/tests/__init__.py`. `backend/alembic.ini` sits with C, alongside the
migrations it configures. `frontend/Dockerfile` sits with D, inside the directory it builds.

Every file in the repository has exactly one owning stream. That is verifiable, not aspirational — a
coverage check over the ownership table returns no unclaimed files.

Note that `fixtures/api/*.json` sits with C rather than A. The fixtures are the contract between A's real
endpoints and D's screens; keeping them with the data owner means a shape change is a deliberate, single
place decision rather than something two streams drift on.

## Branch and integration model

```text
main                        always runnable
└── stream/<letter>/<slice>     e.g. stream/b/assurance-gate
```

- One branch per slice, not per stream. Short-lived, merged daily.
- PR title prefixed with the stream letter: `[B] assurance gate aggregation`.
- **Integrate daily.** A conflict found on day two costs an hour; found the day before Stage 2 it costs
  the demo.
- `git pull --rebase origin main` before every push.
- Nobody merges their own PR without one other stream reviewing it.

With four accounts every stream reviews exactly one other stream's work in a fixed rotation, so no PR
waits on a volunteer:

```text
A reviews B    B reviews C    C reviews D    D reviews A
```

## Session prompt

Do not write your own. [`kickoff/`](kickoff/README.md) holds four complete, paste-ready prompts — one per
account, nothing to fill in. Each declares its owned paths, lists what Wave 0 already built so it is not
rebuilt, orders its deliverables, and states a definition of done.

Per-stream required reading, if you need it outside the prompts:

| Stream | Read |
| --- | --- |
| A | `26-implementation-contracts.md`, `01-architecture.md`, `02-disruption-flow.md` |
| B | `18-decision-assurance-gate.md`, `19-jurisdiction-and-policy-packs.md`, `13-compensation-and-policy.md`, the pack's `rules.yaml` and `test_cases.yaml` |
| C | `11-data-model.md`, `10-data-sources.md`, `12-synthetic-data-plan.md`, `03-agent-design.md`, `06-ai-vs-deterministic.md`, `22-crew-pairing-model.md` |
| D | `21-design-system.md`, `27-ui-specification.md` (all eight screens) |

## Suggested first slices

Everything below is independently startable right now.

| Stream | First slice |
| --- | --- |
| A | Redis Streams event bus, then the orchestrator engine's `open_incident` and `advance` |
| B | Six checks as pure functions with unit tests, then aggregation, then the pack loader |
| C | Delay Risk `execute()` on top of the committed crosswind maths, then the pairing generator |
| D | `WhyPopover` upgrade to a real positioned popover, then the Recovery Workspace layout |

## Sequencing inside the two heavy streams

C and D each carry more than a single sprint of work. Both have an explicit demo-critical prefix, and
both prompts encode it. Summarised here so the plan is legible without opening the prompts:

**C — do these four services first, defer the other six.** Delay Risk, Connection, Crew Impact,
Communication. Those four are the entire Stage 2 narrative. Flight Recovery, Hotel, Transport,
Compensation, Gate/Resource and Analytics can land in Stage 3 without weakening the demo — with the one
caveat that Compensation is what makes the policy screen live, so it is first among the deferred six.

**D — Recovery Workspace and the assurance panel first.** Those two screens are where a judge sees the
gate refuse to execute. The cascade graph, policy citation, executive report and provenance ledger are
each self-contained afterwards.

## Realistic expectations

Four parallel sessions do not produce four times the output. Expect roughly **two to two and a half
times** a single stream, because integration, review and contract alignment consume the rest. That is
still a large win, and it is the difference between Stage 2 being comfortable and being a scramble.

Two failure modes to watch:

- **Divergent conventions.** Two sessions invent two different error shapes. Mitigation: the steering
  file, the shared skills in `.kiro/skills/`, and a single review pass on the first PR from each stream.
- **Silent scope creep.** A session decides it needs a feature outside its lane and builds it. Mitigation:
  the ownership table, and reviewing PR file lists before content.

Review the PR **file list before the code**. If it touches paths the stream does not own, that is the
finding, regardless of how good the code is.
