---
name: implement-assurance-check
description: Implement or modify one of the six Decision Assurance Gate checks, or the fail-closed aggregation that combines them. Use when working on app/assurance/, when a check must return PASS WARN or FAIL, or when changing how an action is authorised.
---

# Implement an assurance check

The gate is the authorisation boundary for the whole system. It replaces LLM self-reported
confidence entirely. Get this wrong and every safety claim in the project becomes false.

Owner: Stream B. Full design: `docs/18-decision-assurance-gate.md`.

## The rules that must not bend

1. **Checks are pure functions.** No database, no network, no clock reads. Everything arrives
   in arguments. That is what makes them reproducible and trivially testable.
2. **Three states, never a boolean.** `PASS`, `WARN`, `FAIL`. Collapsing `WARN` into a boolean
   destroys the distinction the config depends on.
3. **Every result carries a machine-readable `reason_code`** from `ReasonCode`. The UI maps
   codes to copy; it never parses free text.
4. **A fact present but `None` counts as absent.** That single distinction is what stops a null
   being silently treated as a legal answer.
5. **A source with no timestamp is `FAIL`, never assumed fresh.**
6. **An action type absent from `config.risk_tiers` is `high` risk.** Unknown means dangerous.

## Aggregation order — implement exactly this

```text
1. Missing config, unknown action type, or unknown rule operator  -> FAIL
2. Any FAIL                    -> needs_human. Nothing executes.
3. risk_tier == high           -> needs_human even when every check passes
4. A WARN                      -> execute_flagged ONLY if
                                  config.warn_permitted(action_type, check) is true
5. Otherwise                   -> execute
```

Multiple warnings never become safer by aggregation. There is no global soft-failure bypass —
`warn_allowed_actions` in `config/assurance.v1.yaml` is the only route to `execute_flagged`,
and it currently lists three low-risk reversible actions.

## Contract you must not change

`app/assurance/contract.py` is fixed. `CheckName`, `CHECK_ORDER`, `CheckResult`,
`AssuranceResult` and `AssuranceConfig` are consumed by Stream A, the frontend and the
fixtures. Adding a field is a cross-stream change — raise it, do not just do it.

## Immutability

`AssuranceResult` is a record, not a mutable object. A corrected decision requires a **new
evaluation**, never an update to an existing row. `config_version` and `config_hash` are
recorded on every evaluation so a replay uses the semantics that applied at the time.

## Test it like this

```python
def test_stale_source_without_permission_blocks():
    result = sources_fresh(
        sources={"metar:VOBL": now - timedelta(minutes=74)},
        now=now,
        config=config,
        action_type="notify_passengers",   # not in warn_allowed_actions
    )
    assert result.state is CheckState.failed
    assert result.reason_code is ReasonCode.SOURCE_STALE
```

Cover for each check: the passing case, the failing case, the boundary value, and the
missing-input case. Then confirm the 23 cases in
`policy_packs/in-moca-charter-2019/2019.02/test_cases.yaml` still pass.

## Definition of done

All 23 pack test cases pass, `needs_human` is produced for every hard failure and every
high-risk action, and `uv run pytest` is green.
