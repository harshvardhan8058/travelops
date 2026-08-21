"""Contract tests: the properties that make hallucination structurally impossible."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from app.agents.contract import (
    ExplanationResponse,
    PlannerResponse,
    ReasoningResponse,
    ReportResponse,
)
from app.assurance.contract import (
    CHECK_ORDER,
    AssuranceConfig,
    AssuranceResult,
    CheckName,
    CheckResult,
    ReasonCode,
)
from app.events.types import DomainEvent
from app.models.enums import AssuranceDecision, CheckState, RiskTier

reasoning = TypeAdapter(ReasoningResponse)
events = TypeAdapter(DomainEvent)


def _planner(**overrides):
    payload = {
        "status": "success",
        "reason": "Protect time-sensitive connections first",
        "payload_type": "planner.v1",
        "tasks": [{"action": "check_connections", "target_refs": ["flight:AI203"]}],
    }
    payload.update(overrides)
    return payload


class TestReasoningContracts:
    def test_planner_routes_by_discriminator(self):
        assert isinstance(reasoning.validate_python(_planner()), PlannerResponse)

    def test_explanation_and_report_are_distinct_types(self):
        explanation = reasoning.validate_python(
            {
                "status": "success",
                "reason": "r",
                "payload_type": "explanation.v1",
                "explanation": "Visibility fell below threshold",
            }
        )
        report = reasoning.validate_python(
            {
                "status": "success",
                "reason": "r",
                "payload_type": "report.v1",
                "summary": "Recovered",
            }
        )
        assert isinstance(explanation, ExplanationResponse)
        assert isinstance(report, ReportResponse)

    @pytest.mark.parametrize(
        "action",
        [
            "wire_money",
            "delete_bookings",
            "reserve_hotel",  # near-miss for reserve_hotel_block
            "NOTIFY_PASSENGERS",  # wrong case
            "",
        ],
    )
    def test_unknown_action_types_are_rejected(self, action: str):
        with pytest.raises(ValidationError):
            reasoning.validate_python(_planner(tasks=[{"action": action, "target_refs": ["x"]}]))

    def test_confidence_is_not_part_of_the_contract(self):
        """LLM self-reported confidence must be structurally impossible to submit."""
        with pytest.raises(ValidationError):
            reasoning.validate_python(_planner(confidence=92))

    def test_empty_plan_is_rejected(self):
        with pytest.raises(ValidationError):
            reasoning.validate_python(_planner(tasks=[]))

    def test_blank_target_ref_is_rejected(self):
        with pytest.raises(ValidationError):
            reasoning.validate_python(
                _planner(tasks=[{"action": "check_connections", "target_refs": ["  "]}])
            )


class TestEventContracts:
    def test_event_gets_identity_automatically(self):
        event = events.validate_python(
            {
                "event_type": "HIGH_RISK_DELAY",
                "producer": "delay_risk",
                "flight_id": 1,
                "risk_index": 87,
                "risk_level": "high",
                "rule_version": "delay-risk-v1",
            }
        )
        assert event.event_id and event.schema_version == "1"
        assert event.occurred_at.tzinfo is not None, "timestamps must be timezone-aware"

    @pytest.mark.parametrize("index", [-1, 101, 150])
    def test_risk_index_range_is_enforced(self, index: int):
        with pytest.raises(ValidationError):
            events.validate_python(
                {
                    "event_type": "HIGH_RISK_DELAY",
                    "producer": "x",
                    "flight_id": 1,
                    "risk_index": index,
                    "risk_level": "high",
                    "rule_version": "v1",
                }
            )


class TestAssuranceContract:
    def test_six_checks_in_fixed_order(self):
        assert len(CHECK_ORDER) == 6
        assert CHECK_ORDER[0] is CheckName.evidence_complete
        assert CHECK_ORDER[-1] is CheckName.action_risk

    def test_warn_is_representable_and_survives_serialisation(self):
        result = AssuranceResult(
            decision=AssuranceDecision.needs_human,
            risk_tier=RiskTier.high,
            checks=[
                CheckResult(
                    name=CheckName.sources_fresh,
                    state=CheckState.warn,
                    reason_code=ReasonCode.SOURCE_STALE,
                    reason="64m old",
                ),
            ],
            blocking=[CheckName.action_risk],
            config_version="assurance-v1",
            config_hash="deadbeef",
        )
        dumped = result.model_dump(mode="json")
        assert dumped["checks"][0]["state"] == "WARN"
        assert result.requires_human and not result.executable

    def test_unknown_action_is_high_risk(self):
        config = AssuranceConfig(version="t", risk_tiers={"check_connections": RiskTier.low})
        assert config.tier_for("check_connections") is RiskTier.low
        assert config.tier_for("never_seen_before") is RiskTier.high

    def test_warn_is_not_permitted_by_default(self):
        config = AssuranceConfig(version="t")
        assert not config.warn_permitted("anything", CheckName.sources_fresh)
