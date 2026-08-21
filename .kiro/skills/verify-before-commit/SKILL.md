---
name: verify-before-commit
description: Run the full TravelOps verification suite before committing or opening a PR. Use before every commit, before pushing a branch, when asked to verify work, or when checking whether a change broke anything.
---

# Verify before commit

Run these in order. **A command exiting without error is not sufficient evidence — read the
output.** Stop at the first failure and fix it rather than continuing.

## Backend

```bash
cd backend
uv run pytest                 # must end "N passed", zero failures
uv run ruff check .           # must print "All checks passed!"
uv run ruff format --check .  # must print "N files already formatted"
```

If you touched models or migrations, also confirm the migration still renders real DDL:

```bash
DATABASE_URL="postgresql+asyncpg://t:t@localhost:5432/t" uv run alembic upgrade head --sql \
  | grep -c "CREATE TABLE"
```

This works with no database running, because `--sql` is offline mode. The count must not
*drop* — currently 34, including `alembic_version`.

## Frontend

```bash
cd frontend
npm run typecheck     # tsc, must be silent
npm run lint          # eslint, must be silent
npm run tokens:check  # must print "OK: no colour literals..."
npm run build         # must print "built in ..."
```

`tokens:check` is not optional. It fails the build on a hand-written colour literal or a
banned hue, which is the only thing standing between the design system and gradual drift.

## Repository

```bash
python3 scripts/verify_docs.py   # every relative markdown link must resolve
git diff --check                 # no trailing whitespace or conflict markers
git status --short                # nothing unexpected staged
```

## Before you push

- No secret, API key, `.env` file, real email address or personal data in the diff.
- `git diff --cached --name-only` shows **only** paths your stream owns.
- No build artifacts: `node_modules/`, `dist/`, `.venv/`, `__pycache__/`,
  `frontend/public/fixtures/`.
- Commit message says *why*, not just *what*.

## If a test you did not write is failing

Do not delete it or mark it skipped. It is protecting an invariant:

| Test | Invariant it protects |
| --- | --- |
| `test_no_llm_in_services.py` | No deterministic service may import an LLM client |
| `test_state_machine.py` | `executing` is unreachable except through `assuring` |
| `test_contracts.py` | Unknown action types and a `confidence` field are both rejected |
| `test_config_fail_closed.py` | Unsafe configuration refuses to start |

If your change breaks one of these, your change is wrong — not the test.
