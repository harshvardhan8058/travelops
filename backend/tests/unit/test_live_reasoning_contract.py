"""What a real model is allowed to return for the two read-only artifacts.

`LLM_MODE=fixture` validated the committed fixtures, which are schema-perfect by construction,
so nothing here was ever exercised until a live Groq call arrived. In live mode both
`/incidents/{ref}/explanation` and `/reports/{group}` returned 500 because `AgentEnvelope` is
`extra="forbid"` and the two prose prompts — unlike `planner.v1.md` — never told the model to
omit extra fields, keep `reason` short, or return nothing but JSON.

The seam these tests pin: strictness is right where output authorises action and wrong where it
does not. `PlannerResponse.tasks[]` reaches the assurance gate and then execution, so an
undeclared field must be fatal. An explanation authorises nothing, so an undeclared field must
be dropped and the artifact still delivered.

Owner: Stream C.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.contract import (
    ExplanationResponse,
    PlannerResponse,
    ReportResponse,
    ReportSection,
)
from app.llm.client import _coerce_self_report


def _explanation(**overrides) -> dict:
    payload = {
        "status": "success",
        "reason": "Explains the completed recovery.",
        "evidence_refs": ["action:1"],
        "payload_type": "explanation.v1",
        "explanation": "Visibility fell below the threshold and the departure was held.",
        "citation_refs": ["action:check_connections:1"],
    }
    payload.update(overrides)
    return payload


def _report(**overrides) -> dict:
    payload = {
        "status": "success",
        "reason": "Executive report for the group.",
        "evidence_refs": ["group:GRP-2026-0820-VOBL"],
        "payload_type": "report.v1",
        "summary": "Eight flights and 604 passengers were affected.",
        "sections": [{"heading": "Scope", "body": "Eight flights affected at VOBL."}],
        "metric_refs": ["rollup:flights_affected:8"],
    }
    payload.update(overrides)
    return payload


def _planner(**overrides) -> dict:
    payload = {
        "status": "success",
        "reason": "Protect time-sensitive connections first.",
        "payload_type": "planner.v1",
        "tasks": [{"action": "check_connections", "target_refs": ["flight:1"]}],
    }
    payload.update(overrides)
    return payload


class TestTheProseArtifactsToleratWhatTheModelVolunteers:
    """These three cases each produced a 500 in live mode."""

    @pytest.mark.parametrize(
        "extra",
        [
            {"confidence": 0.92},
            {"model_self_report": 91},
            {"certainty": "high"},
            {"notes": "Generated from recorded actions."},
        ],
    )
    def test_an_unsolicited_key_is_dropped_not_fatal_for_an_explanation(self, extra: dict):
        parsed = ExplanationResponse.model_validate(_explanation(**extra))
        assert parsed.explanation
        for key in extra:
            assert not hasattr(parsed, key), f"{key} must be dropped, not stored"

    def test_an_unsolicited_key_inside_a_report_section_is_dropped(self):
        payload = _report(
            sections=[{"heading": "Scope", "body": "Eight flights.", "bullets": ["a", "b"]}]
        )
        parsed = ReportResponse.model_validate(payload)
        assert [s.heading for s in parsed.sections] == ["Scope"]
        assert not hasattr(parsed.sections[0], "bullets")

    def test_a_reason_longer_than_the_envelope_cap_is_accepted_for_prose(self):
        """2000 chars stops a planner writing an essay in a justification field.

        For these two the prose *is* the deliverable, and a verbose `reason` is not worth
        refusing the whole artifact over.
        """
        assert ExplanationResponse.model_validate(_explanation(reason="R" * 2500)).reason
        assert ReportResponse.model_validate(_report(reason="R" * 2500)).reason


class TestThePlannerStaysStrict:
    """The other half of the seam. Relaxing the artifacts must not relax the proposal."""

    def test_confidence_is_still_structurally_impossible_for_a_planner(self):
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(_planner(confidence=91))

    def test_an_unknown_key_is_still_rejected_for_a_planner(self):
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(_planner(notes="anything"))

    def test_a_planner_reason_is_still_capped(self):
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(_planner(reason="R" * 2500))

    def test_a_task_still_forbids_extra_keys(self):
        with pytest.raises(ValidationError):
            PlannerResponse.model_validate(
                _planner(
                    tasks=[{"action": "check_connections", "target_refs": ["flight:1"], "why": "x"}]
                )
            )


class TestTheRequiredFieldsAreStillRequired:
    """`extra="ignore"` widens what is tolerated, not what is optional."""

    def test_an_explanation_without_explanation_text_is_rejected(self):
        payload = _explanation()
        del payload["explanation"]
        with pytest.raises(ValidationError):
            ExplanationResponse.model_validate(payload)

    def test_an_empty_explanation_is_rejected(self):
        with pytest.raises(ValidationError):
            ExplanationResponse.model_validate(_explanation(explanation=""))

    def test_a_report_without_a_summary_is_rejected(self):
        payload = _report()
        del payload["summary"]
        with pytest.raises(ValidationError):
            ReportResponse.model_validate(payload)

    def test_a_wrong_payload_type_is_rejected(self):
        with pytest.raises(ValidationError):
            ExplanationResponse.model_validate(_explanation(payload_type="report.v1"))

    def test_an_invented_status_is_rejected(self):
        with pytest.raises(ValidationError):
            ReportResponse.model_validate(_report(status="probably_fine"))

    def test_a_section_still_requires_both_fields(self):
        with pytest.raises(ValidationError):
            ReportSection.model_validate({"heading": "Scope"})


class TestTheSelfReportIsDiagnosticNotFatal:
    """`ModelCallAudit.model_self_report` is an int 0..100 and the audit is never branched on.

    Before the fix the client passed `raw.get("model_self_report")` straight in, so a model that
    answered with a string failed `ModelCallAudit` validation and took the whole artifact with
    it — a 503 caused by an unsolicited diagnostic.
    """

    @pytest.mark.parametrize("value", [0, 50, 100, 91.0])
    def test_a_value_inside_the_contract_is_kept(self, value):
        assert _coerce_self_report(value, agent_name="explainer") == int(value)

    @pytest.mark.parametrize(
        "value",
        [
            "llama-3.3-70b-versatile",  # a model naming itself
            0.92,  # a confidence on a 0-1 scale
            101,
            -1,
            True,  # bool is an int subclass; not a self-report
            {"score": 91},
            ["high"],
        ],
    )
    def test_a_value_outside_the_contract_is_discarded_without_raising(self, value):
        assert _coerce_self_report(value, agent_name="reporter") is None

    def test_absent_stays_absent(self):
        assert _coerce_self_report(None, agent_name="explainer") is None

    def test_a_fractional_confidence_is_not_rescaled_into_a_different_claim(self):
        """`int(0.92)` is 0 — a confident model recorded as a diffident one.

        Guessing the scale would invent a figure, which is the one thing these agents may
        never do, so the value is dropped instead.
        """
        assert _coerce_self_report(0.92, agent_name="explainer") is None
