"""Fail-closed aggregation, config loading, and the gate entry point.

The aggregation order is the safety property the whole project rests on, so it is asserted
rule by rule and then again end to end through `evaluate`.

The tests that matter most:

  * `test_warn_never_silently_becomes_execute` — the only route to execute_flagged is an
    explicit config entry.
  * `test_high_risk_blocks_even_when_every_check_passes` — rule 3 outranks a clean sheet.
  * `test_missing_config_yields_needs_human_not_a_permissive_default`.
  * `test_gate_digest_matches_what_system_mode_reports` — an evaluation and /system/mode must
    never disagree about which config was in force.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.assurance.contract import (
    CHECK_ORDER,
    AssuranceConfig,
    CheckName,
    CheckResult,
    ReasonCode,
)
from app.assurance.gate import (
    CONFIG_UNAVAILABLE,
    GateInputs,
    aggregate,
    evaluate,
    load_config,
    load_config_with_digest,
)
from app.config import Settings, resolve_modes
from app.errors import AssuranceConfigMissing
from app.models.enums import AssuranceDecision, CheckState, RiskTier

NOW = datetime(2026, 8, 20, 15, 40, tzinfo=UTC)

CONFIG = AssuranceConfig(
    version="assurance-v1-test",
    risk_tiers={
        "check_connections": RiskTier.low,
        "find_hotel_options": RiskTier.low,
        "reserve_hotel_block": RiskTier.medium,
        "notify_passengers": RiskTier.high,
    },
    warn_allowed_actions={"find_hotel_options": [CheckName.sources_fresh]},
)

DIGEST = "0123456789abcdef"


def _check(name: CheckName, state: CheckState = CheckState.passed, **kwargs) -> CheckResult:
    return CheckResult(name=name, state=state, **kwargs)


def _all_passing(*, tier: RiskTier = RiskTier.low) -> list[CheckResult]:
    return [
        _check(name, tier=tier if name is CheckName.action_risk else None) for name in CHECK_ORDER
    ]


# --------------------------------------------------------------------- aggregation order


class TestRuleOneHardFailures:
    def test_missing_config_yields_needs_human_not_a_permissive_default(self):
        result = aggregate(checks=_all_passing(), action_type="check_connections", config=None)
        assert result.decision is AssuranceDecision.needs_human
        assert result.risk_tier is RiskTier.high
        assert all(check.state is CheckState.failed for check in result.checks)
        assert all(check.reason_code is ReasonCode.CONFIG_MISSING for check in result.checks)
        assert result.blocking == list(CHECK_ORDER)

    def test_missing_config_records_that_it_could_not_name_its_semantics(self):
        result = aggregate(checks=[], action_type="check_connections", config=None)
        assert result.config_version == CONFIG_UNAVAILABLE
        assert result.config_hash == CONFIG_UNAVAILABLE

    def test_unknown_action_type_fails_rather_than_being_classified(self):
        result = aggregate(
            checks=_all_passing(), action_type="wire_money", config=CONFIG, config_hash=DIGEST
        )
        assert result.decision is AssuranceDecision.needs_human
        assert result.risk_tier is RiskTier.high
        risk = next(c for c in result.checks if c.name is CheckName.action_risk)
        assert risk.state is CheckState.failed
        assert risk.reason_code is ReasonCode.UNKNOWN_ACTION_TYPE
        assert CheckName.action_risk in result.blocking

    def test_unknown_rule_operator_blocks(self):
        checks = _all_passing()
        checks[3] = _check(
            CheckName.policy_compliant,
            CheckState.failed,
            reason_code=ReasonCode.UNKNOWN_RULE_OPERATOR,
        )
        result = aggregate(checks=checks, action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert CheckName.policy_compliant in result.blocking

    @pytest.mark.parametrize(
        "code",
        [
            ReasonCode.CONFIG_MISSING,
            ReasonCode.UNKNOWN_ACTION_TYPE,
            ReasonCode.UNKNOWN_RULE_OPERATOR,
        ],
    )
    def test_hard_fail_codes_are_coerced_to_fail_even_if_reported_as_pass(self, code):
        """Rule 1 holds even if a check is later written to be more forgiving."""
        checks = _all_passing()
        checks[1] = _check(CheckName.sources_fresh, CheckState.passed, reason_code=code)
        result = aggregate(checks=checks, action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert CheckName.sources_fresh in result.blocking

    def test_a_check_that_did_not_run_blocks(self):
        """Six checks or no authorisation. A short list is not a clean sheet."""
        partial = [c for c in _all_passing() if c.name is not CheckName.no_conflicts]
        result = aggregate(checks=partial, action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        absent = next(c for c in result.checks if c.name is CheckName.no_conflicts)
        assert absent.state is CheckState.failed
        assert absent.reason_code is ReasonCode.MISSING_EVIDENCE

    def test_empty_check_list_blocks(self):
        result = aggregate(checks=[], action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert len(result.checks) == 6


class TestRuleTwoAnyFailure:
    def test_single_failure_blocks_everything(self):
        checks = _all_passing()
        checks[0] = _check(
            CheckName.evidence_complete,
            CheckState.failed,
            reason_code=ReasonCode.MISSING_REQUIRED_FACT,
        )
        result = aggregate(checks=checks, action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert not result.executable
        assert result.requires_human
        assert result.blocking == [CheckName.evidence_complete]

    def test_failure_wins_over_a_permitted_warning(self):
        checks = _all_passing()
        checks[0] = _check(
            CheckName.evidence_complete,
            CheckState.failed,
            reason_code=ReasonCode.MISSING_REQUIRED_FACT,
        )
        checks[1] = _check(
            CheckName.sources_fresh, CheckState.warn, reason_code=ReasonCode.SOURCE_STALE
        )
        result = aggregate(checks=checks, action_type="find_hotel_options", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human

    def test_high_risk_is_also_named_as_blocking_alongside_a_failure(self):
        """Matches the recorded shape: a failed check and an unapproved high tier both block."""
        checks = _all_passing(tier=RiskTier.high)
        checks[0] = _check(
            CheckName.evidence_complete,
            CheckState.failed,
            reason_code=ReasonCode.MISSING_REQUIRED_FACT,
        )
        result = aggregate(checks=checks, action_type="notify_passengers", config=CONFIG)
        assert result.blocking == [CheckName.evidence_complete, CheckName.action_risk]

    def test_blocking_is_reported_in_check_order(self):
        checks = _all_passing()
        checks[4] = _check(
            CheckName.no_conflicts, CheckState.failed, reason_code=ReasonCode.DUPLICATE_ACTION
        )
        checks[0] = _check(
            CheckName.evidence_complete,
            CheckState.failed,
            reason_code=ReasonCode.MISSING_REQUIRED_FACT,
        )
        result = aggregate(checks=checks, action_type="check_connections", config=CONFIG)
        assert result.blocking == [CheckName.evidence_complete, CheckName.no_conflicts]

    def test_worst_state_wins_for_a_duplicated_check(self):
        checks = [
            *_all_passing(),
            _check(CheckName.sources_fresh, CheckState.failed, reason_code=ReasonCode.SOURCE_STALE),
        ]
        result = aggregate(checks=checks, action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert len(result.checks) == 6, "the record always holds exactly six checks"


class TestRuleThreeHighRisk:
    def test_high_risk_blocks_even_when_every_check_passes(self):
        result = aggregate(
            checks=_all_passing(tier=RiskTier.high),
            action_type="notify_passengers",
            config=CONFIG,
            config_hash=DIGEST,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert result.blocking == [CheckName.action_risk]
        assert all(check.state is CheckState.passed for check in result.checks), (
            "every check passed and the action was still refused — the point of the design"
        )

    def test_tier_comes_from_the_classification_of_record(self):
        """action_risk's reported tier governs, so a replay cannot be re-tiered by config."""
        result = aggregate(
            checks=_all_passing(tier=RiskTier.high), action_type="check_connections", config=CONFIG
        )
        assert result.risk_tier is RiskTier.high
        assert result.decision is AssuranceDecision.needs_human

    def test_config_is_the_fallback_when_the_tier_was_not_reported(self):
        checks = [c for c in _all_passing() if c.name is not CheckName.action_risk]
        result = aggregate(checks=checks, action_type="notify_passengers", config=CONFIG)
        assert result.risk_tier is RiskTier.high

    def test_medium_risk_with_all_passing_executes(self):
        result = aggregate(
            checks=_all_passing(tier=RiskTier.medium),
            action_type="reserve_hotel_block",
            config=CONFIG,
        )
        assert result.decision is AssuranceDecision.execute

    def test_high_risk_gate_can_be_relaxed_only_by_versioned_config(self):
        """The shipped config sets high_risk_requires_human true; nothing else can waive it."""
        shipped, _ = load_config_with_digest("./config/assurance.v1.yaml")
        assert shipped.high_risk_requires_human is True

        relaxed = CONFIG.model_copy(update={"high_risk_requires_human": False})
        result = aggregate(
            checks=_all_passing(tier=RiskTier.high),
            action_type="notify_passengers",
            config=relaxed,
        )
        assert result.decision is AssuranceDecision.execute


class TestRuleFourWarnings:
    def test_warn_never_silently_becomes_execute(self):
        """A warning on an action with no config entry blocks. There is no global bypass."""
        checks = _all_passing()
        checks[1] = _check(
            CheckName.sources_fresh, CheckState.warn, reason_code=ReasonCode.SOURCE_STALE
        )
        result = aggregate(checks=checks, action_type="check_connections", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert result.blocking == [CheckName.sources_fresh]

    def test_permitted_warning_yields_execute_flagged(self):
        checks = _all_passing()
        checks[1] = _check(
            CheckName.sources_fresh, CheckState.warn, reason_code=ReasonCode.SOURCE_STALE
        )
        result = aggregate(checks=checks, action_type="find_hotel_options", config=CONFIG)
        assert result.decision is AssuranceDecision.execute_flagged
        assert result.blocking == []
        assert result.executable

    def test_the_warning_must_be_permitted_for_that_specific_check(self):
        """find_hotel_options tolerates a stale source, not any warning whatsoever."""
        checks = _all_passing()
        checks[3] = _check(
            CheckName.policy_compliant,
            CheckState.warn,
            reason_code=ReasonCode.POLICY_CONSTRAINT_BREACH,
        )
        result = aggregate(checks=checks, action_type="find_hotel_options", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human
        assert result.blocking == [CheckName.policy_compliant]

    def test_multiple_warnings_never_become_safer_by_aggregation(self):
        checks = _all_passing()
        checks[1] = _check(
            CheckName.sources_fresh, CheckState.warn, reason_code=ReasonCode.SOURCE_STALE
        )
        checks[4] = _check(
            CheckName.no_conflicts,
            CheckState.warn,
            reason_code=ReasonCode.CAPACITY_UNAVAILABLE,
        )
        result = aggregate(checks=checks, action_type="find_hotel_options", config=CONFIG)
        assert result.decision is AssuranceDecision.needs_human, (
            "one tolerated warning must not carry an untolerated one through"
        )
        assert result.blocking == [CheckName.no_conflicts]

    def test_high_risk_outranks_a_permitted_warning(self):
        permissive = CONFIG.model_copy(
            update={"warn_allowed_actions": {"notify_passengers": [CheckName.sources_fresh]}}
        )
        checks = _all_passing(tier=RiskTier.high)
        checks[1] = _check(
            CheckName.sources_fresh, CheckState.warn, reason_code=ReasonCode.SOURCE_STALE
        )
        result = aggregate(checks=checks, action_type="notify_passengers", config=permissive)
        assert result.decision is AssuranceDecision.needs_human


class TestRuleFiveOtherwise:
    def test_all_passing_low_risk_executes(self):
        result = aggregate(
            checks=_all_passing(),
            action_type="check_connections",
            config=CONFIG,
            config_hash=DIGEST,
        )
        assert result.decision is AssuranceDecision.execute
        assert result.blocking == []
        assert result.executable and not result.requires_human


class TestRecordIdentity:
    def test_every_evaluation_records_config_version_and_hash(self):
        result = aggregate(
            checks=_all_passing(),
            action_type="check_connections",
            config=CONFIG,
            config_hash=DIGEST,
        )
        assert result.config_version == "assurance-v1-test"
        assert result.config_hash == DIGEST

    def test_a_content_fingerprint_is_marked_as_such(self):
        """It must never be mistaken for the file digest reported by /system/mode."""
        result = aggregate(checks=_all_passing(), action_type="check_connections", config=CONFIG)
        assert result.config_hash.startswith("content:")

    def test_evaluated_at_is_timezone_aware(self):
        result = aggregate(checks=_all_passing(), action_type="check_connections", config=CONFIG)
        assert result.evaluated_at.tzinfo is not None

    def test_checks_are_always_in_fixed_order(self):
        shuffled = list(reversed(_all_passing()))
        result = aggregate(checks=shuffled, action_type="check_connections", config=CONFIG)
        assert [check.name for check in result.checks] == list(CHECK_ORDER)


# ------------------------------------------------------------------------- load_config


class TestLoadConfig:
    def test_the_real_config_loads(self):
        config = load_config("./config/assurance.v1.yaml")
        assert config.version == "assurance-v1"
        assert config.freshness.metar_minutes == 60
        assert config.tier_for("evaluate_entitlements") is RiskTier.high
        assert config.warn_permitted("find_hotel_options", CheckName.sources_fresh)

    def test_relative_path_resolves_regardless_of_working_directory(self):
        assert load_config("./config/assurance.v1.yaml").version == "assurance-v1"

    def test_missing_file_raises_rather_than_defaulting(self):
        with pytest.raises(AssuranceConfigMissing):
            load_config("/nonexistent/assurance.yaml")

    def test_unparseable_yaml_raises(self, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text("version: [unclosed\n", encoding="utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_config(str(broken))

    def test_non_mapping_yaml_raises(self, tmp_path):
        listy = tmp_path / "listy.yaml"
        listy.write_text("- version: assurance-v1\n", encoding="utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_config(str(listy))

    def test_schema_invalid_config_raises(self, tmp_path):
        """An unknown risk tier must not load as something harmless."""
        invalid = tmp_path / "invalid.yaml"
        invalid.write_text("version: v1\nrisk_tiers:\n  notify_passengers: catastrophic\n", "utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_config(str(invalid))

    def test_unrecognised_key_raises_rather_than_being_ignored(self, tmp_path):
        """A typo in safety config must not read as permissive."""
        typo = tmp_path / "typo.yaml"
        typo.write_text("version: v1\nwarn_allowed_everything: true\n", encoding="utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_config(str(typo))

    def test_empty_file_raises(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(AssuranceConfigMissing):
            load_config(str(empty))

    def test_gate_digest_matches_what_system_mode_reports(self):
        """An evaluation and /system/mode must never disagree about the config in force."""
        _, digest = load_config_with_digest("./config/assurance.v1.yaml")
        modes = resolve_modes(Settings(_env_file=None))
        assert digest == modes.assurance_config_hash
        assert load_config("./config/assurance.v1.yaml").version == modes.assurance_config_version


# ---------------------------------------------------------------------------- evaluate


class TestEvaluate:
    def test_clean_low_risk_action_executes(self):
        result = evaluate(
            inputs=GateInputs(
                action_type="check_connections",
                required_facts=["event.type"],
                provided_facts={"event": {"type": "delay"}},
                sources={"metar:VOBL": NOW - timedelta(minutes=10)},
                referenced_refs=["flight:1"],
                resolved_entities={"flight:1": {"exists": True, "state_matches": True}},
                target_refs=["flight:1"],
            ),
            config=CONFIG,
            config_hash=DIGEST,
            now=NOW,
        )
        assert result.decision is AssuranceDecision.execute
        assert [check.name for check in result.checks] == list(CHECK_ORDER)
        assert result.config_hash == DIGEST

    def test_the_bengaluru_entitlement_case_blocks_on_missing_evidence(self):
        """A weather trigger with no reasonable-measures evidence must never authorise."""
        result = evaluate(
            inputs=GateInputs(
                action_type="evaluate_entitlements",
                required_facts=[
                    "cause_evidence.external_to_carrier",
                    "cause_evidence.unavoidable_despite_reasonable_measures",
                ],
                provided_facts={
                    "cause_evidence": {
                        "operational_cause": "meteorological",
                        "external_to_carrier": True,
                        "unavoidable_despite_reasonable_measures": None,
                    }
                },
                extra_evidence_refs=["incident:INC-2026-0820-VOBL-01"],
            ),
            config=CONFIG,
            config_hash=DIGEST,
            now=NOW,
        )
        assert result.decision is AssuranceDecision.needs_human
        evidence = next(c for c in result.checks if c.name is CheckName.evidence_complete)
        assert evidence.state is CheckState.failed
        assert evidence.reason_code is ReasonCode.MISSING_REQUIRED_FACT
        assert result.blocking == [CheckName.evidence_complete, CheckName.action_risk]
        assert "incident:INC-2026-0820-VOBL-01" in result.evidence_refs

    def test_stale_source_on_a_permitted_action_flags_rather_than_blocks(self):
        result = evaluate(
            inputs=GateInputs(
                action_type="find_hotel_options",
                sources={"metar:VABB": NOW - timedelta(minutes=71)},
            ),
            config=CONFIG,
            config_hash=DIGEST,
            now=NOW,
        )
        assert result.decision is AssuranceDecision.execute_flagged
        assert result.evidence_refs == ["metar:VABB"]

    def test_same_stale_source_on_a_high_risk_action_blocks(self):
        result = evaluate(
            inputs=GateInputs(
                action_type="notify_passengers",
                sources={"metar:VABB": NOW - timedelta(minutes=71)},
            ),
            config=CONFIG,
            config_hash=DIGEST,
            now=NOW,
        )
        assert result.decision is AssuranceDecision.needs_human

    def test_missing_config_blocks_without_running_checks(self):
        result = evaluate(inputs=GateInputs(action_type="check_connections"), config=None, now=NOW)
        assert result.decision is AssuranceDecision.needs_human
        assert all(check.reason_code is ReasonCode.CONFIG_MISSING for check in result.checks)

    def test_the_same_inputs_yield_the_same_decision(self):
        """Reproducibility is the property a confidence score never gave us."""
        inputs = GateInputs(
            action_type="reserve_hotel_block",
            sources={"flight_status:AI2841": NOW - timedelta(minutes=2)},
            payload={"rooms": 12},
            constraints=[{"field": "rooms", "op": "lte", "value": 20}],
            target_refs=["hotel:12"],
        )
        first = evaluate(inputs=inputs, config=CONFIG, config_hash=DIGEST, now=NOW)
        second = evaluate(inputs=inputs, config=CONFIG, config_hash=DIGEST, now=NOW)
        assert first.model_dump(exclude={"evaluated_at"}) == second.model_dump(
            exclude={"evaluated_at"}
        )

    def test_unknown_action_type_cannot_reach_a_risk_classification(self):
        result = evaluate(
            inputs=GateInputs(action_type="wire_money"), config=CONFIG, config_hash=DIGEST, now=NOW
        )
        assert result.decision is AssuranceDecision.needs_human
        risk = next(c for c in result.checks if c.name is CheckName.action_risk)
        assert risk.reason_code is ReasonCode.UNKNOWN_ACTION_TYPE

    def test_inputs_reject_an_unrecognised_field(self):
        """A caller cannot smuggle a confidence score, or anything else, past the gate."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GateInputs(action_type="check_connections", confidence=92)

    def test_a_correction_is_a_new_record_not_an_update(self):
        inputs = GateInputs(action_type="notify_passengers")
        blocked = evaluate(inputs=inputs, config=CONFIG, config_hash=DIGEST, now=NOW)
        assert blocked.decision is AssuranceDecision.needs_human

        approved = CONFIG.model_copy(update={"high_risk_requires_human": False})
        rerun = evaluate(inputs=inputs, config=approved, config_hash=DIGEST, now=NOW)

        assert blocked.decision is AssuranceDecision.needs_human, "the original is untouched"
        assert rerun.decision is AssuranceDecision.execute
        assert rerun is not blocked
