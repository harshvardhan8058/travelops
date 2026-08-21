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

        assert rail["blocked"] is not None
        # The happy-path spine is still present, with the states nobody reached left null.
        assert rail["resolved"] is None


class TestRun:
    def test_running_walks_the_state_machine(self, client, incident):
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()

        assert body["previous_state"] == "detected"
        assert body["state"] in {"blocked", "awaiting_approval", "resolved", "executing"}
        assert body["steps_taken"] > 0

    def test_the_run_stops_at_the_honest_service_boundary(self, client, incident):
        """Stream C's services do not exist, so the run must say so and stop."""
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()

        assert body["state"] == "blocked"
        assert body["is_terminal"] is True
        assert "SERVICE_NOT_IMPLEMENTED" in body["note"]

    def test_the_plan_records_the_deterministic_generator(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        plan = client.get(f"{PREFIX}/incidents/{incident}").json()["plan"]

        assert plan["generator"] == "fallback-playbook"
        assert plan["prompt_version"] is None
        assert plan["model_self_report"] is None
        assert len(plan["tasks"]) == 5

    def test_a_replayed_idempotency_key_returns_the_original_result(self, client, incident):
        headers = {"Idempotency-Key": "run-abc-123"}
        first = client.post(f"{PREFIX}/incidents/{incident}/run", headers=headers).json()
        second = client.post(f"{PREFIX}/incidents/{incident}/run", headers=headers).json()

        assert first["replayed"] is False
        assert second["replayed"] is True
        assert second["state"] == first["state"]
        assert second["steps_taken"] == first["steps_taken"]

    def test_running_a_terminal_incident_does_not_advance_it(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()

        assert body["state"] == "blocked"
        assert body["previous_state"] == "blocked"
        assert "terminal" in (body["note"] or "")

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
