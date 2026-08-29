"""G4: the incident policy endpoint serves the authoritative policy layer, not a fixture.

The properties worth protecting are all about honesty rather than coverage. A policy surface that
fabricates a figure, or that reports an absent fact as zero, is more dangerous than one that returns
nothing — so these tests assert what the response must *refuse* to say.

Owner: Stream A. Every figure originates in Stream B's engine.
"""

from __future__ import annotations

import json
from pathlib import Path

PREFIX = "/api/v1"
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "api" / "policy.json"


class TestItIsNoLongerAFixture:
    def test_the_response_is_not_the_committed_fixture(self, client, incident):
        """The clearest possible statement that G4 landed: the payload is computed, not replayed."""
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        committed = json.loads(FIXTURE.read_text(encoding="utf-8"))

        assert body["generated_by"] != committed["generated_by"]
        assert body["generated_by"] == "policy-engine"

    def test_an_unknown_incident_is_a_404_not_a_sample_payload(self, client):
        """A fixture route answered for any id, which is how a demo shows data for nothing."""
        response = client.get(f"{PREFIX}/incidents/INC-DOES-NOT-EXIST/policy")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ENTITY_NOT_FOUND"

    def test_the_endpoint_writes_nothing(self, client, incident):
        """Evaluating policy is a read. Asserted by the action count, which a write would move."""
        before = client.get(f"{PREFIX}/incidents/{incident}").json()["actions"]
        client.get(f"{PREFIX}/incidents/{incident}/policy")
        after = client.get(f"{PREFIX}/incidents/{incident}").json()["actions"]
        assert len(before) == len(after)


class TestProvenanceSurvives:
    def test_the_pack_identity_reaches_the_response(self, client, incident):
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        pack = body["pack"]

        assert pack["id"]
        assert pack["version"]
        assert pack["pack_hash"], "a citation without a pack hash is not reproducible"
        assert pack["authority"]
        assert pack["document"]

    def test_the_pack_label_is_read_from_the_pack_not_composed(self, client, incident):
        """Compared against the pack file itself, so a hardcoded string here would fail."""
        from app.policy.entitlements import load_active_pack

        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        assert body["pack"]["ui_label"] == load_active_pack().ui_label

    def test_the_pack_status_and_eligibility_are_reported_as_the_pack_states_them(
        self, client, incident
    ):
        from app.policy.entitlements import load_active_pack

        pack = load_active_pack()
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()

        assert body["pack"]["status"] == getattr(pack.status, "value", str(pack.status))
        assert body["pack"]["verified_mode_eligible"] is pack.verified_mode_eligible

    def test_clause_references_survive_to_the_response(self, client, incident):
        """A figure with no clause behind it is an assertion, not a citation."""
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        rows = body["entitlements"]
        assert rows

        for row in rows:
            if row["amount_inr"]:
                assert row["source_clause_refs"], f"{row['type']} cites nothing"

    def test_the_resolver_version_is_recorded_on_applicability(self, client, incident):
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        for item in body["applicability"]:
            assert item["resolver_version"], "an applicability decision must be replayable"


class TestUnknownStaysUnknown:
    def test_a_missing_fact_is_named_not_defaulted(self, client, incident):
        """The tri-state guarantee. Absent is reported as absent, with the fact named."""
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()

        for item in body["applicability"]:
            assert item["status"] in {"applicable", "not_applicable", "undetermined"}
            if item["status"] == "undetermined":
                assert item["missing_facts"], "undetermined without naming what was absent"

    def test_no_entitlement_amount_is_fabricated(self, client, incident):
        """An undetermined outcome must not carry an amount. That is the fabrication to prevent."""
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()

        for row in body["entitlements"]:
            if row["outcome"] == "undetermined":
                assert row["amount_inr"] is None, f"{row['type']} invented a figure"

    def test_a_cause_exemption_is_never_inferred_from_the_trigger(self, client, incident):
        """The load-bearing test in this file.

        The incident's trigger is weather. Inferring "external to carrier, unavoidable despite
        reasonable measures" from that word would assert a legal exemption from no evidence — the
        one inference `db/trip_context.py` and `services/compensation.py` both refuse to make. With
        no recorded cause assessment every flag must be null, which is different from false.
        """
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        cause = body["cause_assessment"]

        for flag in (
            "clearly_attributable",
            "external_to_carrier",
            "unavoidable_despite_reasonable_measures",
        ):
            assert cause[flag] is None, f"{flag} was asserted without a recorded assessment"
        assert "never a legal verdict" in cause["note"]

    def test_the_disclaimer_states_the_packs_real_standing(self, client, incident):
        """A dated pack must not be presented as current law."""
        from app.policy.entitlements import load_active_pack

        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        pack = load_active_pack()

        if not pack.verified_mode_eligible:
            assert "not current law" in body["disclaimer"]

    def test_excluded_rules_are_shown_with_their_reason(self, client, incident):
        """A rule the pack withholds is visible, never silently absent."""
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        for rule in body["excluded_rules"]:
            assert rule["evaluated"] is False
            assert rule["reason"]

    def test_the_source_hash_is_the_digest_the_pack_records(self, client, incident):
        """Passed through verbatim from G3's `source_content_sha256`, sentinel included.

        This assertion previously required `null`, which was correct only while nothing published a
        source digest. G3 landed and the endpoint kept returning `null`, so the console could not
        tell "no digest recorded" from "digest pending archival" — it reported the pack's source
        integrity as `unknown` and the `PENDING_ARCHIVAL` sentinel never reached the DOM.
        """
        from app.policy.entitlements import load_active_pack
        from app.policy.loader import PENDING_ARCHIVAL

        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        pack = load_active_pack()

        assert body["pack"]["source_hash"] == pack.source_content_sha256
        # The charter pack's primary document is not archived, so the recorded digest is the
        # sentinel. Asserted against the loader's own constant, not a copy of the string.
        assert body["pack"]["source_hash"] == PENDING_ARCHIVAL
        assert pack.source_document_verified is False

    def test_the_source_hash_is_not_the_pack_label(self, client, incident):
        """Two different fields, and conflating them is what made this look like a UI defect.

        `ui_label` ends in "pending CAR verification"; `source_hash` is the document digest. A badge
        reading the label is not evidence that the digest reached the response.
        """
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()

        assert body["pack"]["source_hash"] != body["pack"]["ui_label"]
        assert "pending CAR verification" in body["pack"]["ui_label"]
        assert "CAR" not in (body["pack"]["source_hash"] or "")


class TestTheCauseComparisonIsAReEvaluation:
    def test_it_uses_the_same_pack_and_only_substitutes_an_input(self, client, incident):
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        comparison = body["cause_comparison"]

        assert comparison["enabled"] is True
        alternative = comparison["alternative"]
        assert alternative["external_to_carrier"] is False
        assert "not a forecast" in comparison["description"]

    def test_an_undetermined_counterfactual_names_what_was_absent(self, client, incident):
        """A blank comparison reads as "nothing changes", which is a different claim.

        On the real dataset the cancellation counterfactual is undetermined because
        `cancellation.notice_obligation_met` is not recorded. The response must say that rather
        than render an empty cash figure.
        """
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        alternative = body["cause_comparison"]["alternative"]

        if alternative["outcome"] == "undetermined":
            assert alternative["cash_inr"] is None
            assert alternative["missing_facts"], "undetermined without naming a fact"
            assert "undetermined" in alternative["note"]

    def test_the_alternative_figure_is_cited(self, client, incident):
        body = client.get(f"{PREFIX}/incidents/{incident}/policy").json()
        alternative = body["cause_comparison"]["alternative"]

        if alternative["cash_inr"]:
            assert alternative["source_clause_refs"], "an amount with no clause behind it"
            assert alternative["formula_used"], "an amount with no derivation"


class TestPhase1To3BehaviourIsUnchanged:
    def test_the_incident_still_runs_to_awaiting_approval(self, client, incident):
        """G4 is a read-only surface; it must not touch the workflow."""
        client.get(f"{PREFIX}/incidents/{incident}/policy")
        body = client.post(f"{PREFIX}/incidents/{incident}/run").json()
        assert body["state"] == "awaiting_approval"

    def test_the_gate_and_timeline_are_untouched(self, client, incident):
        client.post(f"{PREFIX}/incidents/{incident}/run")
        client.get(f"{PREFIX}/incidents/{incident}/policy")
        entries = client.get(f"{PREFIX}/incidents/{incident}/timeline").json()["entries"]

        assert any(entry["event_type"] == "ASSURANCE_EVALUATED" for entry in entries)
        assert not any("POLICY" in entry["event_type"] for entry in entries)
