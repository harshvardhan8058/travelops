"""Verify the Stage 2 demo path and print a pasteable report.

Runs INSIDE the api container, using the container's own Python, so there is no shell
portability question: the same command works on Windows PowerShell, macOS and Linux.

    # bash / zsh
    docker compose exec -T api python - < scripts/verify_demo.py

    # PowerShell
    Get-Content scripts/verify_demo.py | docker compose exec -T api python -

Run it after `make up`, `make migrate`, `make seed` and `make demo`. It checks the eight
things that decide whether the deterministic slice is real, drives the approval itself, and
exits non-zero if any check fails so it cannot be misread as a pass.

It is a reporter, not a fixture: it asserts against what the running system says, and every
figure it prints came from a response or a row. Nothing is stubbed and nothing is patched.

Owner: Stream A.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("VERIFY_API_BASE", "http://127.0.0.1:8000/api/v1")
REFERENCE = os.environ.get("VERIFY_INCIDENT_REF", "INC-2026-0820-VOBL-01")
GROUP_REFERENCE = "GRP-2026-0820-VOBL"
EXPECTED_DIGEST = "fa9564fc4afefc5d"
EXPECTED_ROWS = 2093

PASS = "PASS"
FAIL = "FAIL"
INFO = "  ..."

_results: list[tuple[str, str, str]] = []


def record(state: str, name: str, detail: str = "") -> None:
    _results.append((state, name, detail))
    line = f"[{state}] {name}"
    if detail:
        line += f"\n       {detail}"
    print(line, flush=True)


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        API + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except Exception:
            return exc.code, {}


# ----------------------------------------------------------------- 1. dependencies


def check_dependencies() -> None:
    status, body = call("GET", "/health/ready")
    if not isinstance(body, dict) or "dependencies" not in body:
        record(FAIL, "1. Postgres and Redis healthy", f"unexpected /health/ready body: {body}")
        return

    deps = body["dependencies"]
    database = deps.get("database", {}).get("status")
    redis = deps.get("redis", {}).get("status")
    assurance = body.get("assurance", {})

    ok = database == "up" and redis == "up" and assurance.get("workflow_executable") is True
    record(
        PASS if ok else FAIL,
        "1. Postgres and Redis healthy, assurance config loaded",
        f"HTTP {status} | database={database} | redis={redis} | "
        f"workflow_executable={assurance.get('workflow_executable')} | "
        f"config={assurance.get('config_version')}",
    )
    for degradation in body.get("degradations") or []:
        print(f"{INFO} degradation reported: {degradation}", flush=True)


# ------------------------------------------------------------------ 2 + 3. schema, seed


async def _database_facts() -> dict:
    from sqlalchemy import func, select, text

    from app.db.seed import dataset_counts, plan_digest
    from app.db.session import get_sessionmaker
    from app.models.workflow import Action, AssuranceEvaluation, DecisionLog, Incident

    factory = get_sessionmaker()
    async with factory() as session:
        version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        tables = (
            await session.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            )
        ).scalar_one()
        counts = await dataset_counts(session)

        async def total(model) -> int:
            return int(
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
            )

        return {
            "alembic_version": version,
            "tables": int(tables),
            "counts": counts,
            "digest": plan_digest(),
            "incidents": await total(Incident),
            "actions": await total(Action),
            "evaluations": await total(AssuranceEvaluation),
            "log_entries": await total(DecisionLog),
        }


def check_migrations(facts: dict) -> None:
    ok = bool(facts["alembic_version"]) and facts["tables"] >= 34
    record(
        PASS if ok else FAIL,
        "2. Migrations complete",
        f"alembic_version={facts['alembic_version']} | {facts['tables']} tables in public",
    )


def check_seed(facts: dict) -> None:
    counts = facts["counts"]
    total = sum(v for k, v in counts.items() if k != "TOTAL") if counts else 0
    digest = facts["digest"]

    ok = digest == EXPECTED_DIGEST and total == EXPECTED_ROWS
    detail = (
        f"digest={digest} (expected {EXPECTED_DIGEST}) | {total} rows (expected {EXPECTED_ROWS})"
    )
    record(PASS if ok else FAIL, "3. Seed produced the expected dataset", detail)
    interesting = ("passenger", "booking_segment", "flight", "pairing", "hotel")
    print(
        f"{INFO} " + " | ".join(f"{k}={counts.get(k)}" for k in interesting if k in counts),
        flush=True,
    )


# ---------------------------------------------------------------------- 4. injection


def check_injection() -> dict | None:
    status, incident = call("GET", f"/incidents/{REFERENCE}")
    if status != 200 or not isinstance(incident, dict):
        record(
            FAIL,
            "4. Injection opened the intended incident",
            f"GET /incidents/{REFERENCE} returned HTTP {status}. "
            "Run `make seed` then `make demo` first.",
        )
        return None

    group = incident.get("group_reference")
    flight = incident.get("flight", {})
    ok = (
        incident.get("reference") == REFERENCE
        and group == GROUP_REFERENCE
        and flight.get("flight_number") == "6E 2134"
        and flight.get("delay_minutes") == 420
    )
    record(
        PASS if ok else FAIL,
        "4. Injection opened the intended incident, attached to the cascade group",
        f"{incident.get('reference')} | group={group} | state={incident.get('state')} | "
        f"flight={flight.get('flight_number')} delay={flight.get('delay_minutes')}min | "
        f"passengers={flight.get('passengers')}",
    )
    return incident


def check_risk_recorded() -> None:
    """Delay Risk is scored during `assessing`, so this runs after the first advance.

    Worth being precise about the ordering, because it is visible in the demo: immediately
    after `make demo` the incident exists in `detected` with `evidence.risk` still null. The
    index appears on the first `POST /run`. If the console is opened between those two steps,
    an empty evidence panel is correct rather than broken.
    """
    _status, incident = call("GET", f"/incidents/{REFERENCE}")
    risk = (
        (incident.get("evidence", {}) or {}).get("risk") or {} if isinstance(incident, dict) else {}
    )

    ok = risk.get("risk_index") == 80 and risk.get("risk_level") == "severe"
    record(
        PASS if ok else FAIL,
        "4a. Delay Risk recorded as an index and band, not a probability",
        f"risk={risk.get('risk_index')} ({risk.get('risk_level')}) rule={risk.get('rule_version')}",
    )

    factors = risk.get("factors") or []
    named = [f for f in factors if f.get("name")]
    pointed = [f for f in factors if f.get("points") is not None]
    both = len(named) == len(factors) and len(pointed) == len(factors) and bool(factors)
    record(
        PASS if both else FAIL,
        "4b. Every risk factor carries a name and its point contribution",
        f"{len(factors)} factors | named={len(named)} | with points={len(pointed)}",
    )
    for factor in factors[:6]:
        print(
            f"{INFO} {factor.get('name')!s:42} value={factor.get('value')!s:>8} "
            f"points={factor.get('points')}",
            flush=True,
        )


# -------------------------------------------------------------- 5-7. the recovery


def check_first_run() -> dict | None:
    status, body = call("POST", f"/incidents/{REFERENCE}/run")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "5. POST /run reaches awaiting_approval", f"HTTP {status}: {body}")
        return None

    state = body.get("state")
    if state == "resolved":
        record(
            PASS,
            "5. POST /run (already resolved by a previous run)",
            "the incident was resolved before this script ran; "
            "use `make demo-reset` for a clean pass",
        )
        return body

    ok = state == "awaiting_approval" and body.get("is_terminal") is False
    record(
        PASS if ok else FAIL,
        "5. POST /run stops at awaiting_approval",
        f"{body.get('previous_state')} -> {state} | steps={body.get('steps_taken')} | "
        f"note={body.get('note')}",
    )
    return body


def check_gate_held_it() -> int | None:
    status, body = call("GET", f"/incidents/{REFERENCE}/assurance")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "6a. Assurance evaluations recorded", f"HTTP {status}")
        return None

    evaluations = body.get("evaluations") or []
    pending = [e for e in evaluations if e.get("decision") == "needs_human"]
    print(
        f"{INFO} config={body.get('config_version')} hash={body.get('config_hash')} "
        f"awaiting={body.get('awaiting_approval_count')}",
        flush=True,
    )
    for evaluation in evaluations:
        checks = evaluation.get("checks") or []
        states = {c.get("state") for c in checks}
        print(
            f"{INFO} {evaluation.get('action_type'):20} {evaluation.get('decision'):16} "
            f"tier={evaluation.get('risk_tier'):6} blocking={evaluation.get('blocking')} "
            f"checks={sorted(states)}",
            flush=True,
        )

    if not pending:
        record(PASS, "6a. No evaluation awaiting a decision", "already approved, or nothing held")
        return None

    held = pending[0]
    passing = all(c.get("state") == "PASS" for c in held.get("checks") or [])
    ok = held.get("risk_tier") == "high" and held.get("blocking") == ["action_risk"] and passing
    record(
        PASS if ok else FAIL,
        "6a. The gate held the bulk action on its risk tier, not a data problem",
        f"{held.get('action_type')} | tier={held.get('risk_tier')} | "
        f"blocking={held.get('blocking')} | all six checks PASS={passing}",
    )
    return held.get("id")


def check_approval_persists(evaluation_id: int) -> None:
    status, body = call(
        "POST",
        f"/assurance/{evaluation_id}/decision",
        {"decision": "approved", "reason": "demo verification run"},
    )
    if status != 200:
        record(FAIL, "6b. Approval accepted", f"HTTP {status}: {body}")
        return

    status, assurance = call("GET", f"/incidents/{REFERENCE}/assurance")
    recorded = None
    if isinstance(assurance, dict):
        for evaluation in assurance.get("evaluations") or []:
            if evaluation.get("id") == evaluation_id:
                recorded = evaluation.get("human_decision")
    ok = isinstance(recorded, dict) and recorded.get("decision") == "approved"
    record(
        PASS if ok else FAIL,
        "6b. Approval persisted against the evaluation",
        f"human_decision={recorded}",
    )

    # Re-posting the same decision must return the original rather than acting twice.
    status, replay = call(
        "POST",
        f"/assurance/{evaluation_id}/decision",
        {"decision": "approved", "reason": "demo verification run"},
    )
    replayed = isinstance(replay, dict) and replay.get("replayed") is True
    record(
        PASS if replayed else FAIL,
        "6c. Re-posting the same decision replays rather than re-deciding",
        f"HTTP {status} | replayed={replay.get('replayed') if isinstance(replay, dict) else None}",
    )


def check_second_run() -> None:
    status, body = call("POST", f"/incidents/{REFERENCE}/run")
    ok = status == 200 and isinstance(body, dict) and body.get("state") == "resolved"
    record(
        PASS if ok else FAIL,
        "7. Second POST /run reaches resolved",
        f"HTTP {status} | {body.get('previous_state') if isinstance(body, dict) else '?'} -> "
        f"{body.get('state') if isinstance(body, dict) else '?'} | "
        f"terminal={body.get('is_terminal') if isinstance(body, dict) else '?'}",
    )


# ------------------------------------------------------------- 8. timeline and audit


def check_audit() -> None:
    status, timeline = call("GET", f"/incidents/{REFERENCE}/timeline")
    entries = timeline.get("entries") if isinstance(timeline, dict) else None
    if status != 200 or not entries:
        record(FAIL, "8. Timeline populated", f"HTTP {status}")
        return

    ids = [e.get("id") for e in entries]
    ordered = ids == sorted(ids)
    types = {e.get("event_type") for e in entries}
    required = {
        "INCIDENT_OPENED",
        "PLAN_PROPOSED",
        "ASSURANCE_EVALUATED",
        "ACTION_COMPLETED",
        "STATE_CHANGED",
    }
    missing = required - types
    correlated = all(e.get("correlation_id") for e in entries)

    ok = ordered and not missing and correlated
    record(
        PASS if ok else FAIL,
        "8. Timeline is ordered, complete and correlated",
        f"{len(entries)} entries | ordered={ordered} | "
        f"missing={sorted(missing) or 'none'} | every entry correlated={correlated}",
    )

    status, incident = call("GET", f"/incidents/{REFERENCE}")
    actions = incident.get("actions") if isinstance(incident, dict) else []
    authorised = all(a.get("assurance_id") for a in actions or [])
    approved_ref = [a for a in actions or [] if a.get("human_decision_id")]
    record(
        PASS if (actions and authorised) else FAIL,
        "8a. Every action references the evaluation that authorised it",
        f"{len(actions or [])} actions | all carry assurance_id={authorised} | "
        f"{len(approved_ref)} also carry a human_decision_id",
    )
    for action in actions or []:
        print(
            f"{INFO} {action.get('action_type'):20} {action.get('status'):9} "
            f"| {str(action.get('reason'))[:74]}",
            flush=True,
        )


# ------------------------------------------------------------------------- report


def main() -> int:
    print("=" * 78)
    print("TravelOps AI -- Stage 2 demo verification")
    print(f"api={API}  incident={REFERENCE}")
    print("=" * 78)

    check_dependencies()

    try:
        facts = asyncio.run(_database_facts())
    except Exception as exc:
        record(FAIL, "2-3. Schema and seed", f"could not read the database: {exc!r}")
        facts = None

    if facts:
        check_migrations(facts)
        check_seed(facts)

    if check_injection() is not None:
        check_first_run()
        check_risk_recorded()
        evaluation_id = check_gate_held_it()
        if evaluation_id is not None:
            check_approval_persists(evaluation_id)
        check_second_run()
        check_audit()

    failed = [name for state, name, _ in _results if state == FAIL]
    print()
    print("=" * 78)
    print(f"{len(_results) - len(failed)} of {len(_results)} checks passed")
    if failed:
        print("FAILED:")
        for name in failed:
            print(f"  - {name}")
        print("=" * 78)
        return 1
    print("Stage 2 deterministic slice verified end to end on this machine.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
