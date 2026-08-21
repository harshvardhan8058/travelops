# 29. Kickoff — Sequencing and Account Assignment for Four Kiro Accounts

Read [`28-parallel-workstreams.md`](28-parallel-workstreams.md) for the ownership model. This document is
the execution script: what order things happen in, who sits at which account, and how to get the most out
of each one.

> **Just want to start?** [`kickoff/`](kickoff/README.md) has the four ready-to-paste prompts, one per
> account, with no placeholders to fill. **Those files are the source of truth for what each stream
> builds.** This document deliberately does not repeat them, so there is nothing to keep in sync.

## The sequencing that decides your throughput

```text
WAVE 0   one session only          DONE, on main    scaffold + contracts + fixtures
   │
   ▼
WAVE 1   all four in parallel      days             features, no waiting, no collisions
   │
   ▼
DAILY    integrate, review, merge
```

Wave 0 existed because four sessions cannot create the same `docker-compose.yml`. It is **complete and
merged**, which is why Wave 1 needs no contract-freeze day.

### What Wave 0 already delivered

Every account's prompt lists this, but it is worth stating once centrally, because the most expensive
mistake available right now is an account rebuilding it:

| Area | Delivered |
| --- | --- |
| Root | `docker-compose.yml` (api, web, postgres, redis all healthy), `Makefile`, `.env.example`, `.dockerignore` in both build contexts |
| Backend | `pyproject.toml` (uv), full `app/` tree, health/ready/system-mode endpoints, fail-closed `config.py` |
| Schema | All 33 SQLAlchemy models, `0001_initial_schema` rendering 34-table Postgres DDL |
| Contracts | `events/types.py` (nine typed events), `assurance/contract.py`, `schemas/provenance.py` |
| Gate config | `config/assurance.v1.yaml` with version and hash |
| Policy | The MoCA Passenger Charter pack: 40 rules, 23 test cases, 8 review questions |
| Frontend | Vite + TS + Tailwind with replaced palette, primitives, AppShell, Ops Board, Decision Timeline |
| Fixtures | Eleven API fixtures plus `data/fixtures/bengaluru_storm.yaml` |
| Guards | 103 passing backend tests including AST, state-machine, contract, fail-closed and container-path guards |

**Exit gate, already met:** `docker compose up` starts all four services, `/health/ready` returns
dependency status, the console renders at `:5173` against fixtures, and `alembic upgrade head` runs clean.

---

## The four accounts

Full prompts are in [`kickoff/`](kickoff/README.md). Summary so you can assign them without opening four
files:

| Account | Stream | Owns | Branch | Token load |
| --- | --- | --- | --- | --- |
| 1 | **A · Core & API** | orchestrator, events, API, agents, LLM, CLI, config, compose, `.kiro/`, `docs/` | `stream/a/core` | Medium |
| 2 | **B · Assurance & Policy** | assurance, policy, policy packs, gate config | `stream/b/assurance` | **Lowest** |
| 3 | **C · Data, Providers & Services** | models, migrations, providers, the ten services, `data/`, `fixtures/` | `stream/c/data-services` | **Second highest** |
| 4 | **D · Frontend** | all of `frontend/` | `stream/d/frontend` | **Highest** |

### Assign your token limits like this

**Highest limit → account 4 (Frontend). Second highest → account 3 (Data, Providers & Services).**

Account 2 will consume the least of the four and carries the most correctness risk, so if one account has
a noticeably smaller quota, put Stream B on it — and put your most careful reviewer there. The measured
reasoning behind this ranking is in
[`28-parallel-workstreams.md`](28-parallel-workstreams.md#which-account-will-burn-the-most-tokens).

### Pairing four people to four accounts

One account each. No double-ups, which is the main practical gain from dropping to four.

| Person | Account |
| --- | --- |
| Harshvardhan Sharma | 1 · Core & API |
| Karthikeyan D | 2 · Assurance & Policy |
| Harshvardhan Jha | 3 · Data, Providers & Services |
| Sabyasachin Biswal | 4 · Frontend |

If the frontend is not someone's strength, swap 3 and 4 rather than splitting either — both are single-owner
by design, and both prompts carry their own phase ordering.

---

## Universal rules every account inherits

These are in `.kiro/steering/travelops.md`, which loads automatically in every session in this repository.
They are reproduced here only so a reviewer can check a PR against them without opening the steering file:

```text
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
```

Shared procedures live in `.kiro/skills/` — eight of them, covering how to add an endpoint, a service, a
provider, a policy rule, an assurance check, a UI screen, how to verify before committing, and how to open
a stream PR. Point sessions at the relevant skill instead of re-explaining a convention.

---

## Daily rhythm

| When | Action |
| --- | --- |
| Session start | `git pull --rebase origin main` before touching anything |
| During | Commit small working increments; push your branch often |
| Slice complete | Open a PR titled `[<LETTER>] <slice>`; the next stream in rotation reviews |
| End of day | **Everything mergeable is merged.** `main` must always run |

With four accounts the review rotation is fixed, so no PR waits on a volunteer:

```text
A reviews B    B reviews C    C reviews D    D reviews A
```

Review the PR **file list first**. If it touches paths the stream does not own, that is the finding —
before reading any code.

## Getting maximum output per account

- **Scope every prompt.** A session told "build the backend" wanders. One told "build the six checks as
  pure functions with unit tests" ships. The four kickoff prompts are already scoped this way; do not
  paraphrase them into something broader.
- **Keep `LLM_MODE=fixture` while developing.** Live inference during iteration burns Groq quota for no
  benefit. Switch to live only when rehearsing the demo.
- **Respect the phase ordering in the C and D prompts.** Both streams have more than a sprint of work and
  both prompts front-load the Stage 2 demo. Reordering them is how a quota ceiling turns into a missing
  demo instead of a missing screen.
- **Let each session own its tests.** A stream that writes its own tests does not need another stream to
  verify it, which removes a synchronisation point.
- **Do not ask a session to re-read the whole docs set every turn.** Steering loads automatically; point at
  the two or three specific documents that matter, and at the relevant skill.
- **When a stream finishes early**, it does not wander into another stream's paths. It picks the next slice
  from its own phase list, or writes tests, or takes a review.

## When a stream is blocked

State the blocker in the PR or to the owning stream, then **switch to fixture-backed work in your own
paths.** Never sit idle and never fix it by editing someone else's files.

Stream D is never blocked by definition: `VITE_USE_FIXTURES=true` is the default and serves committed
fixtures with no backend running. Stream C is never blocked either, because every provider has a fixture
implementation. The two streams with real external dependencies are A (Redis, Postgres) and B (nothing —
its checks are pure functions), which is the correct shape for a system that has to demo offline.
