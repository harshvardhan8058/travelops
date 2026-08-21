# Kickoff — one file per account

Four ready-to-paste prompts. Each file contains **one** fenced block that is the complete
prompt: project context, required reading, owned paths, branch name, ordered deliverables,
non-negotiable rules and a definition of done. Nothing to fill in.

## Before you open any session

Wave 0 must be on `main`. Confirm it:

```bash
git pull && ls backend/app/main.py frontend/src/design/tokens.css config/assurance.v1.yaml
```

If those exist, all four streams are unblocked and can start simultaneously. Wave 0 already
committed the schema, the initial migration, the typed event contracts, the assurance
contract and a fixture for every endpoint the frontend needs, so there is no contract-freeze
day to wait through.

## The four accounts

| Account | Stream | Prompt | Branch | Token load |
| --- | --- | --- | --- | --- |
| 1 | Core & API | [`stream-a-core.md`](stream-a-core.md) | `stream/a/core` | Medium |
| 2 | Assurance & Policy | [`stream-b-assurance-policy.md`](stream-b-assurance-policy.md) | `stream/b/assurance` | **Lowest** |
| 3 | Data, Providers & Services | [`stream-c-data-services.md`](stream-c-data-services.md) | `stream/c/data-services` | **Second highest** |
| 4 | Frontend | [`stream-d-frontend.md`](stream-d-frontend.md) | `stream/d/frontend` | **Highest** |

**Put your largest token limit on account 4, and your second largest on account 3.** Account
2 consumes the least and matters the most — give it your best reviewer rather than your
biggest quota. Full reasoning with measured numbers:
[`../28-parallel-workstreams.md`](../28-parallel-workstreams.md#which-account-will-burn-the-most-tokens).

Ownership model and rationale: [`../28-parallel-workstreams.md`](../28-parallel-workstreams.md).
Wave sequencing and daily rhythm: [`../29-kickoff-prompts.md`](../29-kickoff-prompts.md).

## Why each prompt ends with a question

Every prompt closes by asking the session to state its plan before writing code. A scoped
session that confirms its approach first produces reviewable increments; one told "build the
backend" wanders. The extra thirty seconds is the cheapest quality control available.

## What these prompts already account for

Wave 0 shipped working code, so each prompt lists what is **already done** and must not be
rebuilt. Without that, accounts would spend their first hours recreating committed work. The
three that matter most:

- **Stream D** already has tokens, primitives, the app shell, the Ops Board and the
  timeline. Its first slice is the `WhyPopover` upgrade, not a scaffold.
- **Stream C** already has all 33 models, the initial migration, and the crosswind
  trigonometry with its tests. Its first slice is the reference loaders, and Delay Risk is a
  rule set on top of maths that already exists.
- **Stream B** already has the assurance contract and the versioned gate config. Its first
  slice is the six checks as pure functions.

## Two prompts carry more than a sprint

Streams C and D both have an explicit phase order in their prompt: a demo-critical prefix,
then the rest. If either account hits its quota, it loses a screen or a deferred service —
never the Stage 2 demo. Do not reorder those phases to "finish the backend first"; the
ordering is what makes a quota ceiling survivable.

## Daily rhythm

| When | Action |
| --- | --- |
| Session start | `git pull --rebase origin main` before touching anything |
| During | Small commits; push the branch often |
| Slice done | PR titled `[<LETTER>] <slice>`; the next stream in rotation reviews |
| End of day | Everything mergeable is merged. `main` must always run |

Review rotation, so no PR waits on a volunteer:

```text
A reviews B    B reviews C    C reviews D    D reviews A
```

Review the PR **file list before the code**. If it touches paths the stream does not own,
that is the finding, regardless of how good the code is.

## If a stream is blocked

State the blocker, then switch to fixture-backed work inside your own paths. Never fix it by
editing another stream's files. Stream D is never blocked by definition, because
`VITE_USE_FIXTURES=true` serves committed fixtures with no backend running.
