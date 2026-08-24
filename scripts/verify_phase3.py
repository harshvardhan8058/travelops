"""Phase 3 end-to-end verification -- reasoning agents in all three LLM modes.

Run against a live stack (after seed + inject --cascade):

    LLM_MODE=off     python scripts/verify_phase3.py
    LLM_MODE=fixture python scripts/verify_phase3.py
    LLM_MODE=live    python scripts/verify_phase3.py   # requires GROQ_API_KEY

Checks:
  off     -- every plan's generator is the playbook; no agent detail in the journal
  fixture -- the plan's generator is the agent, reflection findings recorded, artefacts available
  live    -- same as fixture but with a real Groq call

All modes: the full journey still completes (detected -> resolved).

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
        # The playbook is the whole planner. No agent ran, so no plan may claim one.
        check(
            len(plans) >= 1,
            f"off mode produced a plan (got {len(plans)})",
            f"generators={generators}",
        )
        check(
            "planner-agent" not in generators,
            "off mode: no plan claims an agent generator",
            f"generators={generators}",
        )
    else:
        # Stream A's model: the agent REPLACES the task list inside the plan row rather than
        # adding a second one, so the assertion is on the generator, not on a plan count.
        check(
            len(plans) >= 1,
            f"fixture/live mode produced a plan (got {len(plans)})",
            f"generators={generators}",
        )
        check(
            "planner-agent" in generators,
            "the agent produced a plan (generator=planner-agent)",
            f"generators={generators} variants={variants}",
        )
        # Every task must still be an executable action: reflection drops anything unregistered.
        planner_plans = [p for p in plans if p.get("generator") == "planner-agent"]
        for plan in planner_plans:
            actions = [t.get("action_type") for t in plan.get("tasks", [])]
            check(
                "reassign_gate" not in actions,
                "reflection dropped the unregistered action the fixture proposes",
                f"actions={actions}",
            )
            check(
                len(actions) == len(set(actions)),
                "no duplicate action survived reflection",
                f"actions={actions}",
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
                f
                for f in frames
                if f.get("event_type") == "PLAN_PROPOSED"
                and f.get("detail", {}).get("generator") == "planner-agent"
            ]
            check(
                len(planner_frames) > 0,
                "the agent's PLAN_PROPOSED is in the replay",
            )
            # Reflection must be auditable: what the model proposed and what was dropped.
            with_agent = [f for f in planner_frames if f.get("detail", {}).get("agent")]
            check(
                len(with_agent) > 0,
                "the journal records the agent's proposal and reflection findings",
                f"keys={sorted((with_agent[0]['detail']['agent'] or {}).keys()) if with_agent else []}",
            )
    else:
        check(False, f"replay returned {status}")

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
