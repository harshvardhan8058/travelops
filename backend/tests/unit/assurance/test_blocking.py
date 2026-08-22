"""Why a decision blocked, and whether it may be approved.

`test_only_risk_is_approvable` is the assertion the whole Phase 2 approval model rests on. If it
weakens, an operator can click past a missing fact and the gate becomes theatre.
"""

from __future__ import annotations

import pytest

from app.assurance.blocking import (
    KIND_CONFLICT,
    KIND_EVIDENCE,
    KIND_RISK,
    blocking_kinds,
    is_approvable,
    kind_for,
    unapprovable_reasons,
)
from app.assurance.contract import AssuranceResult, CheckName, CheckResult, ReasonCode
from app.models.enums import AssuranceDecision, CheckState, RiskTier


def _result(*checks: CheckResult, decision=AssuranceDecision.needs_human) -> AssuranceResult:
    return AssuranceResult(
        decision=decision,
        risk_tier=RiskTier.high,
        checks=list(checks),
        config_version="assurance-v2",
        config_hash="deadbeef",
    )


def _check(name: CheckName, state: CheckState, code: ReasonCode) -> CheckResult:
    return CheckResult(name=name, state=state, reason_code=code)


class TestClassification:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            (ReasonCode.HUMAN_APPROVAL_REQUIRED, KIND_RISK),
            (ReasonCode.MISSING_REQUIRED_FACT, KIND_EVIDENCE),
            (ReasonCode.MISSING_EVIDENCE, KIND_EVIDENCE),
            (ReasonCode.SOURCE_STALE, KIND_EVIDENCE),
            (ReasonCode.SOURCE_MISSING_TIMESTAMP, KIND_EVIDENCE),
            (ReasonCode.ENTITY_NOT_FOUND, KIND_EVIDENCE),
            (ReasonCode.ENTITY_STATE_MISMATCH, KIND_EVIDENCE),
            (ReasonCode.CONFIG_MISSING, KIND_EVIDENCE),
            (ReasonCode.POLICY_PACK_UNAVAILABLE, KIND_EVIDENCE),
            (ReasonCode.UNKNOWN_ACTION_TYPE, KIND_EVIDENCE),
            (ReasonCode.UNKNOWN_RULE_OPERATOR, KIND_EVIDENCE),
            (ReasonCode.DUPLICATE_ACTION, KIND_CONFLICT),
            (ReasonCode.CAPACITY_UNAVAILABLE, KIND_CONFLICT),
            (ReasonCode.POLICY_CONSTRAINT_BREACH, KIND_CONFLICT),
        ],
    )
    def test_every_reason_code_is_classified(self, code: ReasonCode, expected: str):
        assert kind_for(code) == expected

    def test_every_reason_code_in_the_contract_is_covered(self):
        """A new code must not arrive unclassified and default to approvable."""
        for code in ReasonCode:
            if code is ReasonCode.OK:
                continue
            assert kind_for(code) in {KIND_RISK, KIND_EVIDENCE, KIND_CONFLICT}

    def test_an_unmapped_code_is_evidence_never_risk(self):
        assert kind_for("SOMETHING_NEW") == KIND_EVIDENCE  # type: ignore[arg-type]

    def test_kinds_are_reported_most_actionable_first(self):
        result = _result(
            _check(
                CheckName.evidence_complete, CheckState.failed, ReasonCode.MISSING_REQUIRED_FACT
            ),
            _check(CheckName.no_conflicts, CheckState.failed, ReasonCode.DUPLICATE_ACTION),
            _check(CheckName.action_risk, CheckState.passed, ReasonCode.HUMAN_APPROVAL_REQUIRED),
        )
        assert blocking_kinds(result) == [KIND_EVIDENCE, KIND_CONFLICT, KIND_RISK]


class TestApprovability:
    def test_only_risk_is_approvable(self):
        """Approval covers risk. It cannot manufacture a fact or resolve a conflict."""
        risk_only = _result(
            _check(CheckName.action_risk, CheckState.passed, ReasonCode.HUMAN_APPROVAL_REQUIRED)
        )
        assert is_approvable(risk_only)

        for code in (ReasonCode.MISSING_REQUIRED_FACT, ReasonCode.DUPLICATE_ACTION):
            blocked = _result(
                _check(CheckName.evidence_complete, CheckState.failed, code),
                _check(
                    CheckName.action_risk, CheckState.passed, ReasonCode.HUMAN_APPROVAL_REQUIRED
                ),
            )
            assert not is_approvable(blocked)

    def test_a_high_risk_action_whose_checks_all_pass_still_reports_risk(self):
        """The tier blocks while its own check PASSES.

        Without reading the classification, an all-passing high-risk action would report no
        blocking kind at all and read as approvable by accident — which happens to be right, but
        for the wrong reason, and would be wrong the moment a check failed.
        """
        result = _result(
            _check(CheckName.evidence_complete, CheckState.passed, ReasonCode.OK),
            _check(CheckName.action_risk, CheckState.passed, ReasonCode.HUMAN_APPROVAL_REQUIRED),
        )
        assert blocking_kinds(result) == [KIND_RISK]
        assert is_approvable(result)

    def test_an_executable_result_reports_nothing_blocking(self):
        result = _result(
            _check(CheckName.evidence_complete, CheckState.passed, ReasonCode.OK),
            decision=AssuranceDecision.execute,
        )
        assert blocking_kinds(result) == []
        assert not is_approvable(result), "nothing to approve is not the same as approvable"

    def test_a_warn_does_not_make_something_unapprovable(self):
        """A WARN is not a failure; it either permitted execution or blocked as a FAIL elsewhere."""
        result = _result(
            _check(CheckName.sources_fresh, CheckState.warn, ReasonCode.SOURCE_STALE),
            _check(CheckName.action_risk, CheckState.passed, ReasonCode.HUMAN_APPROVAL_REQUIRED),
        )
        assert is_approvable(result)

    def test_unresolved_reasons_name_what_to_fix(self):
        result = _result(
            _check(
                CheckName.evidence_complete, CheckState.failed, ReasonCode.MISSING_REQUIRED_FACT
            ),
            _check(CheckName.no_conflicts, CheckState.failed, ReasonCode.CAPACITY_UNAVAILABLE),
            _check(CheckName.action_risk, CheckState.passed, ReasonCode.HUMAN_APPROVAL_REQUIRED),
        )
        assert unapprovable_reasons(result) == ["CAPACITY_UNAVAILABLE", "MISSING_REQUIRED_FACT"]

    def test_config_missing_is_not_approvable(self):
        """A gate that could not run is never approved past."""
        result = _result(
            *[
                _check(name, CheckState.failed, ReasonCode.CONFIG_MISSING)
                for name in (CheckName.evidence_complete, CheckName.action_risk)
            ]
        )
        assert not is_approvable(result)
