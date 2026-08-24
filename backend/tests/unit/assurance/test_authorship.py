"""Phase 3: what a model-authored proposal is entitled to assert.

Four classes of guard here, in order of how much damage their absence would do:

  * `TestModelCannotAssertSystemDeterminations` — the new Phase 3 hole. The payload the gate
    checks is now written by a model, and several gate constraints are assertions *about* that
    payload, so a model that writes the right key satisfies the check meant to constrain it.
  * `TestProvenanceNeverRelaxesADecision` — a property, asserted over a matrix: for identical
    legitimate inputs, a model-authored proposal reaches the same decision as a deterministic
    one. Authorship refuses claims; it does not re-tier risk.
  * `TestSelfReportNeverGates` — a static scan proving no assurance or policy module so much as
    mentions `model_self_report` or `confidence` in code.
  * `TestPhase2RemainsBackwardCompatible` — omitting authorship reproduces Phase 2 exactly.
"""

from __future__ import annotations

import ast
import pathlib
from typing import ClassVar

import pytest

from app.assurance.authorship import (
    CONSTRAINT_SYSTEM_AUTHORED,
    CONSTRAINT_UNCORROBORATED_EVIDENCE,
    Authorship,
    ProposalAuthorship,
    authorship_constraints,
    authorship_record,
    load_authority,
)
from app.assurance.blocking import KIND_CONFLICT, blocking_kinds, is_approvable
from app.assurance.checks import policy_compliant
from app.assurance.contract import CheckName, ReasonCode
from app.assurance.gate import GateInputs, evaluate, load_config_with_digest
from app.models.enums import AssuranceDecision, CheckState, RiskTier

BACKEND = pathlib.Path(__file__).resolve().parents[3]

MODEL = ProposalAuthorship.from_model("groq:llama-3.3-70b-versatile", prompt_version="planner-v1")
DETERMINISTIC = ProposalAuthorship.deterministic()


@pytest.fixture(scope="module")
def authority():
    return load_authority()


@pytest.fixture(scope="module")
def gate_config():
    return load_config_with_digest("./config/assurance.v2.yaml")


def _constraints(payload, authorship=MODEL, authority=None, **kwargs):
    return authorship_constraints(
        action_type=kwargs.pop("action_type", "reserve_hotel_block"),
        payload=payload,
        authorship=authorship,
        authority=authority,
        **kwargs,
    )


def _check(result, name: CheckName):
    return next(item for item in result.checks if item.name is name)


# ------------------------------------------------------------------- the new Phase 3 hole


class TestModelCannotAssertSystemDeterminations:
    @pytest.mark.parametrize(
        "field",
        [
            "cash_inr",
            "currency",
            "formula",
            "formula_used",
            "cited_rule_ids",
            "pack_version",
            "pack_hash",
            "pack_status",
            "presented_as_current_law",
            "source_clause_refs",
            "assurance_decision",
            "risk_tier",
            "config_version",
            "config_hash",
            "approved_by",
            "approval_scope",
            "plan_hash",
            "business_constraint_versions",
        ],
    )
    def test_each_reserved_field_refuses_the_proposal(self, field: str, authority):
        constraints = _constraints({field: "anything"}, authority=authority)
        assert [c["id"] for c in constraints] == [CONSTRAINT_SYSTEM_AUTHORED]
        assert constraints[0]["unsatisfiable"] is True

    def test_a_compliant_looking_assertion_is_still_refused(self, authority):
        """`presented_as_current_law: false` reads as compliant and is not the model's to say."""
        constraints = _constraints({"presented_as_current_law": False}, authority=authority)
        assert constraints

    def test_a_falsified_rate_cannot_satisfy_a_rate_cap(self, authority):
        """A model writing rate_inr: 1 would pass a cap while the service books something else."""
        constraints = _constraints(
            {"rate_inr": 1}, authority=authority, action_type="reserve_hotel_block"
        )
        assert constraints
        assert "rate_inr" in constraints[0]["reason"]

    def test_the_same_field_is_permitted_for_an_action_that_does_not_reserve_it(self, authority):
        """rate_inr is reserved per-action, not globally."""
        assert (
            _constraints({"rate_inr": 1}, authority=authority, action_type="notify_passengers")
            == []
        )

    def test_entitlement_specific_fields_are_reserved(self, authority):
        for field in ("entitlements", "cohorts", "exposure_inr"):
            constraints = _constraints(
                {field: []}, authority=authority, action_type="evaluate_entitlements"
            )
            assert constraints, field

    def test_several_reserved_fields_are_named_in_one_refusal(self, authority):
        constraints = _constraints(
            {"cash_inr": 9000, "pack_version": "2019.02"}, authority=authority
        )
        assert len(constraints) == 1
        assert "cash_inr" in constraints[0]["reason"]
        assert "pack_version" in constraints[0]["reason"]

    def test_an_ordinary_operational_field_is_untouched(self, authority):
        """A model proposing legitimate work must not be obstructed."""
        assert _constraints({"passengers": 40, "hotel_id": 3}, authority=authority) == []

    def test_the_refusal_is_not_approvable(self, gate_config, authority):
        """An operator cannot make a fabricated assertion true by agreeing with it."""
        config, digest = gate_config
        result = evaluate(
            inputs=GateInputs(
                action_type="reserve_hotel_block",
                payload={"cash_inr": 9000},
                constraints=_constraints({"cash_inr": 9000}, authority=authority),
            ),
            config=config,
            config_hash=digest,
        )
        assert result.decision is AssuranceDecision.needs_human
        assert _check(result, CheckName.policy_compliant).state is CheckState.failed
        assert KIND_CONFLICT in blocking_kinds(result)
        assert not is_approvable(result)

    def test_a_deterministic_proposal_may_carry_system_fields(self, authority):
        """The orchestrator computing a figure and recording it is the normal path."""
        assert _constraints({"cash_inr": 5000}, authorship=DETERMINISTIC, authority=authority) == []


class TestModelCannotInventACitation:
    def test_an_uncorroborated_reference_refuses_the_proposal(self, authority):
        constraints = _constraints(
            {},
            authority=authority,
            proposed_evidence_refs=["metar:VOBL:2026-08-20T15:20Z", "ops_event:invented"],
            known_evidence_refs=["metar:VOBL:2026-08-20T15:20Z"],
        )
        assert [c["id"] for c in constraints] == [CONSTRAINT_UNCORROBORATED_EVIDENCE]
        assert "ops_event:invented" in constraints[0]["reason"]

    def test_fully_corroborated_references_pass(self, authority):
        assert (
            _constraints(
                {},
                authority=authority,
                proposed_evidence_refs=["metar:VOBL"],
                known_evidence_refs=["metar:VOBL", "flight:1"],
            )
            == []
        )

    def test_corroboration_is_skipped_when_the_caller_holds_no_ledger(self, authority):
        """Stream B does not hold the evidence ledger and will not guess at one."""
        assert _constraints({}, authority=authority, proposed_evidence_refs=["anything"]) == []

    def test_a_deterministic_proposal_is_not_corroboration_checked(self, authority):
        assert (
            _constraints(
                {},
                authorship=DETERMINISTIC,
                authority=authority,
                proposed_evidence_refs=["ops_event:invented"],
                known_evidence_refs=[],
            )
            == []
        )


# ---------------------------------------------------------------------- the property test


class TestProvenanceNeverRelaxesADecision:
    """Authorship refuses claims. It never re-tiers risk or moves a threshold."""

    @pytest.mark.parametrize(
        ("action", "payload", "required_facts", "provided"),
        [
            ("check_connections", {}, [], {}),
            ("reserve_hotel_block", {"passengers": 40}, [], {}),
            ("notify_passengers", {"template_id": "delay_v2"}, [], {}),
            (
                "check_connections",
                {},
                ["event.type"],
                {"event": {"type": "delay"}},
            ),
            ("check_connections", {}, ["event.type"], {}),
        ],
    )
    def test_identical_legitimate_inputs_reach_identical_decisions(
        self, gate_config, authority, action, payload, required_facts, provided
    ):
        config, digest = gate_config

        def run(authorship):
            return evaluate(
                inputs=GateInputs(
                    action_type=action,
                    payload=payload,
                    required_facts=required_facts,
                    provided_facts=provided,
                    constraints=_constraints(
                        payload, authorship=authorship, authority=authority, action_type=action
                    ),
                ),
                config=config,
                config_hash=digest,
            )

        deterministic = run(DETERMINISTIC)
        model = run(MODEL)

        assert model.decision is deterministic.decision
        assert model.risk_tier is deterministic.risk_tier
        assert [c.state for c in model.checks] == [c.state for c in deterministic.checks]

    def test_a_model_proposal_is_never_more_permissive(self, gate_config, authority):
        """The one-directional guarantee: model authorship can only ever refuse more."""
        config, digest = gate_config
        ranking = {
            AssuranceDecision.execute: 0,
            AssuranceDecision.execute_flagged: 1,
            AssuranceDecision.needs_human: 2,
        }
        payload = {"cash_inr": 5000, "passengers": 12}

        def run(authorship):
            return evaluate(
                inputs=GateInputs(
                    action_type="reserve_hotel_block",
                    payload=payload,
                    constraints=_constraints(payload, authorship=authorship, authority=authority),
                ),
                config=config,
                config_hash=digest,
            )

        assert ranking[run(MODEL).decision] >= ranking[run(DETERMINISTIC).decision]

    def test_authorship_does_not_change_the_risk_tier(self, authority):
        """A high-risk action is high risk whoever proposed it, and no less."""
        from app.assurance.checks import action_risk

        config, _ = load_config_with_digest("./config/assurance.v2.yaml")
        assert action_risk(action_type="notify_passengers", config=config).tier is RiskTier.high
        assert _constraints({}, authorship=MODEL, authority=authority) == []


# ------------------------------------------------------------------ self-report never gates


class TestSelfReportNeverGates:
    """Asserted statically, because a threshold in code is a threshold someone can raise."""

    FORBIDDEN: ClassVar[frozenset[str]] = frozenset({"model_self_report", "confidence"})
    PROTECTED: ClassVar[tuple[str, ...]] = ("app/assurance", "app/policy")

    @staticmethod
    def _docstrings(tree: ast.AST) -> set[int]:
        """Node ids of docstring constants, so prose may discuss what code may not do."""
        ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    ids.add(id(first.value))
        return ids

    def _identifiers(self, path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = self._docstrings(tree)
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, ast.Attribute):
                found.add(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                found.add(node.arg)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                # A string key would let a dict lookup smuggle it back in.
                found.add(node.value)
        return found

    def _files(self) -> list[pathlib.Path]:
        files = [p for rel in self.PROTECTED for p in sorted((BACKEND / rel).rglob("*.py"))]
        assert files, "no protected files discovered"
        return files

    def test_no_assurance_or_policy_module_references_a_self_report(self):
        offenders: dict[str, set[str]] = {}
        for path in self._files():
            hits = self._identifiers(path) & self.FORBIDDEN
            if hits:
                offenders[str(path.relative_to(BACKEND))] = hits
        assert not offenders, f"self-report reached the authorisation boundary: {offenders}"

    def test_the_authorship_projection_has_no_self_report_field(self):
        """Not a field this layer may see, so there is nothing to be tempted by."""
        assert "model_self_report" not in ProposalAuthorship.model_fields

    def test_the_persisted_record_omits_it_too(self):
        record = authorship_record(MODEL)
        assert set(record) == {"authored_by", "generator", "prompt_version"}
        assert record["authored_by"] == "model"

    def test_the_authority_file_has_no_threshold_key(self, authority):
        """There is deliberately no setting that could introduce one."""
        assert set(type(authority).model_fields) == {
            "version",
            "digest",
            "system_authored_fields",
            "system_authored_by_action",
            "evidence",
        }


# ----------------------------------------------------------------- backward compatibility


class TestPhase2RemainsBackwardCompatible:
    def test_omitting_authorship_produces_no_constraints(self, authority):
        assert (
            authorship_constraints(
                action_type="reserve_hotel_block",
                payload={"cash_inr": 9000},
                authorship=None,
                authority=authority,
            )
            == []
        )

    def test_gate_requirements_is_unchanged_without_authorship(self):
        from pathlib import Path

        from app.config import Settings
        from app.policy.requirements import gate_requirements

        packs = Path(__file__).resolve().parents[4] / "policy_packs"
        settings = Settings(_env_file=None, policy_pack_dir=packs)

        before = gate_requirements(action_type="reserve_hotel_block", facts={}, settings=settings)
        assert before.constraints == []

    def test_gate_requirements_adds_the_refusal_when_authorship_is_supplied(self):
        from pathlib import Path

        from app.config import Settings
        from app.policy.requirements import gate_requirements

        packs = Path(__file__).resolve().parents[4] / "policy_packs"
        requirements = gate_requirements(
            action_type="reserve_hotel_block",
            facts={},
            settings=Settings(_env_file=None, policy_pack_dir=packs),
            authorship=MODEL,
            payload={"pack_version": "2019.02"},
        )
        assert [c["id"] for c in requirements.constraints] == [CONSTRAINT_SYSTEM_AUTHORED]

    def test_a_fixture_mode_response_is_still_a_model_response(self):
        """Otherwise the demo path and the live path have different safety properties."""
        assert ProposalAuthorship.from_model("fixture:planner-v1").authored_by is Authorship.model
        assert Authorship.model.is_model
        assert not Authorship.deterministic.is_model


class TestAuthorityFile:
    def test_the_shipped_file_loads(self, authority):
        assert authority.version == "proposal-authority-v1"
        assert "cash_inr" in authority.system_authored_fields
        assert authority.evidence.require_corroboration is True

    def test_a_missing_file_raises_rather_than_permitting_anything(self):
        from app.errors import PolicyPackUnavailable

        with pytest.raises(PolicyPackUnavailable):
            load_authority("/nonexistent/proposal_authority.yaml")

    def test_an_unreadable_file_refuses_the_proposal_rather_than_allowing_it(self, tmp_path):
        """An unreadable authority file must not mean 'a model may author anything'."""
        broken = tmp_path / "broken.yaml"
        broken.write_text("system_authored_fields: [unclosed\n", encoding="utf-8")

        from app.errors import PolicyPackUnavailable

        with pytest.raises(PolicyPackUnavailable):
            load_authority(broken)

    def test_the_digest_is_stable(self):
        assert load_authority().digest == load_authority().digest

    def test_per_action_fields_extend_the_global_list(self, authority):
        globals_ = set(authority.system_authored_fields)
        hotel = set(authority.system_fields_for("reserve_hotel_block"))
        assert globals_ < hotel
        assert "rate_inr" in hotel

    def test_the_gate_actually_refuses_a_fabricated_entitlement(self, authority):
        """End to end through policy_compliant, the check that carries it."""
        result = policy_compliant(
            action_type="evaluate_entitlements",
            payload={"cash_inr": 25000},
            constraints=_constraints(
                {"cash_inr": 25000}, authority=authority, action_type="evaluate_entitlements"
            ),
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.POLICY_CONSTRAINT_BREACH
