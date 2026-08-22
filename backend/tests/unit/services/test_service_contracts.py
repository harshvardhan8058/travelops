"""Service input fact contracts.

The purpose of the contract is to move a missing-input failure from runtime to preflight, and to
make its message name a column rather than describe a symptom. These tests hold that line.
"""

from __future__ import annotations

from app.models.enums import ActionType
from app.services.contracts import (
    SERVICE_INPUT_SPECS,
    ContractCatalogue,
    missing_facts_for,
    required_facts_for,
    spec_for,
)


def test_every_declared_action_type_is_a_real_action_type():
    """A contract for an action that does not exist would never be checked, and would drift
    unnoticed until someone trusted it."""
    valid = {action.value for action in ActionType}
    for key in SERVICE_INPUT_SPECS:
        assert key in valid, key


def test_every_fact_names_a_loader_and_the_tables_behind_it():
    """ "Where does this number come from" is the question the whole system exists to answer.
    A fact with no source could not answer it."""
    for spec in SERVICE_INPUT_SPECS.values():
        assert spec.facts
        for fact in spec.facts:
            assert fact.source
            # Only the orchestrator-supplied scoping hint has no tables of its own.
            assert fact.tables or fact.source == "orchestrator target_refs"


def test_every_service_declares_what_it_will_not_do():
    """Scope boundaries belong on a declared surface, so widening one is a visible change
    rather than a quiet edit to a docstring."""
    for spec in SERVICE_INPUT_SPECS.values():
        assert spec.scope_exclusions


def test_crew_impact_declares_the_duty_time_boundary():
    """The boundary most costly to breach. It is asserted in three places now: the AST test over
    the service, the docstring, and here on the published contract."""
    spec = spec_for(ActionType.assess_crew_impact.value)
    assert spec is not None
    joined = " ".join(spec.scope_exclusions)
    assert "duty_hours_limit" in joined
    assert "replacement roster" in joined


def test_connection_declares_that_alternatives_are_not_availability():
    spec = spec_for(ActionType.check_connections.value)
    assert spec is not None
    assert any("schedule feasible only" in item for item in spec.scope_exclusions)


def test_hotel_availability_is_declared_as_derived_from_holds():
    """The contract has to say that `hotel.available_rooms` is not the source, or a future
    caller will reasonably assume the obvious column is the right one."""
    spec = spec_for(ActionType.find_hotel_options.value)
    assert spec is not None
    fact = next(f for f in spec.facts if f.name == "hotel_options")
    assert "hotel_inventory_hold" in fact.tables
    assert "never hotel.available_rooms" in fact.note


def test_required_facts_are_the_ones_without_a_documented_default():
    """`business_constraints` is optional everywhere, because absent rows fall back to a
    documented default and the result says which it used."""
    for action_type, spec in SERVICE_INPUT_SPECS.items():
        assert "business_constraints" in spec.optional_facts, action_type
        assert "business_constraints" not in required_facts_for(action_type)


def test_an_empty_list_is_a_finding_but_none_is_a_missing_fact():
    """The distinction this contract exists for. Zero broken connections is a real answer;
    nobody having looked is not, and conflating them turns silence into good news."""
    action = ActionType.check_connections.value
    assert missing_facts_for(action, {"itineraries": [], "flights": {}}) == []
    missing = missing_facts_for(action, {"itineraries": None, "flights": {}})
    assert [fact.name for fact in missing] == ["itineraries"]


def test_the_missing_message_names_the_loader_and_the_tables():
    spec = spec_for(ActionType.check_connections.value)
    assert spec is not None
    message = spec.explain_missing({"flights": {}})
    assert "itineraries" in message
    assert "load_connection_inputs" in message
    assert "booking_segment" in message
    assert "no rows behind it" in message


def test_a_complete_bag_reports_no_missing_facts():
    spec = spec_for(ActionType.assess_crew_impact.value)
    assert spec is not None
    supplied = {"affected_flights": [], "pairings": [], "flights": {}}
    assert spec.missing_from(supplied) == []
    assert "All required facts present" in spec.explain_missing(supplied)


def test_an_undeclared_action_does_not_block_dispatch():
    """A contract that has not been written yet must not stop a service that worked before the
    contract existed. Failing open here is the safe direction: the service still refuses on its
    own if an input is genuinely missing."""
    assert required_facts_for("some_future_action") == ()
    assert missing_facts_for("some_future_action", {}) == []
    assert spec_for("some_future_action") is None


def test_the_catalogue_is_ordered_so_the_published_surface_is_stable():
    catalogue = ContractCatalogue.build()
    action_types = [spec.action_type for spec in catalogue.specs]
    assert action_types == sorted(action_types)
    payload = catalogue.payload()
    assert len(payload["specs"]) == len(SERVICE_INPUT_SPECS)
    assert "refuses" in payload["note"]
