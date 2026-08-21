# 32. Skills — reusable procedures for every agent

Eight skills live in [`.kiro/skills/`](../.kiro/skills/). They load automatically in any Kiro
session opened on this repository, so **no agent needs to search outside the repo** for how to
do a recurring task correctly.

## How they work

Kiro loads only each skill's `name` and `description` at startup, then loads the full
instructions when a request matches. So a session that says *"implement the sources_fresh
check"* gets the assurance rules without anyone pasting them.

Committing `.kiro/skills/` to git is the documented way to share workflows across a team, which
is exactly our six-account situation.

## The eight

| Skill | Activates when | Primary stream |
| --- | --- | --- |
| `verify-before-commit` | Before any commit, push or PR | **All** |
| `open-stream-pr` | Finishing a slice, creating or reviewing a PR | **All** |
| `add-api-endpoint` | Working in `app/api/`, changing a response shape | A |
| `implement-assurance-check` | Working in `app/assurance/`, anything about authorising an action | B |
| `add-policy-rule` | Working in `policy_packs/` or `app/policy/`, any entitlement | B |
| `add-provider` | Working in `app/providers/`, any external boundary | C |
| `implement-service` | Working in `app/services/`, any domain logic | D |
| `build-ui-screen` | Any React, TypeScript or styling under `frontend/src` | E, F |

Two are deliberately universal. `verify-before-commit` carries the exact commands and the
warning that a clean exit code is not evidence. `open-stream-pr` carries the ownership rules and
the reviewer's invariant checklist.

## Skills versus steering

`.kiro/steering/travelops.md` uses `inclusion: always`, so its rules are in context for every
session on this repo: the architecture taxonomy, the assurance boundary, the data rules, the UI
prohibitions.

Skills are the *procedures*. Steering says "never gate on model confidence"; the
`implement-assurance-check` skill says how to write the check that replaces it.

Rule of thumb: an always-true constraint belongs in steering, a repeatable multi-step procedure
belongs in a skill.

## Each skill encodes the mistake it prevents

They are written around the specific way each task goes wrong, because that is what makes them
worth loading:

- `implement-assurance-check` — that collapsing `WARN` into a boolean destroys the config's
  meaning, and that an action type absent from the tier map is **high** risk, not low.
- `add-policy-rule` — that a plausible invented figure is worse than a missing one, because
  nobody will question it.
- `implement-service` — that a service failing to import an LLM client is the architecture, and
  the AST test enforcing it is not optional.
- `add-provider` — that returning empty data on failure is the one outcome that must never
  happen, because it looks like success.
- `build-ui-screen` — that colour must carry meaning, so the accent can never also be a status.
- `verify-before-commit` — that a failing test you did not write is protecting an invariant, and
  editing it to pass is the wrong fix.

## Adding a skill

```text
.kiro/skills/<name>/SKILL.md
```

`name` must match the folder exactly: lowercase letters, numbers and hyphens, 64 characters
maximum. `description` must say **what it does and when to use it**, 1024 characters maximum —
Kiro matches requests against it, so "helps with policy" will not activate reliably.

Keep `SKILL.md` actionable; put long reference material in a `references/` subfolder and point
at it, since the full body loads on activation.

Skill files are owned by Stream A, like the steering file, because a change affects all six
sessions.
