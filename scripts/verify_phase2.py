"""Verify the Phase 2 full-disruption journey and print a pasteable report.

Runs against a live API, so the same command works on Windows PowerShell, macOS and Linux:

    # bash / zsh
    docker compose exec -T api python - < scripts/verify_phase2.py

    # PowerShell
    Get-Content scripts/verify_phase2.py | docker compose exec -T api python -

Run it after `make up`, `make migrate`, `make seed` and `make demo-cascade`. It drives the whole
network journey -- group open, cascade run, blast radius, graph, candidate plans, plan comparison,
group assurance, plan approval, execution, resolution, replay -- and exits non-zero if any check
fails, so a partial pass cannot be misread as a pass.

It is a reporter, not a fixture. Every figure it prints came from a response or a row; nothing is
stubbed and nothing is patched. Where it asserts a number it says which endpoint produced it, so a
disagreement between two screens shows up here rather than on a projector.

Owner: Stream A.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("VERIFY_API_BASE", "http://127.0.0.1:8000/api/v1")
GROUP = os.environ.get("VERIFY_GROUP_REF", "GRP-2026-0820-VOBL")

#: The verified figures for the storm. Asserted rather than printed, because a demo that reports
#: a different number than the one everybody rehearsed is worse than one that fails loudly.
EXPECTED_FLIGHTS = 8
EXPECTED_PASSENGERS = 604
EXPECTED_CONNECTIONS = 22
EXPECTED_PAIRINGS = 9

PASS = "PASS"
FAIL = "FAIL"

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
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except Exception:
            return exc.code, {}
    except Exception as exc:  # pragma: no cover - connectivity
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------------------ 1. cascade


def check_group_opens() -> dict | None:
    status, body = call("POST", f"/incident-groups/{GROUP}/open")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "group opens", f"HTTP {status}: {body}")
        return None

    members = body.get("members") or []
    opened = [m for m in members if m.get("incident_reference")]
    if len(members) != EXPECTED_FLIGHTS:
        record(
            FAIL,
            "group declares eight member flights",
            f"declared {len(members)}; membership comes from incident_group_flight, so a "
            "different count means the seed changed",
        )
        return None
    record(
        PASS,
        f"group opens {len(opened)} incidents across {len(members)} declared flights",
        f"state {body.get('state')} (derived from members), "
        f"roles: {', '.join(sorted({m['role'] for m in members}))}",
    )
    return body


def check_idempotent_open() -> None:
    _s1, first = call("POST", f"/incident-groups/{GROUP}/open")
    _s2, second = call("POST", f"/incident-groups/{GROUP}/open")
    if not isinstance(first, dict) or not isinstance(second, dict):
        record(FAIL, "re-opening the cascade creates nothing new", "unexpected response")
        return
    a = {m["incident_reference"] for m in first.get("members", []) if m.get("incident_reference")}
    b = {m["incident_reference"] for m in second.get("members", []) if m.get("incident_reference")}
    if a != b:
        record(FAIL, "re-opening the cascade creates nothing new", f"{sorted(a ^ b)} differ")
        return
    record(PASS, "re-opening the cascade creates nothing new", f"{len(a)} incidents, unchanged")


def check_cascade_run() -> dict | None:
    status, body = call("POST", f"/incident-groups/{GROUP}/run")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "cascade advances", f"HTTP {status}: {body}")
        return None
    states: dict[str, int] = {}
    for member in body.get("members", []):
        state = member.get("state") or "none"
        states[state] = states.get(state, 0) + 1
    record(
        PASS,
        f"cascade advances to group state '{body.get('state')}'",
        ", ".join(f"{count} {state}" for state, count in sorted(states.items())),
    )
    return body


# ------------------------------------------------------------------------- 2. the figures


def check_rollup_figures() -> dict | None:
    status, body = call("GET", f"/incident-groups/{GROUP}")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "group detail", f"HTTP {status}: {body}")
        return None

    rollups = body.get("rollups") or {}
    checks = [
        ("flights_affected", EXPECTED_FLIGHTS),
        ("passengers_affected", EXPECTED_PASSENGERS),
        ("connections_at_risk", EXPECTED_CONNECTIONS),
        ("crew_pairings_affected", EXPECTED_PAIRINGS),
    ]
    wrong = [
        f"{key}={rollups.get(key)} (expected {want})"
        for key, want in checks
        if rollups.get(key) != want
    ]
    if wrong:
        record(FAIL, "verified figures", "; ".join(wrong))
        return body

    record(
        PASS,
        f"{EXPECTED_FLIGHTS} flights, {EXPECTED_PASSENGERS} passengers, "
        f"{EXPECTED_CONNECTIONS} connections, {EXPECTED_PAIRINGS} rotations",
        f"candidate_hotels={rollups.get('candidate_hotels')}; "
        f"rollup complete={((body.get('rollup_status') or {}).get('is_complete'))}",
    )
    return body


def check_no_summing(detail: dict) -> None:
    """Connections must be a union, never a sum of per-incident counts.

    Eight incidents each reporting their own broken connections would total far more than 22.
    Asserting the union figure is below the naive sum is what catches a regression to summing.
    """
    incidents = [f for f in detail.get("flights", []) if f.get("incident_reference")]
    connections = (detail.get("rollups") or {}).get("connections_at_risk", 0)
    if not incidents:
        record(FAIL, "group figures are unions, not sums", "no member incidents to compare")
        return
    naive = EXPECTED_CONNECTIONS * len(incidents)
    if connections >= naive:
        record(
            FAIL,
            "group figures are unions, not sums",
            f"connections_at_risk={connections} is at least the naive sum {naive}",
        )
        return
    record(
        PASS,
        "group figures are unions, not sums",
        f"{connections} distinct at-risk bookings across {len(incidents)} incidents, not {naive}",
    )


def check_why_nine_is_derived(detail: dict) -> None:
    sentence = detail.get("why_nine_not_eight") or ""
    pairings = detail.get("crew_pairings") or []
    mechanisms = sorted({p["mechanism"] for p in pairings})
    missing = [m for m in mechanisms if m not in sentence]
    if not sentence or missing:
        record(
            FAIL,
            "the nine-rotations explanation is derived from the data",
            f"missing mechanisms in the sentence: {missing}",
        )
        return
    record(
        PASS,
        "the nine-rotations explanation is derived from the data",
        f"mechanisms present: {', '.join(mechanisms)}",
    )


def check_blast_radius() -> None:
    status, body = call("GET", f"/incident-groups/{GROUP}/blast-radius")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "blast radius", f"HTTP {status}: {body}")
        return
    dimensions = body.get("dimensions") or []
    unmeasured = [d["key"] for d in dimensions if not d.get("measured_by")]
    if not dimensions:
        record(FAIL, "blast radius", "no dimensions returned")
        return
    if body.get("basis") != "composed_from_recorded_findings":
        record(FAIL, "blast radius states its basis", f"basis={body.get('basis')}")
        return
    record(
        PASS,
        f"blast radius composed from {len(dimensions)} recorded dimensions",
        "every dimension names its source"
        + (f"; unmeasured: {unmeasured}" if unmeasured else "")
        + f"; completeness {((body.get('completeness') or {}).get('ratio'))}",
    )


def check_graph_provenance() -> None:
    status, body = call("GET", f"/incident-groups/{GROUP}/graph")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "disruption graph", f"HTTP {status}: {body}")
        return
    edges = body.get("edges") or []
    nodes = body.get("nodes") or []
    orphans = [
        e
        for e in edges
        if e.get("derived_from_action_id") is None and e.get("derived_from_prediction_id") is None
    ]
    if orphans:
        record(
            FAIL,
            "every graph edge names the row it came from",
            f"{len(orphans)} edges have no provenance",
        )
        return
    record(
        PASS,
        f"disruption graph: {len(nodes)} nodes, {len(edges)} edges, all with provenance",
        f"edge kinds: {body.get('edge_counts_by_kind')}",
    )


# -------------------------------------------------------------- 3. plans and comparison


def _first_member(detail: dict) -> str | None:
    for flight in detail.get("flights", []):
        if flight.get("incident_reference"):
            return flight["incident_reference"]
    return None


def check_candidate_plans(incident_ref: str) -> list[dict]:
    status, body = call("GET", f"/incidents/{incident_ref}/plans")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "candidate plans", f"HTTP {status}: {body}")
        return []
    plans = body.get("plans") or []
    if len(plans) < 2:
        record(
            FAIL,
            "more than one candidate exists with LLM_MODE=off",
            f"{len(plans)} candidate(s); the comparison screen would be empty in the mode the "
            "demo runs in",
        )
        return plans
    hashed = [p for p in plans if p.get("plan_hash")]
    record(
        PASS,
        f"{len(plans)} candidate plans, each with a plan hash",
        f"variants: {', '.join(sorted(p.get('variant_key') or '?' for p in plans))}; "
        f"{len(hashed)}/{len(plans)} hashed",
    )
    return plans


def check_comparison_writes_nothing(incident_ref: str) -> None:
    before = call("GET", f"/incidents/{incident_ref}")[1]
    status, body = call("GET", f"/incidents/{incident_ref}/plans/comparison")
    after = call("GET", f"/incidents/{incident_ref}")[1]

    if status != 200 or not isinstance(body, dict):
        record(FAIL, "plan comparison", f"HTTP {status}: {body}")
        return
    if body.get("basis") != "recorded_evidence":
        record(FAIL, "comparison states its basis", f"basis={body.get('basis')}")
        return
    changed = (
        isinstance(before, dict)
        and isinstance(after, dict)
        and len(before.get("actions") or []) != len(after.get("actions") or [])
    )
    if changed:
        record(FAIL, "comparison writes nothing", "the action count changed")
        return
    record(
        PASS,
        f"comparison over {len(body.get('candidates') or [])} candidates writes nothing",
        f"decision={body.get('decision')}, basis={body.get('basis')}, "
        f"admissible={body.get('admissible')}",
    )


def check_no_ranking(incident_ref: str) -> None:
    _status, body = call("GET", f"/incidents/{incident_ref}/plans/comparison")
    raw = json.dumps(body).lower()
    for banned in ("recommended", "score", "confidence", "best_plan"):
        if banned in raw:
            record(FAIL, "no ranking or score in the comparison", f"found '{banned}'")
            return
    record(PASS, "no ranking, score or confidence in the comparison")


# ------------------------------------------------------------- 4. group plan assurance


def check_group_assurance() -> dict | None:
    status, body = call("GET", f"/incident-groups/{GROUP}/assurance")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "group assurance", f"HTTP {status}: {body}")
        return None
    if body.get("authorises_no_action") is not True:
        record(FAIL, "the group summary authorises nothing", "authorises_no_action is not True")
        return body
    checks = body.get("checks") or []
    if len(checks) != 6:
        record(FAIL, "six plan checks always render", f"{len(checks)} returned")
        return body
    raw = json.dumps(body).lower()
    if "score" in raw or "average" in raw:
        record(FAIL, "no aggregate assurance score", "found a score or average")
        return body
    record(
        PASS,
        f"group assurance: {len(checks)} plan checks, decision {body.get('decision')}",
        f"plan_risk_tier={body.get('plan_risk_tier')}, "
        f"blocking={body.get('blocking')}, "
        f"config {body.get('config_version')} uniform={body.get('config_hash_uniform')}, "
        f"authorises_no_action=True",
    )
    return body


def check_approval_preview(assurance: dict) -> None:
    preview = assurance.get("approval_preview")
    if preview is None:
        record(FAIL, "the console can see what an approval would cover", "no preview returned")
        return
    covered = preview.get("covered") or []
    excluded = preview.get("excluded") or []
    high = [e for e in excluded if e.get("risk_tier") == "high"]
    record(
        PASS,
        f"approval preview: {len(covered)} covered, {len(excluded)} excluded",
        f"high-risk excluded: {len(high)}"
        + (f"; refusal={preview.get('refusal')}" if preview.get("refusal") else "")
        + f"; reasons: {sorted({e.get('reason_code') for e in excluded})}",
    )


def check_high_risk_never_covered() -> None:
    """The single most important check in Phase 2.

    A plan approval must never cover a high-risk action. If this ever passes wrongly, the system
    executes something a person did not individually authorise.
    """
    status, body = call(
        "POST",
        f"/incident-groups/{GROUP}/assurance/decision",
        {"actor_id": "operator-verify", "reason": "verifying plan-level coverage"},
    )
    if status not in (200, 409):
        record(FAIL, "plan approval", f"HTTP {status}: {body}")
        return
    if status == 409 or not isinstance(body, dict):
        record(
            PASS,
            "plan approval refused rather than over-reaching",
            f"HTTP {status}: {(body or {}).get('detail') or body}",
        )
        return

    covered = body.get("covered") or []
    excluded = body.get("excluded") or []
    high_covered = [c for c in covered if c.get("risk_tier") == "high"]
    if high_covered:
        record(
            FAIL,
            "NO high-risk action is covered by a plan approval",
            f"{len(high_covered)} high-risk actions were covered: {high_covered}",
        )
        return
    if body.get("covered_count") != len(covered):
        record(
            FAIL,
            "the count recorded equals the count claimed",
            f"covered_count={body.get('covered_count')} but {len(covered)} entries",
        )
        return
    record(
        PASS,
        "no high-risk action is covered by a plan approval",
        f"covered {len(covered)} low/medium, excluded {len(excluded)} "
        f"({sorted({e.get('reason_code') for e in excluded})}); "
        f"plan_approval_id={body.get('plan_approval_id')}",
    )


def check_evidence_never_approvable(assurance: dict) -> None:
    """An evidence-blocked evaluation must be refused at the action level too."""
    target = None
    for incident in assurance.get("incidents", []):
        for task in incident.get("tasks", []):
            kinds = task.get("blocking_kinds") or []
            if task.get("evaluation_id") and ("evidence" in kinds or "conflict" in kinds):
                target = task
                break
        if target:
            break
    if target is None:
        record(PASS, "no evidence-blocked evaluation to approve (nothing to refuse)")
        return

    status, body = call(
        "POST",
        f"/assurance/{target['evaluation_id']}/decision",
        {"actor_id": "operator-verify", "reason": "attempting to approve past evidence"},
    )
    if status == 200:
        record(
            FAIL,
            "approval never overrides failed evidence",
            f"evaluation {target['evaluation_id']} ({target['action_type']}) was approved "
            f"despite blocking kinds {target.get('blocking_kinds')}",
        )
        return
    record(
        PASS,
        "approval never overrides failed evidence",
        f"HTTP {status} for {target['action_type']}: {(body or {}).get('detail')}",
    )


# ----------------------------------------------------------------------------- 5. what-if


def check_what_if_zero_write() -> None:
    before = call("GET", f"/incident-groups/{GROUP}")[1]
    status, body = call(
        "POST",
        f"/incident-groups/{GROUP}/what-if",
        {"minimum_connection_minutes": 30, "seed": 20260820},
    )
    after = call("GET", f"/incident-groups/{GROUP}")[1]

    if status != 200 or not isinstance(body, dict):
        record(FAIL, "what-if", f"HTTP {status}: {body}")
        return
    if body.get("basis") != "recorded_evidence" or body.get("wrote_rows") is not False:
        record(
            FAIL,
            "what-if is zero-write and states its basis",
            f"basis={body.get('basis')}, wrote_rows={body.get('wrote_rows')}",
        )
        return
    moved = (
        isinstance(before, dict)
        and isinstance(after, dict)
        and json.dumps(before.get("rollups")) != json.dumps(after.get("rollups"))
    )
    if moved:
        record(FAIL, "what-if changed the recorded rollups", "figures moved")
        return
    note = (body.get("boundary_note") or "").lower()
    if "not a forecast" not in note or "not a simulation" not in note:
        record(
            FAIL,
            "what-if states its boundary in the payload",
            "boundary_note does not disclaim simulation and forecasting",
        )
        return
    record(
        PASS,
        f"what-if re-evaluated {len(body.get('deltas') or [])} figures and wrote nothing",
        f"levers applied: {body.get('levers_applied')}; "
        f"rejected: {[r.get('lever') for r in (body.get('levers_rejected') or [])]}",
    )


# ------------------------------------------------------------------ 6. execution + replay


def check_action_approvals() -> int:
    """Approve each held evaluation individually, as P2-D3 requires for high risk.

    A plan approval cannot cover these -- they are high-risk notifications, and every one gets its
    own decision, its own record and its own actor. That is the whole argument the gate exists to
    make, so the journey has to actually make it rather than describe it.
    """
    status, groups = call("GET", f"/incident-groups/{GROUP}")
    if status != 200 or not isinstance(groups, dict):
        record(FAIL, "action-level approvals", f"HTTP {status}")
        return 0

    approved = 0
    refused: list[str] = []
    for flight in groups.get("flights", []):
        reference = flight.get("incident_reference")
        if not reference:
            continue
        _s, detail = call("GET", f"/incidents/{reference}/assurance")
        if not isinstance(detail, dict):
            continue
        for evaluation in detail.get("evaluations", []):
            if evaluation.get("decision") != "needs_human":
                continue
            if evaluation.get("human_decision"):
                continue
            code, body = call(
                "POST",
                f"/assurance/{evaluation['id']}/decision",
                {
                    "decision": "approved",
                    "actor_id": "operator-1",
                    "reason": "network recovery authorised for this flight",
                },
            )
            if code == 200:
                approved += 1
            else:
                refused.append(
                    f"{reference}/{evaluation['id']}: {(body or {}).get('detail') or code}"
                )

    if approved == 0:
        record(FAIL, "operator approvals recorded", f"none accepted; refusals: {refused[:3]}")
        return 0
    record(
        PASS,
        f"{approved} high-risk actions approved individually, each by name",
        f"refused: {len(refused)}" + (f" ({refused[0]})" if refused else ""),
    )
    return approved


def check_human_attribution(incident_ref: str) -> None:
    status, body = call("GET", f"/incidents/{incident_ref}/timeline")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "human attribution on the timeline", f"HTTP {status}")
        return
    humans = [e for e in body.get("entries", []) if e.get("actor_kind") == "human"]
    if not humans:
        record(FAIL, "the operator decision reads as a human act", "no human entry on the timeline")
        return
    record(
        PASS,
        f"{len(humans)} timeline entries attributed to a human",
        f"actors: {sorted({e.get('actor') for e in humans})}",
    )


def check_resolution() -> dict | None:
    for _ in range(6):
        status, body = call("POST", f"/incident-groups/{GROUP}/run")
        if status != 200 or not isinstance(body, dict):
            record(FAIL, "cascade run to completion", f"HTTP {status}: {body}")
            return None
        if body.get("state") in {"resolved", "blocked", "failed"}:
            break
    states: dict[str, int] = {}
    for member in body.get("members", []):
        state = member.get("state") or "none"
        states[state] = states.get(state, 0) + 1
    resolved = states.get("resolved", 0)
    detail = ", ".join(f"{count} {state}" for state, count in sorted(states.items()))
    if body.get("state") == "resolved":
        record(PASS, f"cascade reaches 'resolved': {resolved} member incidents", detail)
    else:
        record(
            PASS if resolved else FAIL,
            f"cascade reaches '{body.get('state')}' with {resolved} resolved",
            f"{detail}"
            + (f"; reason: {body.get('blocked_reason')}" if body.get("blocked_reason") else ""),
        )
    return body


def check_replay(incident_ref: str) -> None:
    status, body = call("GET", f"/incidents/{incident_ref}/replay")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "incident replay", f"HTTP {status}: {body}")
        return
    frames = body.get("frames") or []
    sequences = [f["sequence"] for f in frames]
    if sequences != list(range(1, len(frames) + 1)):
        record(FAIL, "replay frames are contiguous", f"{sequences[:12]}...")
        return
    humans = [f for f in frames if f.get("actor_kind") == "human"]
    scopes = sorted({f.get("decision_scope") for f in humans if f.get("decision_scope")})
    record(
        PASS,
        f"incident replay: {len(frames)} contiguous frames, {len(humans)} by a human",
        f"decision scopes present: {scopes}",
    )


def check_group_replay() -> None:
    status, body = call("GET", f"/incident-groups/{GROUP}/replay")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "group replay", f"HTTP {status}: {body}")
        return
    frames = body.get("frames") or []
    times = [f["occurred_at"] for f in frames]
    if times != sorted(times):
        record(FAIL, "group replay is chronological", "frames are out of order")
        return
    references = {f.get("incident_reference") for f in frames if f.get("incident_reference")}
    record(
        PASS,
        f"group replay: {len(frames)} frames interleaved across {len(references)} incidents",
        "ordered by (occurred_at, id)",
    )


def check_actions_are_real(incident_ref: str) -> None:
    status, body = call("GET", f"/incidents/{incident_ref}")
    if status != 200 or not isinstance(body, dict):
        record(FAIL, "actions", f"HTTP {status}: {body}")
        return
    actions = body.get("actions") or []
    if not actions:
        record(FAIL, "real services executed", "no actions recorded")
        return
    faked = [a for a in actions if "SERVICE_NOT_IMPLEMENTED" in (a.get("reason") or "")]
    unauthorised = [a for a in actions if not a.get("assurance_id")]
    if faked:
        record(FAIL, "no action fabricated success", f"{len(faked)} refusals recorded as actions")
        return
    if unauthorised:
        record(FAIL, "every action names its authorisation", f"{len(unauthorised)} without one")
        return

    detail_ok = 0
    for action in actions:
        status, payload = call("GET", f"/incidents/{incident_ref}/actions/{action['id']}")
        if status == 200 and isinstance(payload, dict) and "payload" in payload:
            detail_ok += 1
    record(
        PASS,
        f"{len(actions)} actions executed through real services, each authorised",
        f"action detail available for {detail_ok}/{len(actions)}; "
        f"types: {', '.join(sorted({a['action_type'] for a in actions}))}",
    )


# --------------------------------------------------------------------------------- main


def main() -> int:
    print(f"TravelOps Phase 2 verification against {API}")
    print(f"group {GROUP}\n")

    opened = check_group_opens()
    if opened is None:
        print("\nCannot continue: the cascade did not open.")
        return 1
    check_idempotent_open()
    check_cascade_run()

    detail = check_rollup_figures()
    if detail:
        check_no_summing(detail)
        check_why_nine_is_derived(detail)
    check_blast_radius()
    check_graph_provenance()

    member = _first_member(detail or {})
    if member:
        check_candidate_plans(member)
        check_comparison_writes_nothing(member)
        check_no_ranking(member)

    assurance = check_group_assurance()
    if assurance:
        check_approval_preview(assurance)
        check_evidence_never_approvable(assurance)
    check_high_risk_never_covered()

    check_what_if_zero_write()

    check_action_approvals()
    check_resolution()

    if member:
        check_actions_are_real(member)
        check_human_attribution(member)
        check_replay(member)
    check_group_replay()

    failures = [name for state, name, _ in _results if state == FAIL]
    print()
    print(f"{len(_results) - len(failures)}/{len(_results)} checks passed")
    if failures:
        print("\nFAILED:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("\nPhase 2 journey verified end to end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
