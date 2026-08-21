---
name: open-stream-pr
description: Open or review a pull request for a TravelOps workstream. Use when finishing a slice, pushing a branch, creating a PR, or reviewing another stream's PR.
---

# Open or review a stream PR

Four accounts work this repository in parallel — A · Core & API, B · Assurance & Policy,
C · Data, Providers & Services, D · Frontend. These conventions are what keep that from
turning into a merge problem.

Ownership model: `docs/28-parallel-workstreams.md`.

## Before you push

1. Run the skill `verify-before-commit`. Everything must be green.
2. Rebase on main: `git pull --rebase origin main`.
3. Check your file list:

```bash
git diff --cached --name-only
```

**Every path must belong to your stream.** If it does not, remove it and raise the change with
the owning stream instead.

Never touch, unless your stream owns it: `backend/migrations/` and `fixtures/api/` (Stream C
only), `backend/app/models/`, `policy_packs/`, `config/`, `docker-compose.yml`, `Makefile`,
`.kiro/`, `docs/`.

## Creating the PR

Branch naming: `stream/<letter>/<slice>` — for example `stream/b/assurance-gate`.

```bash
git push -u origin stream/b/assurance-gate

gh api repos/harshvardhan8058/travelops/pulls \
  -f title="[B] six assurance checks and fail-closed aggregation" \
  -f body="..." -f head="stream/b/assurance-gate" -f base="main"
```

Use `gh api` for PRs in this environment — `gh pr create` is GraphQL-backed and fails.

Title: `[<LETTER>] <what changed>`, under 70 characters. Body: what changed, what you verified
with actual output, anything deliberately left out, and anything another stream needs to know.

## Reviewing someone else's PR

**Read the file list before the code.** If it touches paths that stream does not own, that is
the finding — stop there.

Then check the invariants, because these are the ones a well-meaning change breaks:

| Check | Why it matters |
| --- | --- |
| No LLM import under `services/`, `assurance/`, `policy/` | The boundary is the architecture |
| No `confidence` value used for control flow | Execution is gated deterministically |
| Every action references an `assurance_evaluation` | No side effect without authorisation |
| A `needs_human` action also references an approved decision | Human approval is enforced, not implied |
| No colour literal in a component | The design system holds or it drifts |
| No hardcoded count, amount or status label | Totals come from records |
| No entitlement presented as current law without an `approved` pack | We do not overclaim regulation |
| Tests added for new logic, and existing tests unmodified | A modified test usually means a broken change |

If a test was edited to make a change pass, ask why. That is almost always the wrong fix.

## Merge discipline

- **Everything mergeable merges daily.** A conflict found on day two costs an hour; the day
  before an evaluation it costs the demo.
- `main` must always run.
- Nobody merges their own PR without another stream reviewing it.
- Never force-push to main. Never skip hooks.

## If you are blocked

State the blocker in the PR, then switch to fixture-backed work inside your own paths. Never
unblock yourself by editing another stream's files. Frontend streams are never truly blocked —
`VITE_USE_FIXTURES=true` runs the whole UI with no backend.
