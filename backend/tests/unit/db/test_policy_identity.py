"""`resolver_hash`: one decision, one identity — Phase 4 G5.

The column has existed since the initial migration with no producer, so a persisted
applicability row could not be told apart from a later row decided on different facts against a
re-authored pack. These tests pin the four properties that make it useful: the same resolution
always hashes the same, a caller's pack ordering does not change it, a change to the facts the
resolution depended on does change it, and a change to the pack's own rules changes it through
`pack_hash`.

Driven through Stream B's real `select()` against the committed charter pack rather than a
hand-built resolution, so the hash is proven over the resolver that actually runs.
"""

from __future__ import annotations

import copy

import pytest

from app.db.policy_identity import (
    HASH_LENGTH,
    RESOLVER_IDENTITY_VERSION,
    canonical_resolution,
    compute_resolver_hash,
    consulted_facts,
)
from app.models.enums import ApplicabilityStatus
from app.policy.resolver import NEEDS_HUMAN, PROCEED, select

APPLICABLE_CONTEXT = {
    "itinerary": {
        "origin_country": "IN",
        "destination_country": "IN",
        "scheduled_departure_local": "2026-08-20T21:10:00+05:30",
    },
    "operating_carrier": {"id": "6E", "country": "IN"},
    "event": {"type": "delay", "travel_date": "2026-08-20"},
}


def _hash(context: dict, packs: list) -> str:
    resolution = select(trip_context=context, packs=packs)
    return compute_resolver_hash(resolution=resolution, trip_context=context, packs=packs)


class TestTheHashIsDeterministic:
    def test_the_same_resolution_hashes_identically(self, charter):
        first = _hash(APPLICABLE_CONTEXT, [charter])
        second = _hash(APPLICABLE_CONTEXT, [charter])

        assert first == second

    def test_repeated_hashing_of_one_resolution_object_is_stable(self, charter):
        resolution = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])

        digests = {
            compute_resolver_hash(
                resolution=resolution, trip_context=APPLICABLE_CONTEXT, packs=[charter]
            )
            for _ in range(5)
        }

        assert len(digests) == 1

    def test_the_hash_is_thirty_two_lowercase_hex_characters(self, charter):
        digest = _hash(APPLICABLE_CONTEXT, [charter])

        assert len(digest) == HASH_LENGTH
        assert digest == digest.lower()
        assert all(character in "0123456789abcdef" for character in digest)

    def test_key_order_in_the_trip_context_does_not_change_the_hash(self, charter):
        """Canonical JSON, so a caller building the same facts in a different order agrees."""
        reordered = {
            "event": dict(reversed(list(APPLICABLE_CONTEXT["event"].items()))),
            "operating_carrier": APPLICABLE_CONTEXT["operating_carrier"],
            "itinerary": dict(reversed(list(APPLICABLE_CONTEXT["itinerary"].items()))),
        }

        assert _hash(reordered, [charter]) == _hash(APPLICABLE_CONTEXT, [charter])


class TestChangedInputsChangeTheHash:
    def test_a_fact_the_resolution_turned_on_changes_the_hash(self, charter):
        """`origin_country` decides applicability, so changing it must change the identity."""
        elsewhere = copy.deepcopy(APPLICABLE_CONTEXT)
        elsewhere["itinerary"]["origin_country"] = "GB"
        elsewhere["operating_carrier"]["country"] = "GB"

        assert _hash(elsewhere, [charter]) != _hash(APPLICABLE_CONTEXT, [charter])

    def test_a_declared_required_fact_changes_the_hash_even_without_flipping_the_status(
        self, charter
    ):
        """`event.travel_date` is required but is not an applicability condition.

        Both resolutions are `applicable`; the inputs differ, so the identities must differ.
        Recording only the outcome would collapse two decisions made on different facts.
        """
        later = copy.deepcopy(APPLICABLE_CONTEXT)
        later["event"]["travel_date"] = "2026-08-21"

        original = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])
        changed = select(trip_context=later, packs=[charter])
        assert original.decision == changed.decision == PROCEED

        assert _hash(later, [charter]) != _hash(APPLICABLE_CONTEXT, [charter])

    def test_a_missing_required_fact_changes_the_hash(self, charter):
        incomplete = copy.deepcopy(APPLICABLE_CONTEXT)
        del incomplete["event"]["travel_date"]

        resolution = select(trip_context=incomplete, packs=[charter])
        assert resolution.decision == NEEDS_HUMAN

        assert _hash(incomplete, [charter]) != _hash(APPLICABLE_CONTEXT, [charter])

    def test_a_changed_pack_hash_changes_the_hash(self, charter):
        """An edited applicability or entitlement rule reaches this hash through `pack_hash`."""
        edited = charter.model_copy(update={"pack_hash": "0" * 16})

        assert _hash(APPLICABLE_CONTEXT, [edited]) != _hash(APPLICABLE_CONTEXT, [charter])

    def test_a_changed_resolver_version_changes_the_hash(self, charter):
        resolution = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])
        rewritten = resolution.model_copy(update={"resolver_version": "resolver-v2"})

        assert compute_resolver_hash(
            resolution=rewritten, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        ) != compute_resolver_hash(
            resolution=resolution, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        )

    def test_a_different_decision_changes_the_hash(self, charter):
        applicable = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])
        blocked = applicable.model_copy(
            update={"decision": NEEDS_HUMAN, "blocking_reasons": ["UNRESOLVED_PACK_OVERLAP"]}
        )

        assert compute_resolver_hash(
            resolution=blocked, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        ) != compute_resolver_hash(
            resolution=applicable, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        )


class TestWhatDeliberatelyDoesNotChangeTheHash:
    def test_the_order_packs_are_passed_in_does_not_change_the_hash(self, charter):
        """Pack iteration order is a caller detail, not a resolver input."""
        second = charter.model_copy(update={"pack_id": "zz-second-pack"})

        forward = _hash(APPLICABLE_CONTEXT, [charter, second])
        reversed_order = _hash(APPLICABLE_CONTEXT, [second, charter])

        assert forward == reversed_order

    def test_a_fact_no_candidate_declares_does_not_change_the_hash(self, charter):
        """The hash identifies the resolution, not the whole trip context.

        A fact the resolver never declared or consulted did not participate in the decision, so
        moving it must not invalidate an identity a stored row depends on. The entitlement side
        is pinned separately by `entitlement_evaluation.input_facts`.
        """
        annotated = copy.deepcopy(APPLICABLE_CONTEXT)
        annotated["hotel"] = {"rate_inr": 4200}

        assert _hash(annotated, [charter]) == _hash(APPLICABLE_CONTEXT, [charter])


class TestTheHashedDocument:
    def test_the_document_names_its_own_version(self, charter):
        resolution = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])

        document = canonical_resolution(
            resolution=resolution, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        )

        assert document["version"] == RESOLVER_IDENTITY_VERSION
        assert document["resolver_version"] == resolution.resolver_version

    def test_every_candidate_carries_its_pack_hash_and_status(self, charter):
        resolution = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])

        document = canonical_resolution(
            resolution=resolution, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        )

        assert [candidate["pack_hash"] for candidate in document["candidates"]] == [
            charter.pack_hash
        ]
        assert document["candidates"][0]["status"] == ApplicabilityStatus.applicable.value

    def test_candidates_are_sorted_so_caller_ordering_cannot_leak_in(self, charter):
        second = charter.model_copy(update={"pack_id": "aa-first-pack"})
        resolution = select(trip_context=APPLICABLE_CONTEXT, packs=[charter, second])

        document = canonical_resolution(
            resolution=resolution, trip_context=APPLICABLE_CONTEXT, packs=[charter, second]
        )

        pack_ids = [candidate["pack_id"] for candidate in document["candidates"]]
        assert pack_ids == sorted(pack_ids)

    def test_the_document_is_json_serialisable(self, charter):
        import json

        resolution = select(trip_context=APPLICABLE_CONTEXT, packs=[charter])

        document = canonical_resolution(
            resolution=resolution, trip_context=APPLICABLE_CONTEXT, packs=[charter]
        )

        assert json.dumps(document, sort_keys=True, default=str)


class TestConsultedFacts:
    def test_present_facts_are_recorded_with_their_values(self):
        recorded = consulted_facts(
            trip_context=APPLICABLE_CONTEXT,
            paths=["itinerary.origin_country", "event.type"],
        )

        assert recorded == {"itinerary.origin_country": "IN", "event.type": "delay"}

    @pytest.mark.parametrize(
        "context",
        [
            {},
            {"itinerary": {}},
            {"itinerary": {"origin_country": None}},
            {"itinerary": "not-a-mapping"},
        ],
    )
    def test_an_absent_or_null_fact_is_omitted_rather_than_recorded_as_null(self, context):
        """`None` and "not recorded" must not become the same input."""
        assert consulted_facts(trip_context=context, paths=["itinerary.origin_country"]) == {}

    def test_a_declared_fact_that_is_false_is_still_recorded(self):
        """`False` is an answer. Only absence is absence."""
        recorded = consulted_facts(
            trip_context={"passenger": {"contact_info_provided_at_booking": False}},
            paths=["passenger.contact_info_provided_at_booking"],
        )

        assert recorded == {"passenger.contact_info_provided_at_booking": False}
