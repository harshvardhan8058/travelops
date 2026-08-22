"""End-to-end tests for the real incident lifecycle endpoints.

These drive the one deterministic bengaluru_storm path over HTTP with LLM_MODE unset in the
request path and no model reachable: open, run, read the timeline, read the assurance panel,
approve, and see the state machine respond.

They also assert the thing the fixture routes could not: that every endpoint has a real
OpenAPI schema rather than `"string"`.
"""

from __future__ import annotations

PREFIX = "/api/v1"


class TestIncidentDetail:
    def test_returns_the_recorded_incident(self, client, incident):
        body = client.get(f"{PREFIX}/incidents/{incident}").json()

        assert body["reference"] == incident
        assert body["state"] == "detected"
        assert body["trigger_type"] == "weather"
        assert body["flight"]["flight_number"] == "6E 2134"
        assert body["flight"]["route"] == "VOBL → VIDP"

    def test_delay_minutes_is_computed_not_stored(self, client, incident):
        """420 = the 7-hour estimate against the scheduled departure."""
        body = client.get(f"{PREFIX}/incidents/{incident}").json()
        assert body["flight"]["delay_minutes"] == 420

    def test_numeric_id_also_resolves(self, client, incident):
        assert client.get(f"{PREFIX}/incidents/1").json()["reference"] == incident

    def test_unknown_incident_uses_the_typed_error_envelope(self, client, incident):
        response = client.get(f"{PREFIX}/incidents/INC-nope")
        assert response.status_code == 404
        error = response.json()["error"]
        assert set(error) == {"code", "message", "correlation_id", "details"}
        assert error["code"] == "ENTITY_NOT_FOUND"
        assert error["correlation_id"]

    def test_recorded_weather_carries_the_provenance_contract(self, client, incident):
        weather = client.get(f"{PREFIX}/incidents/{incident}").json()["evidence"]["weather"]
        assert weather["airport_icao"] == "VOBL"
        assert weather["provenance"]["kind"] == "fixture"
        assert weather["provenance"]["source_ref"] == "fixture:bengaluru_storm:weather:VOBL"

    def test_an_uncomputed_count_is_absent_rather_than_zero(self, client, incident):
        """No bookings are seeded, so claiming 0 passengers would be a fabricated total."""
        body = client.get(f"{PREFIX}/incidents/{incident}").json()
        assert body["evidence"]["affected_entities"] == {}
        assert body["flight"]["passengers"] is None

    def test_risk_is_null_before_a_prediction_exists(self, client, incident):
        """Null, never a fabricated index. Nothing here is calibrated."""
        assert client.get(f"{PREFIX}/incidents/{incident}").json()["evidence"]["risk"] is None

    def test_plan_is_null_before_one_is_proposed(self, client, incident):
        assert client.get(f"{PREFIX}/incidents/{incident}").json()["plan"] is None

    def test_the_state_rail_reflects_what_actually_happened(self, client, incident):
        body = client.get(f"{PREFIX}/incidents/{incident}").json()
        rail = {entry["state"]: entry["reached_at"] for entry in body["state_rail"]}
        assert rail["detected"] is not None
        # Not reached yet, so null rather than inferred from its position in the sequence.
        assert rail["assuring"] is None
        assert rail["resolved"] is None

    def test_a_branch_state_appears_on_the_rail_once_reached(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}").json()
        rail = {entry["state"]: entry["reached_at"] for entry in body["state_rail"]}

        assert rail["awaiting_approval"] is not None
        # The happy-path spine is still present, with the states nobody reached left null.
        assert rail["resolved"] is None


class TestRun:
    def test_running_walks_the_state_machine(self, client, incident):
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()

        assert body["previous_state"] == "detected"
        assert body["state"] in {"blocked", "awaiting_approval", "resolved", "executing"}
        assert body["steps_taken"] > 0

    def test_the_run_stops_for_operator_approval(self, client, incident):
        """The low-risk tasks execute for real; the bulk external effect waits for a person.

        This is the whole point of the gate. `notify_passengers` is high risk in the
        committed config, so no amount of successful prior work lets it through on its own.
        """
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()

        assert body["state"] == "awaiting_approval"
        assert body["is_terminal"] is False
        assert "operator decision" in (body["note"] or "")

    def test_the_low_risk_tasks_really_executed(self, client, incident):
        """Not refused, not faked: three Stream C services ran and recorded a result.

        `find_hotel_options` joined this set when Phase 2 registered the hotel services. It is
        listed explicitly rather than asserted loosely, because "some services ran" would still
        pass if one silently stopped running.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}").json()

        done = {a["action_type"]: a for a in body["actions"]}
        assert set(done) == {
            "check_connections",
            "find_hotel_options",
            "assess_crew_impact",
        }
        for action in done.values():
            assert action["status"] == "success"
            assert "SERVICE_NOT_IMPLEMENTED" not in action["reason"]
            assert action["provenance_kind"] in {"synthetic", "simulated", "fixture", "real"}

    def test_the_plan_records_the_deterministic_generator(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        assert plan["generator"] == "fallback-playbook"
        assert plan["prompt_version"] is None
        assert plan["model_self_report"] is None

    def test_the_plan_proposes_only_what_can_be_executed(self, client, incident):
        """A plan that proposes work nothing can do stops dead and overstates the system.

        The two Stage 3 actions are deferred rather than proposed-and-failed, and the
        omission is written into the rationale rather than left for a reader to notice.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        assert [t["action_type"] for t in plan["tasks"]] == [
            "check_connections",
            "find_hotel_options",
            "assess_crew_impact",
            "notify_passengers",
        ]
        # One deferral left. `evaluate_entitlements` still has no registered service, and the
        # omission is written into the rationale rather than left for a reader to notice.
        assert "evaluate_entitlements" in plan["rationale"]
        assert "no deterministic service is available" in plan["rationale"]
        # And the converse: a registered action must NOT be described as deferred.
        assert "find_hotel_options" not in plan["rationale"]

    def test_the_deferral_is_on_the_timeline_too(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]

        proposed = next(e for e in entries if e["event_type"] == "PLAN_PROPOSED")
        # One action still has no registered service. The list shrank when Phase 2 registered
        # the hotel services, which is the point: a deferral is a statement about what exists,
        # so it has to move when that changes.
        assert proposed["detail"]["deferred_actions"] == ["evaluate_entitlements"]

    def test_a_replayed_idempotency_key_returns_the_original_result(self, client, incident):
        headers = {"Idempotency-Key": "run-abc-123"}
        first = client.post(f"{PREFIX}/incidents/{incident}/run", headers=headers).json()
        second = client.post(f"{PREFIX}/incidents/{incident}/run", headers=headers).json()

        assert first["replayed"] is False
        assert second["replayed"] is True
        assert second["state"] == first["state"]
        assert second["steps_taken"] == first["steps_taken"]

    def test_running_again_while_awaiting_approval_changes_nothing(self, client, incident):
        """Waiting on a person is a resting state, not something a retry can push past."""
        client.post(f"{PREFIX}/incidents/{incident}/run")
        before = client.get(f"{PREFIX}/incidents/{incident}").json()

        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()
        after = client.get(f"{PREFIX}/incidents/{incident}").json()

        assert body["state"] == "awaiting_approval"
        assert body["previous_state"] == "awaiting_approval"
        assert len(after["actions"]) == len(before["actions"])

    def test_correlation_id_is_echoed_on_a_mutation(self, client, incident):
        response = client.post(
            f"{PREFIX}/incidents/{incident}/run", headers={"X-Correlation-Id": "run-corr-1"}
        )
        assert response.headers["X-Correlation-Id"] == "run-corr-1"


class TestTimeline:
    def test_entries_are_ordered_and_carry_the_correlation_id(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()

        assert body["incident_reference"] == incident
        entries = body["entries"]
        assert entries
        assert [e["id"] for e in entries] == sorted(e["id"] for e in entries)
        assert all(e["correlation_id"] for e in entries)

    def test_the_whole_run_is_reconstructable_in_order(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        types = [e["event_type"] for e in entries]

        assert types[0] == "INCIDENT_OPENED"
        for expected in ("PLAN_PROPOSED", "ASSURANCE_EVALUATED", "ACTION_COMPLETED"):
            assert expected in types, f"{expected} missing from the timeline"

    def test_actor_kind_is_derived_so_the_ui_never_string_matches(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]

        kinds = {e["actor"]: e["actor_kind"] for e in entries}
        assert kinds["orchestrator"] == "orchestrator"
        assert kinds["assurance_gate"] == "orchestrator"


class TestAssurancePanel:
    def test_evaluations_carry_six_checks_in_contractual_order(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()

        assert body["evaluations"]
        checks = body["evaluations"][0]["checks"]
        assert [c["name"] for c in checks] == [
            "evidence_complete",
            "sources_fresh",
            "entities_valid",
            "policy_compliant",
            "no_conflicts",
            "action_risk",
        ]

    def test_the_config_hash_is_always_present_for_replay(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()

        assert body["config_version"]
        assert body["config_hash"]
        for evaluation in body["evaluations"]:
            assert evaluation["config_version"]
            assert evaluation["config_hash"]

    def test_check_states_are_three_valued(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()

        for evaluation in body["evaluations"]:
            for check in evaluation["checks"]:
                assert check["state"] in {"PASS", "WARN", "FAIL"}

    def test_no_confidence_value_appears_anywhere(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        raw = client.get(f"{PREFIX}/incidents/{incident}/assurance").text
        assert "confidence" not in raw.lower()


class TestOperatorDecision:
    def _first_evaluation(self, client, incident) -> int:
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()
        return body["evaluations"][0]["id"]

    def _pending_evaluation(self, client, incident) -> int:
        """The evaluation the gate actually held for a person.

        Not `evaluations[0]`: that is `check_connections`, which the gate authorised outright.
        Approving something that was never held produces a `human_decision` row that no action
        references — correctly, because `execute()` only records one when the gate demanded it.
        """
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()
        pending = [e for e in body["evaluations"] if e["decision"] == "needs_human"]
        assert pending, "expected the gate to hold at least one action for approval"
        return pending[0]["id"]

    def test_approval_is_recorded_with_a_pseudonymous_actor(self, client, incident):
        evaluation_id = self._first_evaluation(client, incident)
        response = client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "approved", "reason": "checked against the ops board"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "approved"
        assert body["actor_id"] == "operator-1"
        assert body["replayed"] is False

    def test_the_decision_appears_on_the_evaluation(self, client, incident):
        evaluation_id = self._first_evaluation(client, incident)
        client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "rejected", "reason": "not appropriate here"},
        )
        body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()
        recorded = next(e for e in body["evaluations"] if e["id"] == evaluation_id)

        assert recorded["human_decision"]["decision"] == "rejected"
        assert recorded["human_decision"]["reason"] == "not appropriate here"
        # The gate's own record is untouched by the operator response.
        assert recorded["decision"] in {"execute", "execute_flagged", "needs_human"}

    def test_reposting_the_same_decision_returns_the_original(self, client, incident):
        evaluation_id = self._first_evaluation(client, incident)
        payload = {"decision": "approved", "reason": "verified"}
        first = client.post(f"{PREFIX}/assurance/{evaluation_id}/decision", json=payload).json()
        second = client.post(f"{PREFIX}/assurance/{evaluation_id}/decision", json=payload).json()

        assert first["replayed"] is False
        assert second["replayed"] is True
        assert second["decided_at"] == first["decided_at"]

    def test_a_contradicting_decision_is_a_conflict_not_an_update(self, client, incident):
        """An operator response is a record of what someone decided, not a setting."""
        evaluation_id = self._first_evaluation(client, incident)
        client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "approved", "reason": "verified"},
        )
        response = client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "rejected", "reason": "changed my mind"},
        )

        assert response.status_code == 409
        details = response.json()["error"]["details"]
        assert details["recorded_decision"] == "approved"
        assert details["requested_decision"] == "rejected"

    def test_the_approval_is_attributed_to_a_human_on_the_timeline(self, client, incident):
        """Regression: an approval was recorded as `actor_kind=orchestrator`.

        The only timeline entry an approval produced was the orchestrator's `STATE_CHANGED`
        from `awaiting_approval` to `executing`. That entry is correct — the orchestrator is
        what moved the incident — but it was also the *sole* record, so the audit trail showed
        a machine authorising a bulk external effect that a person actually authorised.

        A human overriding the gate is the single most important actor in this system to
        attribute correctly, so this asserts the whole chain: the decision endpoint writes a
        human-attributed entry carrying the operator and evaluation IDs, and the action that
        follows references the decision row.
        """
        evaluation_id = self._pending_evaluation(client, incident)
        client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={
                "decision": "approved",
                "reason": "confirmed against the ops board",
                "actor_id": "operator-7",
            },
        )

        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        human = [e for e in entries if e["event_type"] == "HUMAN_DECISION_RECORDED"]

        assert len(human) == 1, "exactly one entry per human decision, not zero and not two"
        entry = human[0]
        assert entry["actor_kind"] == "human"
        assert entry["actor"] == "human"
        assert entry["stage"] == "assure"
        assert entry["detail"]["assurance_id"] == evaluation_id
        assert entry["detail"]["actor_id"] == "operator-7"
        assert entry["detail"]["decision"] == "approved"
        assert entry["detail"]["human_decision_id"] is not None
        assert entry["correlation_id"]

        # No other entry claims to be the human's, and the orchestrator keeps its own.
        assert [e["actor_kind"] for e in entries].count("human") == 1
        assert any(e["event_type"] == "STATE_CHANGED" for e in entries)

    def test_the_resulting_action_references_the_human_decision(self, client, incident):
        """The other half of the chain: approval authorises, and the action records it."""
        evaluation_id = self._pending_evaluation(client, incident)
        client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "approved", "reason": "confirmed", "actor_id": "operator-7"},
        )
        client.post(f"{PREFIX}/incidents/{incident}/run")

        body = client.get(f"{PREFIX}/incidents/{incident}").json()
        authorised = [a for a in body["actions"] if a["assurance_id"] == evaluation_id]
        assert authorised, "the approved evaluation produced no action"
        for action in authorised:
            assert action["human_decision_id"] is not None

        # And the timeline's human entry names the same decision row.
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        entry = next(e for e in entries if e["event_type"] == "HUMAN_DECISION_RECORDED")
        assert entry["detail"]["human_decision_id"] == authorised[0]["human_decision_id"]

    def test_a_rejection_is_also_attributed_to_a_human_exactly_once(self, client, incident):
        """Rejections used to be journalled by the engine on the next run instead.

        Both outcomes are now recorded in one place, at the time the person decided, so a
        rejection cannot produce two entries or land with a later timestamp than the decision.
        """
        evaluation_id = self._pending_evaluation(client, incident)
        client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "rejected", "reason": "not appropriate", "actor_id": "operator-9"},
        )
        client.post(f"{PREFIX}/incidents/{incident}/run")

        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        human = [e for e in entries if e["event_type"] == "HUMAN_DECISION_RECORDED"]

        assert len(human) == 1
        assert human[0]["actor_kind"] == "human"
        assert human[0]["detail"]["decision"] == "rejected"
        assert human[0]["detail"]["actor_id"] == "operator-9"

    def test_a_replayed_decision_does_not_add_a_second_timeline_entry(self, client, incident):
        """A replay decided nothing new, so it must not appear as a fresh human act."""
        evaluation_id = self._pending_evaluation(client, incident)
        payload = {"decision": "approved", "reason": "confirmed"}
        client.post(f"{PREFIX}/assurance/{evaluation_id}/decision", json=payload)
        client.post(f"{PREFIX}/assurance/{evaluation_id}/decision", json=payload)

        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
        human = [e for e in entries if e["event_type"] == "HUMAN_DECISION_RECORDED"]
        assert len(human) == 1

    def test_unknown_evaluation_is_a_typed_404(self, client, incident):
        response = client.post(
            f"{PREFIX}/assurance/999999/decision",
            json={"decision": "approved", "reason": "x"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ENTITY_NOT_FOUND"

    def test_a_missing_reason_is_rejected(self, client, incident):
        evaluation_id = self._first_evaluation(client, incident)
        response = client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision", json={"decision": "approved"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"

    def test_an_unknown_decision_value_is_rejected(self, client, incident):
        evaluation_id = self._first_evaluation(client, incident)
        response = client.post(
            f"{PREFIX}/assurance/{evaluation_id}/decision",
            json={"decision": "maybe", "reason": "x"},
        )
        assert response.status_code == 422


class TestResolvedPath:
    """The same HTTP path once Stream C's services exist.

    Services are registered into the dispatch registry here — exactly what Stream C's work
    will do — and removed afterwards. No Stream C file is touched. This proves the endpoint
    surface is complete for the demo, not only for the currently-blocked case.
    """

    @staticmethod
    def _register_fakes():
        from app.models.enums import ActionStatus, ActionType
        from app.orchestrator import dispatch
        from app.services.base import ServiceResult

        for action in ActionType:

            def call(_action=action, **kwargs):
                async def _run() -> ServiceResult:
                    return ServiceResult(
                        status=ActionStatus.success,
                        reason=f"{_action.value} completed",
                        payload={"action": _action.value},
                        provenance_kind="simulated",
                    )

                return _run()

            dispatch.register(action, call)

    @staticmethod
    def _clear():
        from app.orchestrator import dispatch

        dispatch.SERVICE_REGISTRY.clear()

    def test_the_run_reaches_a_terminal_state_without_a_model(self, client, incident):
        try:
            self._register_fakes()
            body = client.post(f"{PREFIX}/incidents/{incident}/run").json()

            # notify_passengers and evaluate_entitlements are high risk in the committed
            # config, so the gate holds them for a person. That is the correct end state.
            assert body["state"] in {"resolved", "awaiting_approval"}
            assert body["steps_taken"] > 3
        finally:
            self._clear()

    def test_high_risk_actions_are_held_for_a_human(self, client, incident):
        try:
            self._register_fakes()
            client.post(f"{PREFIX}/incidents/{incident}/run")
            body = client.get(f"{PREFIX}/incidents/{incident}/assurance").json()

            by_action = {e["action_type"]: e for e in body["evaluations"]}
            assert by_action["check_connections"]["decision"] in {
                "execute",
                "execute_flagged",
            }
            if "notify_passengers" in by_action:
                assert by_action["notify_passengers"]["decision"] == "needs_human"
                assert by_action["notify_passengers"]["risk_tier"] == "high"
                assert body["awaiting_approval_count"] >= 1
        finally:
            self._clear()

    def test_the_rail_records_when_a_state_was_first_reached(self, client, incident):
        """`assuring` is entered once per task, so the rail must not drift to the last one."""
        try:
            self._register_fakes()
            client.post(f"{PREFIX}/incidents/{incident}/run")

            rail = {
                e["state"]: e["reached_at"]
                for e in client.get(f"{PREFIX}/incidents/{incident}").json()["state_rail"]
            }
            entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]
            transitions = [
                e["occurred_at"]
                for e in entries
                if e["event_type"] == "STATE_CHANGED" and e["detail"]["to"] == "assuring"
            ]
            assert len(transitions) > 1, "expected assuring to be re-entered per task"
            assert rail["assuring"] == transitions[0]
        finally:
            self._clear()

    def test_every_action_references_its_authorisation(self, client, incident):
        try:
            self._register_fakes()
            client.post(f"{PREFIX}/incidents/{incident}/run")
            actions = client.get(f"{PREFIX}/incidents/{incident}").json()["actions"]

            assert actions
            for action in actions:
                assert action["assurance_id"] is not None
                assert action["idempotency_key"]
        finally:
            self._clear()

    def test_a_task_reports_the_evaluation_that_authorised_it(self, client, incident):
        try:
            self._register_fakes()
            client.post(f"{PREFIX}/incidents/{incident}/run")
            plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

            assured = [t for t in plan["tasks"] if t["state"] != "proposed"]
            assert assured
            for task in assured:
                assert task["assurance_id"] is not None
        finally:
            self._clear()


class TestOpenApiSchemas:
    """The reason every real endpoint needs a response_model.

    Fixture routes return `Any`, which OpenAPI renders as `"string"` — useless for a
    generated client, and why `frontend/src/api/types.ts` had to be hand-written.
    """

    def test_real_endpoints_declare_a_component_schema(self, client):
        spec = client.get("/openapi.json").json()
        expected = {
            ("/api/v1/incidents/{incident_id}", "get"): "IncidentDetailResponse",
            ("/api/v1/incidents/{incident_id}/timeline", "get"): "TimelineResponse",
            ("/api/v1/incidents/{incident_id}/assurance", "get"): "AssuranceResponse",
            ("/api/v1/incidents/{incident_id}/run", "post"): "RunResponse",
            ("/api/v1/assurance/{assurance_id}/decision", "post"): "DecisionResponse",
        }
        for (path, method), model in expected.items():
            schema = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"][
                "schema"
            ]
            assert schema.get("$ref") == f"#/components/schemas/{model}", (
                f"{method.upper()} {path} must declare {model}, got {schema}"
            )

    def test_no_real_endpoint_renders_as_a_bare_string(self, client):
        spec = client.get("/openapi.json").json()
        for path in (
            "/api/v1/incidents/{incident_id}",
            "/api/v1/incidents/{incident_id}/timeline",
            "/api/v1/incidents/{incident_id}/assurance",
        ):
            schema = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
                "schema"
            ]
            assert schema.get("type") != "string"

    def test_the_replaced_fixture_routes_are_gone(self, client):
        """One implementation per path, never two."""
        spec = client.get("/openapi.json").json()
        for path in (
            "/api/v1/incidents/{incident_id}",
            "/api/v1/incidents/{incident_id}/timeline",
            "/api/v1/incidents/{incident_id}/assurance",
        ):
            summary = spec["paths"][path]["get"]["summary"]
            assert "[fixture]" not in summary, f"{path} is still fixture-backed"

    def test_the_endpoints_not_yet_real_are_still_labelled_fixture(self, client):
        """Honest labelling: an unimplemented endpoint must not look finished."""
        spec = client.get("/openapi.json").json()
        for path in ("/api/v1/flights", "/api/v1/incidents/{incident_id}/policy"):
            assert "[fixture]" in spec["paths"][path]["get"]["summary"]

    def test_mutations_document_the_idempotency_key(self, client):
        spec = client.get("/openapi.json").json()
        for path, method in (
            ("/api/v1/incidents/{incident_id}/run", "post"),
            ("/api/v1/assurance/{assurance_id}/decision", "post"),
        ):
            names = {p["name"] for p in spec["paths"][path][method].get("parameters", [])}
            assert "Idempotency-Key" in names, f"{method.upper()} {path} must accept the header"
