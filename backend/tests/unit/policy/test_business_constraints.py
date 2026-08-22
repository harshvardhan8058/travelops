"""Commercial limits, translated into gate constraints.

Grounded in what Stream C actually seeds. The rows here mirror
`data/generators/scenario_dataset.py`, and `test_the_seeded_hotel_cap_is_enforced_by_the_gate`
asserts the real one reaches `policy_compliant` and refuses an over-cap reservation before the
action runs, instead of the service refusing internally where nothing records it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.assurance.checks import policy_compliant
from app.assurance.contract import ReasonCode
from app.errors import PolicyPackUnavailable
from app.models.enums import CheckState
from app.policy.business_constraints import (
    business_constraint_versions,
    constraints_from_rows,
    load_mappings,
)

#: The shape `app.db.scenario_queries.load_business_constraints` returns.
HOTEL_CAP: dict[str, Any] = {
    "service": "hotel_service",
    "constraint_key": "max_rate_inr",
    "constraint_value": {"inr": 6000},
    "is_hard": True,
    "version": "v1",
}
SOFT_PARTNER: dict[str, Any] = {
    "service": "hotel_service",
    "constraint_key": "prefer_partner",
    "constraint_value": {"enabled": True},
    "is_hard": False,
    "version": "v1",
}
CONNECTION_MINIMUM: dict[str, Any] = {
    "service": "connection_service",
    "constraint_key": "minimum_connection_minutes",
    "constraint_value": {"minutes": 45},
    "is_hard": True,
    "version": "v1",
}


@pytest.fixture(scope="module")
def mappings():
    return load_mappings()


class TestMappingFile:
    def test_the_shipped_file_loads(self, mappings):
        assert mappings.version == "action-requirements-v1"
        assert mappings.mappings

    def test_every_mapped_operator_is_one_the_gate_can_evaluate(self, mappings):
        """A skipped limit is a limit that does not exist."""
        from app.assurance.checks import CONSTRAINT_OPERATORS

        assert {m.op for m in mappings.mappings} <= CONSTRAINT_OPERATORS

    def test_an_unsupported_operator_refuses_the_file(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            "version: v1\nmappings:\n  - service: s\n    constraint_key: k\n"
            "    field: f\n    op: approximately\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyPackUnavailable, match="unsupported operator"):
            load_mappings(path)

    def test_a_missing_file_raises_rather_than_meaning_no_limits(self):
        with pytest.raises(PolicyPackUnavailable):
            load_mappings("/nonexistent/action_requirements.yaml")

    def test_unreadable_yaml_raises(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("mappings: [unclosed\n", encoding="utf-8")
        with pytest.raises(PolicyPackUnavailable):
            load_mappings(path)

    def test_an_unrecognised_mapping_key_raises(self, tmp_path):
        path = tmp_path / "typo.yaml"
        path.write_text(
            "version: v1\nmappings:\n  - service: s\n    constraint_key: k\n"
            "    field: f\n    op: lte\n    applies_to_everything: true\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyPackUnavailable):
            load_mappings(path)


class TestTranslation:
    def test_the_hotel_cap_becomes_a_gate_constraint(self, mappings):
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        assert constraints == [
            {
                "id": "business.hotel_service.max_rate_inr",
                "field": "rate_inr",
                "op": "lte",
                "value": 6000,
            }
        ]

    def test_the_value_is_never_restated_only_read(self, mappings):
        """Change the row and the constraint changes. Nothing is hardcoded in the mapping."""
        raised = {**HOTEL_CAP, "constraint_value": {"inr": 9500}}
        [constraint] = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[raised], mappings=mappings
        )
        assert constraint["value"] == 9500

    def test_an_action_the_mapping_does_not_cover_gets_nothing(self, mappings):
        assert (
            constraints_from_rows(
                action_type="check_connections", rows=[HOTEL_CAP], mappings=mappings
            )
            == []
        )

    def test_an_unseeded_row_is_absent_not_invented(self, mappings):
        """Absence of a limit is not a violation, and inventing one would be writing policy."""
        assert (
            constraints_from_rows(action_type="reserve_hotel_block", rows=[], mappings=mappings)
            == []
        )

    def test_rows_with_no_mapping_are_ignored(self, mappings):
        """Occupancy assumptions and minimum connection times are not payload limits."""
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block",
            rows=[SOFT_PARTNER, CONNECTION_MINIMUM],
            mappings=mappings,
        )
        assert constraints == []

    def test_a_malformed_value_blocks_rather_than_disappearing(self, mappings):
        """A limit nobody can evaluate must block; dropping it would let the action through."""
        broken = {**HOTEL_CAP, "constraint_value": {"rupees": 6000}}
        [constraint] = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[broken], mappings=mappings
        )
        assert constraint["unsatisfiable"] is True
        assert "could not be read" in constraint["reason"]

    def test_is_hard_is_obeyed_not_decided(self, mappings):
        """Stream C owns the hard/soft distinction because they own the row."""
        soft_cap = {**HOTEL_CAP, "is_hard": False}
        [constraint] = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[soft_cap], mappings=mappings
        )
        assert constraint["soft"] is True

    def test_versions_are_recorded_for_the_audit_trail(self):
        versions = business_constraint_versions([HOTEL_CAP, CONNECTION_MINIMUM])
        assert versions == {
            "hotel_service.max_rate_inr": "v1",
            "connection_service.minimum_connection_minutes": "v1",
        }

    def test_translation_is_deterministic(self, mappings):
        first = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        second = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        assert first == second


class TestTheGateActuallyRefuses:
    def test_the_seeded_hotel_cap_is_enforced_by_the_gate(self, mappings):
        """An over-cap reservation is refused as an authorisation decision, not a service error."""
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 9000, "rooms": 40},
            constraints=constraints,
        )
        assert result.state is CheckState.failed
        assert result.reason_code is ReasonCode.POLICY_CONSTRAINT_BREACH
        assert result.reason is not None
        assert "business.hotel_service.max_rate_inr" in result.reason

    def test_a_within_cap_reservation_passes(self, mappings):
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        result = policy_compliant(
            action_type="reserve_hotel_block",
            payload={"rate_inr": 5200},
            constraints=constraints,
        )
        assert result.state is CheckState.passed

    def test_exactly_at_the_cap_passes(self, mappings):
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        result = policy_compliant(
            action_type="reserve_hotel_block", payload={"rate_inr": 6000}, constraints=constraints
        )
        assert result.state is CheckState.passed

    def test_a_payload_that_never_mentions_the_rate_is_not_penalised_today(self, mappings):
        """No mapping sets require_field, because Stream A's adapters build inputs from the DB.

        Demanding a payload field the orchestrator does not send would refuse every action, so
        the mechanism exists and is deliberately unused until a payload carries the field.
        """
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[HOTEL_CAP], mappings=mappings
        )
        assert all(not m.require_field for m in mappings.mappings)
        result = policy_compliant(
            action_type="reserve_hotel_block", payload={}, constraints=constraints
        )
        assert result.reason_code is ReasonCode.MISSING_REQUIRED_FACT
        assert result.state is CheckState.failed, (
            "a constraint cannot be shown to hold against a missing value"
        )

    def test_a_malformed_limit_blocks_the_action(self, mappings):
        broken = {**HOTEL_CAP, "constraint_value": {"rupees": 6000}}
        constraints = constraints_from_rows(
            action_type="reserve_hotel_block", rows=[broken], mappings=mappings
        )
        result = policy_compliant(
            action_type="reserve_hotel_block", payload={"rate_inr": 10}, constraints=constraints
        )
        assert result.state is CheckState.failed


class TestRequirementsIntegration:
    def test_a_non_policy_action_now_carries_its_commercial_limits(self):
        """Previously this returned empty constraints and policy_compliant verified nothing."""
        from app.config import Settings
        from app.policy.requirements import gate_requirements

        requirements = gate_requirements(
            action_type="reserve_hotel_block",
            facts={},
            settings=Settings(_env_file=None),
            business_rows=[HOTEL_CAP, SOFT_PARTNER],
        )
        assert requirements.policy_bearing is False
        assert [c["id"] for c in requirements.constraints] == [
            "business.hotel_service.max_rate_inr"
        ]
        assert requirements.business_constraint_versions == {"hotel_service.max_rate_inr": "v1"}

    def test_omitting_the_rows_changes_nothing(self):
        """A caller that has not wired them yet behaves exactly as before."""
        from app.config import Settings
        from app.policy.requirements import gate_requirements

        requirements = gate_requirements(
            action_type="reserve_hotel_block", facts={}, settings=Settings(_env_file=None)
        )
        assert requirements.constraints == []
        assert requirements.business_constraint_versions == {}

    def test_a_policy_bearing_action_carries_both_sets(self):
        """A statutory entitlement and a commercial limit can both apply."""
        from pathlib import Path

        from app.config import Settings
        from app.policy.requirements import gate_requirements

        packs_root = Path(__file__).resolve().parents[4] / "policy_packs"
        requirements = gate_requirements(
            action_type="evaluate_entitlements",
            facts={},
            settings=Settings(_env_file=None, policy_pack_dir=packs_root),
            business_rows=[HOTEL_CAP],
        )
        assert requirements.policy_bearing is True
        ids = {c.get("id") for c in requirements.constraints}
        assert "policy.pack_version_matches" in ids
        assert requirements.business_constraint_versions == {}, (
            "the hotel cap does not apply to an entitlement action"
        )
