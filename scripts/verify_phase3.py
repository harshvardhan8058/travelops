"""Phase 3 end-to-end verification -- reasoning agents in all three LLM modes.

Run against a live stack (after seed + inject --cascade):

    LLM_MODE=off     python scripts/verify_phase3.py
    LLM_MODE=fixture python scripts/verify_phase3.py
    LLM_MODE=live    python scripts/verify_phase3.py   # requires GROQ_API_KEY

Covers the whole Phase 3 chain:

    facts -> planner -> reflection -> assurance -> human approval -> execution
          -> explanation/report -> replay/audit

  off     -- playbook only, no model plan, no artefacts; the Phase 2 path unchanged
  fixture -- playbook + planner-agent candidate, reflection recorded, artefacts available
  live    -- same as fixture but with a real Groq call

In every mode: no task advances without its own evaluation, the high-risk action carries an
attributed human decision, and execution happened through the recorded path.

Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

API = os.environ.get("API_BASE", "http://127.0.0.1:8000/api/v1")
GROUP = "GRP-2026-0820-VOBL"
INCIDENT = "INC-2026-0820-VOBL-01"

PASS = "[PASS]"
FAIL = "[FAIL]"
failures: list[str] = []


def get(path: str) -> tuple[int, dict]:
    url = f"{API}{path}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"raw": body[:500]}


def post(path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:500]}


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print(f"{PASS} {label}")
        if detail:
            print(f"       {detail}")
    else:
        failures.append(label)
        print(f"{FAIL} {label}")
        if detail:
            print(f"       {detail}")


def check_reflection(mode: str, reflection: dict, proposed: list[str]) -> None:
    """Verify reflection without requiring a live model to propose invalid work."""
    dropped = reflection.get("dropped_actions")
    findings = reflection.get("findings")
    kept = reflection.get("kept_actions")

    # A non-empty drop is a property of the committed fixture, not of reflection itself. A live
    # model that proposes only executable actions should pass with no findings; requiring it to
    # propose something invalid rewards worse model output.
    if mode == "fixture":
        check(
            isinstance(dropped, list) and "evaluate_entitlements" in dropped,
            "fixture reflection dropped the unimplemented entitlement action",
            f"dropped={dropped}",
        )
    else:
        check(
            isinstance(dropped, list) and isinstance(findings, list),
            "live reflection recorded findings and dropped-action lists",
            f"dropped={dropped}, findings_type={type(findings).__name__}",
        )
        check(
            reflection.get("rejected") is False,
            "live planner candidate survived reflection",
            f"rejected={reflection.get('rejected')}",
        )
        check(
            isinstance(kept, list) and kept == proposed,
            "live reflection kept-actions match the persisted plan",
            f"kept={kept}, persisted={proposed}",
        )

    check(
        "evaluate_entitlements" not in proposed,
        "the action with no registered service is not in the persisted plan",
        f"persisted={proposed}",
    )


def detect_mode() -> str:
    status, body = get("/system/mode")
    if status == 200:
        return body.get("llm_mode", "unknown")
    return os.environ.get("LLM_MODE", "unknown")


def drive_group() -> None:
    """Open and advance the group until resolved, approving whatever the gate holds."""
    post(f"/incident-groups/{GROUP}/open")
    for _ in range(12):
        _status, state = post(f"/incident-groups/{GROUP}/run")
        held = []
        for member in state.get("members", []):
            ref = member.get("incident_reference")
            if not ref:
                continue
            _, assurance = get(f"/incidents/{ref}/assurance")
            for ev in assurance.get("evaluations", []):
                if ev.get("decision") == "needs_human" and not ev.get("human_decision"):
                    held.append(ev["id"])
        if not held:
            break
        for eid in held:
            post(f"/assurance/{eid}/decision", {"decision": "approved", "reason": "phase3 test"})


def main() -> int:
    mode = detect_mode()
    print("=" * 70)
    print(f"Phase 3 verification | LLM_MODE={mode}")
    print("=" * 70)

    # 1. Drive the group to resolved
    print("\n--- driving the group ---")
    drive_group()
    status, detail = get(f"/incident-groups/{GROUP}")
    check(status == 200, "group detail returns 200")
    group_state = detail.get("state", "?")
    check(
        group_state == "resolved",
        f"group reaches resolved (state={group_state})",
    )

    # 2. Check plans on one member incident
    print("\n--- plans ---")
    status, plans_body = get(f"/incidents/{INCIDENT}/plans")
    plans = plans_body.get("plans", []) if status == 200 else []
    generators = [p.get("generator") for p in plans]
    variants = [p.get("variant_key") for p in plans]

    if mode == "off":
        # Off mode: exactly the playbook, no planner candidate
        check(
            len(plans) >= 1,
            f"off mode has at least 1 plan (got {len(plans)})",
            f"generators={generators}",
        )
        check(
            "planner-agent" not in generators,
            "off mode: no planner-agent plan",
            f"generators={generators}",
        )
    else:
        # Fixture or live: playbook + planner
        check(
            len(plans) >= 2,
            f"fixture/live mode has 2+ plans (got {len(plans)})",
            f"generators={generators}",
        )
        check(
            "fallback-playbook" in generators,
            "playbook plan present",
        )
        check(
            "planner-agent" in generators,
            "planner-agent plan present",
            f"variants={variants}",
        )

    # 3. Plan comparison shows generator
    print("\n--- plan comparison ---")
    status, comparison = get(f"/incidents/{INCIDENT}/plans/comparison")
    if status == 200:
        candidates = comparison.get("candidates", [])
        comp_generators = [c.get("generator") for c in candidates]
        check(
            all(g is not None for g in comp_generators),
            "comparison carries generator on every candidate",
            f"generators={comp_generators}",
        )
    else:
        check(False, f"comparison endpoint returned {status}")

    # 4. Explanation endpoint
    print("\n--- explanation ---")
    status, explanation = get(f"/incidents/{INCIDENT}/explanation")
    if mode == "off":
        check(
            status == 404,
            "off mode: explanation returns 404",
            f"message={explanation.get('error', {}).get('message', '?')[:80]}",
        )
    else:
        check(status == 200, f"explanation returns 200 (got {status})")
        if status == 200:
            check(
                len(explanation.get("explanation", "")) > 50,
                f"explanation has content (len={len(explanation.get('explanation', ''))})",
            )
            check(
                explanation.get("generator") is not None,
                f"explanation names generator={explanation.get('generator')}",
            )

    # 5. Report endpoint
    print("\n--- report ---")
    status, report = get(f"/reports/{GROUP}")
    if mode == "off":
        check(
            status == 404,
            "off mode: report returns 404",
        )
    else:
        check(status == 200, f"report returns 200 (got {status})")
        if status == 200:
            check(
                len(report.get("sections", [])) >= 3,
                f"report has 3+ sections (got {len(report.get('sections', []))})",
            )
            check(
                len(report.get("summary", "")) > 50,
                f"report summary has content (len={len(report.get('summary', ''))})",
            )
            check(
                report.get("generator") is not None,
                f"report names generator={report.get('generator')}",
            )
            metric_refs = report.get("metric_refs", [])
            check(
                len(metric_refs) > 0,
                f"report cites {len(metric_refs)} metric refs",
            )

    # 6. Replay contains the planner entry (fixture/live only)
    print("\n--- replay ---")
    status, replay = get(f"/incident-groups/{GROUP}/replay")
    if status == 200:
        frames = replay.get("frames", [])
        events = {f.get("event_type") for f in frames}
        check("PLAN_PROPOSED" in events, "PLAN_PROPOSED in group replay")
        if mode != "off":
            planner_frames = [
                f for f in frames
                if f.get("event_type") == "PLAN_PROPOSED"
                and f.get("detail", {}).get("generator") == "planner-agent"
            ]
            check(
                len(planner_frames) > 0,
                "planner-agent PLAN_PROPOSED visible in replay",
            )
    else:
        check(False, f"replay returned {status}")

    # 7. Reflection is recorded, not just applied
    print("\n--- reflection ---")
    if mode == "off":
        check(True, "off mode: no reflection to record (no agent ran)")
    else:
        status, replay = get(f"/incidents/{INCIDENT}/replay")
        agent_frames = [
            f
            for f in (replay.get("frames", []) if status == 200 else [])
            if f.get("event_type") == "PLAN_PROPOSED"
            and f.get("detail", {}).get("generator") == "planner-agent"
        ]
        check(bool(agent_frames), "the agent's PLAN_PROPOSED is on the incident replay")
        if agent_frames:
            detail = agent_frames[-1].get("detail", {})
            reflection = detail.get("reflection") or detail.get("agent") or {}
            check(
                bool(reflection),
                "reflection is recorded on the plan event",
                f"keys={sorted(reflection.keys())}",
            )
            proposed = detail.get("actions") or []
            check_reflection(mode, reflection, proposed)

    # 8. Authorship reached the gate: a model-authored plan is assured under model authorship
    print("\n--- authorship at the gate ---")
    status, plans_body = get(f"/incidents/{INCIDENT}/plans")
    agent_plans = [
        p for p in (plans_body.get("plans", []) if status == 200 else [])
        if p.get("generator") == "planner-agent"
    ]
    if mode == "off":
        check(not agent_plans, "off mode: no model-authored plan exists to authorise")
    else:
        check(bool(agent_plans), "a model-authored plan exists")

    # Whatever the author, no task may advance without its own evaluation. This is the
    # one-path-to-execution property, checked over every plan rather than only the driving one.
    unassured: list[str] = []
    for plan in plans_body.get("plans", []) if status == 200 else []:
        for task in plan.get("tasks", []):
            if task.get("state") not in {"proposed", None} and task.get("assurance_id") is None:
                unassured.append(f"plan {plan['id']} task {task['id']} ({task['action_type']})")
    check(
        not unassured,
        "no task advanced without its own evaluation",
        f"offenders={unassured}" if unassured else "",
    )

    # 9. A person authorised the high-risk work, and it is attributed
    print("\n--- human approval ---")
    status, assurance = get(f"/incidents/{INCIDENT}/assurance")
    evaluations = assurance.get("evaluations", []) if status == 200 else []
    high_risk = [e for e in evaluations if e.get("risk_tier") == "high"]
    check(bool(high_risk), f"a high-risk action was evaluated (got {len(high_risk)})")
    decided = [e for e in high_risk if e.get("human_decision")]
    check(
        bool(decided),
        "the high-risk action carries a human decision",
        f"actors={[e['human_decision'].get('actor_id') for e in decided]}",
    )
    for evaluation in decided:
        check(
            bool(evaluation["human_decision"].get("actor_id")),
            f"eval {evaluation['id']} names the operator who decided it",
        )

    # 10. Execution happened through the recorded path
    print("\n--- execution ---")
    status, detail = get(f"/incidents/{INCIDENT}")
    plan = detail.get("plan", {}) if status == 200 else {}
    executed = [t for t in plan.get("tasks", []) if t.get("state") == "succeeded"]
    check(bool(executed), f"tasks executed (got {len(executed)})")
    check(
        detail.get("state") in {"resolved", "executing", "awaiting_approval", "blocked"},
        f"incident reached a legitimate state ({detail.get('state')})",
    )

    # Summary
    print("\n" + "=" * 70)
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        print("=" * 70)
        return 1
    total = sum(1 for line in sys.stdout.getvalue().splitlines() if PASS in line) if hasattr(sys.stdout, 'getvalue') else "?"
    print(f"All checks passed | LLM_MODE={mode}")
    print("Phase 3 journey verified end to end.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
