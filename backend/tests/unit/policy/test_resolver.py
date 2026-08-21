"""Tri-state applicability.

The property under test throughout: a missing fact produces `undetermined`, never
`not_applicable`. Collapsing unknown into false is how a system quietly denies a passenger an
entitlement, and it would do so while looking like it worked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PolicyMode
from app.models.enums import ApplicabilityStatus
from app.policy.loader import load_pack
from app.policy.resolver import (
    NEEDS_HUMAN,
    PROCEED,
    REASON_NO_APPLICABLE_PACK,
    REASON_UNRESOLVED_PACK_OVERLAP,
    RESOLVER_VERSION,
    resolve,
    select,
)

PACKS_ROOT = Path(__file__).resolve().parents[4] / "policy_packs"

COMPLETE_CONTEXT = {
    "itinerary": {
        "origin_country": "IN",
        "destination_country": "IN",
        "scheduled_departure_local": "2026-08-20T21:10:00+05:30",
    },
    "operating_carrier": {"id": "AI", "country": "IN"},
    "event": {"type": "delay", "travel_date": "2026-08-20"},
}


@pytest.fixture(scope="module")
def pack():
    return load_pack(
        pack_dir=PACKS_ROOT,
        pack_id="in-moca-charter-2019",
        version="2019.02",
        mode=PolicyMode.charter,
    )


def _without(path: str) -> dict:
    """A copy of the complete context with one dotted path removed."""
    import copy

    context = copy.deepcopy(COMPLETE_CONTEXT)
    family, _, leaf = path.partition(".")
    context[family].pop(leaf)
    return context


class TestTriState:
    def test_complete_indian_itinerary_is_applicable(self, pack):
        [result] = resolve(trip_context=COMPLETE_CONTEXT, packs=[pack])
        assert result.status is ApplicabilityStatus.applicable
        assert result.basis == {"itinerary.origin_country": "IN"}
        assert result.missing_facts == []

    @pytest.mark.parametrize(
        "path",
        [
            "itinerary.origin_country",
            "itinerary.destination_country",
            "itinerary.scheduled_departure_local",
            "operating_carrier.id",
            "event.type",
            "event.travel_date",
        ],
    )
    def test_any_missing_required_fact_is_undetermined_never_not_applicable(self, pack, path):
        [result] = resolve(trip_context=_without(path), packs=[pack])
        assert result.status is ApplicabilityStatus.undetermined
        assert result.status is not ApplicabilityStatus.not_applicable
        assert path in result.missing_facts

    def test_a_null_fact_is_treated_as_absent(self, pack):
        context = _without("event.type")
        context["event"]["type"] = None
        [result] = resolve(trip_context=context, packs=[pack])
        assert result.status is ApplicabilityStatus.undetermined

    def test_foreign_itinerary_with_all_facts_is_not_applicable(self, pack):
        """`not_applicable` is reachable, but only on facts we actually have."""
        context = {
            "itinerary": {
                "origin_country": "FR",
                "destination_country": "DE",
                "scheduled_departure_local": "2026-08-20T09:00:00+02:00",
            },
            "operating_carrier": {"id": "AF", "country": "FR"},
            "event": {"type": "delay", "travel_date": "2026-08-20"},
        }
        [result] = resolve(trip_context=context, packs=[pack])
        assert result.status is ApplicabilityStatus.not_applicable

    def test_one_unknown_condition_prevents_not_applicable(self, pack):
        """Origin is not India, but the carrier's country is unknown.

        One of the two applicability conditions cannot be decided, so the honest answer is
        `undetermined`. Answering `not_applicable` would deny an entitlement on a guess.
        """
        context = {
            "itinerary": {
                "origin_country": "FR",
                "destination_country": "IN",
                "scheduled_departure_local": "2026-08-20T09:00:00+02:00",
            },
            "operating_carrier": {"id": "AI"},  # country absent
            "event": {"type": "delay", "travel_date": "2026-08-20"},
        }
        [result] = resolve(trip_context=context, packs=[pack])
        assert result.status is ApplicabilityStatus.undetermined
        assert "operating_carrier.country" in result.missing_facts

    def test_a_satisfied_condition_wins_over_an_unknown_one(self, pack):
        """Tri-state OR: one true disjunct settles it, so nothing is left undetermined."""
        context = {
            "itinerary": {
                "origin_country": "IN",
                "destination_country": "SG",
                "scheduled_departure_local": "2026-08-20T21:10:00+05:30",
            },
            "operating_carrier": {"id": "SQ"},  # country absent, but origin already matches
            "event": {"type": "delay", "travel_date": "2026-08-20"},
        }
        [result] = resolve(trip_context=context, packs=[pack])
        assert result.status is ApplicabilityStatus.applicable

    def test_empty_context_is_undetermined(self, pack):
        [result] = resolve(trip_context={}, packs=[pack])
        assert result.status is ApplicabilityStatus.undetermined
        assert len(result.missing_facts) == len(pack.required_facts)

    def test_result_names_the_pack_and_its_required_facts(self, pack):
        [result] = resolve(trip_context=COMPLETE_CONTEXT, packs=[pack])
        assert result.pack_id == "in-moca-charter-2019"
        assert result.pack_version == "2019.02"
        assert result.required_facts == pack.required_facts


class TestSelection:
    def test_a_single_applicable_pack_proceeds(self, pack):
        resolution = select(trip_context=COMPLETE_CONTEXT, packs=[pack])
        assert resolution.decision == PROCEED
        assert resolution.selected == ["in-moca-charter-2019"]
        assert resolution.conflicts == []
        assert resolution.resolver_version == RESOLVER_VERSION

    def test_undetermined_applicability_needs_a_human(self, pack):
        resolution = select(trip_context=_without("event.type"), packs=[pack])
        assert resolution.decision == NEEDS_HUMAN
        assert resolution.requires_human
        assert "event.type" in resolution.missing_facts

    def test_no_applicable_pack_needs_a_human(self, pack):
        """Having no reviewed rules for an itinerary is not the same as nothing being owed."""
        context = {
            "itinerary": {
                "origin_country": "FR",
                "destination_country": "DE",
                "scheduled_departure_local": "2026-08-20T09:00:00+02:00",
            },
            "operating_carrier": {"id": "AF", "country": "FR"},
            "event": {"type": "delay", "travel_date": "2026-08-20"},
        }
        resolution = select(trip_context=context, packs=[pack])
        assert resolution.decision == NEEDS_HUMAN
        assert resolution.blocking_reasons == [REASON_NO_APPLICABLE_PACK]

    def test_no_packs_at_all_needs_a_human(self):
        resolution = select(trip_context=COMPLETE_CONTEXT, packs=[])
        assert resolution.decision == NEEDS_HUMAN

    def test_an_unreviewed_overlap_needs_a_human(self, pack):
        """No global most-favourable-to-passenger rule is assumed.

        Two packs applying with no reviewed precedence is an unresolved legal question, and
        picking the more generous one would be an unreviewed judgement that looks helpful.
        """
        second = pack.model_copy(update={"pack_id": "in-other-regime"})
        resolution = select(trip_context=COMPLETE_CONTEXT, packs=[pack, second])
        assert resolution.decision == NEEDS_HUMAN
        assert resolution.blocking_reasons == [REASON_UNRESOLVED_PACK_OVERLAP]
        assert sorted(resolution.conflicts) == ["in-moca-charter-2019", "in-other-regime"]

    def test_an_overlap_proceeds_only_when_conflict_rules_are_reviewed(self, pack):
        reviewed = pack.model_copy(update={"conflict_rules_defined": True})
        second = reviewed.model_copy(update={"pack_id": "in-other-regime"})
        resolution = select(trip_context=COMPLETE_CONTEXT, packs=[reviewed, second])
        assert resolution.decision == PROCEED
        assert len(resolution.selected) == 2

    def test_every_candidate_is_reported_even_when_blocked(self, pack):
        resolution = select(trip_context={}, packs=[pack])
        assert len(resolution.candidates) == 1
        assert resolution.candidates[0].status is ApplicabilityStatus.undetermined
