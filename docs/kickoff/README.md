# Kickoff — one file per account

Six ready-to-paste prompts. Each file contains **one** fenced block that is the complete
prompt: project context, required reading, owned paths, branch name, ordered deliverables,
non-negotiable rules and a definition of done. Nothing to fill in.

## Before you open any session

Wave 0 must be on `main`. Confirm it:

```bash
git pull && ls backend/app/main.py frontend/src/design/tokens.css config/assurance.v1.yaml
```

If those exist, the streams are unblocked. If not, merge the Wave 0 PR first — six sessions
against an empty repo will collide on `docker-compose.yml` and waste a day.

## The six accounts

| Account | Stream | Prompt | Branch |
| --- | --- | --- | --- |
| 1 | Core | [`stream-a-core.md`](stream-a-core.md) | `stream/a/orchestrator` |
| 2 | Assurance + Policy | [`stream-b-assurance-policy.md`](stream-b-assurance-policy.md) | `stream/b/assurance-gate` |
| 3 | Data + Providers | [`stream-c-data-providers.md`](stream-c-data-providers.md) | `stream/c/data-providers` |
| 4 | Services | [`stream-d-services.md`](stream-d-services.md) | `stream/d/services` |
| 5 | Frontend shell | [`stream-e-frontend-shell.md`](stream-e-frontend-shell.md) | `stream/e/ops-board` |
| 6 | Frontend workspace | [`stream-f-frontend-workspace.md`](stream-f-frontend-workspace.md) | `stream/f/recovery-workspace` |

Ownership model and rationale: [`../28-parallel-workstreams.md`](../28-parallel-workstreams.md).
Wave sequencing and daily rhythm: [`../29-kickoff-prompts.md`](../29-kickoff-prompts.md).

## Why each prompt ends with a question

Every prompt closes by asking the session to state its plan before writing code. A scoped
session that confirms its approach first produces reviewable increments; one told "build the
backend" wanders. The extra thirty seconds is the cheapest quality control available.

## What these prompts already account for

Wave 0 shipped working code, so each prompt lists what is **already done** and must not be
rebuilt. That matters most for two streams:

- **Stream E** already has tokens, primitives, the app shell, the Ops Board and the
  timeline. Its first slice is the `WhyPopover` upgrade, not a scaffold.
- **Stream D** already has the crosswind trigonometry and its tests. Its first slice is the
  risk rule set on top.

Without that, two accounts would spend their first hours recreating committed work.

## Daily rhythm

| When | Action |
| --- | --- |
| Session start | `git pull --rebase origin main` before touching anything |
| During | Small commits; push the branch often |
| Slice done | PR titled `[<LETTER>] <slice>`; another stream reviews |
| End of day | Everything mergeable is merged. `main` must always run |

Review the PR **file list before the code**. If it touches paths the stream does not own,
that is the finding.

## If a stream is blocked

State the blocker, then switch to fixture-backed work inside your own paths. Never fix it by
editing another stream's files. The frontend streams are never blocked by definition, because
`VITE_USE_FIXTURES=true` serves committed fixtures with no backend running.
