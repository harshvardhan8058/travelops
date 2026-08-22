"""Render `fixtures/api/incident_group_detail.json` from the cascade spec.

`fixtures/api/*.json` is contractual: Stream A's real endpoints must stay byte-compatible
with it and Stream D renders it directly. So the fixture is not hand-maintained — it is
**derived from the same spec and the same attribution logic the generator and the Crew
Impact service use**. There is no second, independently invented version of the cascade
that could drift away from the dataset.

`backend/tests/contract/test_incident_group_fixture.py` fails if the committed file and a
fresh render disagree, which is what keeps that promise enforceable rather than aspirational.

Run:
    cd backend && uv run python -m data.generators.build_incident_group_fixture

Owner: Stream C.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

from app.config import REPO_ROOT
from app.models.enums import PairingMechanism
from app.services.crew_impact import PairingImpact, attribute_pairing_impacts, explain_identity
from data.generators.cascade_spec import BENGALURU_STORM, IATA_BY_ICAO, CascadeScenario

FIXTURE_PATH = REPO_ROOT / "fixtures" / "api" / "incident_group_detail.json"

GROUP_REFERENCE = "GRP-2026-0820-VOBL"
GENERATED_BY = "data/generators/build_incident_group_fixture.py"

#: Values are the operator-facing wording of the four mechanisms. Keys are the enum, so a
#: renamed mechanism cannot silently leave the legend behind.
MECHANISM_LEGEND: dict[str, str] = {
    PairingMechanism.operating.value: "Crew are working the affected flight",
    PairingMechanism.onward_duty.value: "A later leg of the same pairing is now infeasible",
    PairingMechanism.second_pairing.value: "Cockpit and cabin crew sit on different pairings",
    PairingMechanism.positioning.value: (
        "Crew were travelling as passengers to operate another flight"
    ),
}


def _route(origin_icao: str, destination_icao: str) -> str:
    origin = IATA_BY_ICAO.get(origin_icao, origin_icao)
    destination = IATA_BY_ICAO.get(destination_icao, destination_icao)
    return f"{origin} \u2192 {destination}"


def _why_nine_not_eight(impacts: list[PairingImpact]) -> str:
    """The structural explanation, assembled from the records.

    Every number in this sentence is counted from `impacts`. It explains the arithmetic
    instead of asserting the total, because a reviewer is meant to check it against the
    edges on screen rather than take it on trust.
    """
    return (
        "Crew are assigned to multi-leg pairings, not to individual flights, so the count of "
        "affected rotations has no reason to equal the count of affected flights. "
        + explain_identity(impacts)
        + " Every edge above names the mechanism that put that rotation at risk, so the total "
        "can be counted from the graph rather than taken on trust. Coordination and display "
        "only: duty-time legality is not validated anywhere in this system."
    )


def _graph(scenario: CascadeScenario, at_risk: list[PairingImpact]) -> dict:
    """The cascade as nodes and edges, in the same shape `app.services.cascade_graph` returns.

    Additive to the fixture: every key Stream D already reads is untouched, so the console keeps
    working while the graph view is built. Node refs use `kind:id` — the same vocabulary as
    `evidence_refs` — because there is no node table and a node *is* the row it names.

    `derived_from` is a fixture marker here rather than a row id. The live projection carries a
    real `action:` or `prediction:` reference, and the fixture says plainly that it does not, so
    nobody mistakes a fixture edge for recorded evidence.
    """
    event_ref = f"event:{GROUP_REFERENCE}"
    nodes = [
        {
            "ref": event_ref,
            "kind": "event",
            "label": GROUP_REFERENCE,
            "sublabel": f"Root cause at {scenario.root_airport_icao}",
            "depth": 0,
            "at_risk": True,
            "has_evidence": True,
            "role": None,
        }
    ]
    edges = []

    for spec in scenario.affected:
        flight = spec.flight
        ref = f"flight:{flight.flight_id}"
        nodes.append(
            {
                "ref": ref,
                "kind": "flight",
                "label": flight.flight_number,
                "sublabel": (
                    f"{_route(flight.origin_icao, flight.destination_icao)}, "
                    f"+{flight.delay_minutes} min"
                ),
                "depth": 1,
                "at_risk": True,
                "has_evidence": True,
                "role": spec.membership_role,
            }
        )
        edges.append(
            {
                "source_ref": event_ref,
                "target_ref": ref,
                "edge_kind": "root_cause",
                "mechanism": None,
                "detail": "Delay risk assessed against the recorded weather and runway state.",
                "depth": 1,
                "derived_from": "fixture",
            }
        )

    flights_by_number = {
        flight.flight_number: flight.flight_id for flight in scenario.flights_by_id.values()
    }
    for impact in at_risk:
        ref = f"pairing:{impact.pairing_id}"
        nodes.append(
            {
                "ref": ref,
                "kind": "pairing",
                "label": impact.pairing_reference,
                "sublabel": f"Base {impact.base_icao}",
                "depth": 2,
                "at_risk": True,
                "has_evidence": True,
                "role": None,
            }
        )
        source_id = flights_by_number.get(impact.source_flight_number, impact.source_flight_id)
        edges.append(
            {
                "source_ref": f"flight:{source_id}",
                "target_ref": ref,
                "edge_kind": "crew",
                "mechanism": impact.mechanism.value,
                "detail": impact.detail,
                "depth": 2,
                "derived_from": "fixture",
            }
        )

    counts: dict[str, int] = {}
    for edge in edges:
        counts[edge["edge_kind"]] = counts.get(edge["edge_kind"], 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "edge_counts_by_kind": counts,
        "completeness": {
            "member_flight_count": len(scenario.affected),
            "flights_with_evidence": len(scenario.affected),
            "is_complete": True,
            "note": f"All {len(scenario.affected)} declared flights carry recorded evidence.",
        },
        "note": (
            "Nodes are references to rows that already exist; there is no node table. Edge "
            "provenance is a fixture marker, not a row id — the live projection carries a real "
            "action or prediction reference."
        ),
    }


def _blast_radius(scenario: CascadeScenario, rollups: dict) -> dict:
    """Composition only: every value is repeated from `rollups`, none is calculated here.

    Reports **completeness**, never confidence. Completeness is countable — eight of eight
    flights assessed. A confidence percentage would be a probability nothing in this system is
    calibrated to produce, and one uncheckable figure sitting next to five checkable ones takes
    the credibility of all six.
    """
    dimensions = [
        {
            "key": "flights",
            "label": "Flights in the cascade",
            "value": rollups["flights_affected"],
            "unit": "flights",
            "measured_by": "incident_group_flight",
            "is_complete": True,
            "note": "Declared membership, so this figure does not depend on work completed.",
        },
        {
            "key": "passengers",
            "label": "Passengers on those flights",
            "value": rollups["passengers_affected"],
            "unit": "passengers",
            "measured_by": "booking_segment",
            "is_complete": True,
            "note": "Counted from booking rows against the declared flights.",
        },
        {
            "key": "connections",
            "label": "Connections that break",
            "value": rollups["connections_at_risk"],
            "unit": "connections",
            "measured_by": "connection",
            "is_complete": True,
            "note": "The union of distinct bookings, so nobody is counted twice.",
        },
        {
            "key": "crew_pairings",
            "label": "Crew rotations at risk",
            "value": rollups["crew_pairings_affected"],
            "unit": "rotations",
            "measured_by": "crew_impact",
            "is_complete": True,
            "note": "Direct impacts only. Second-order expansion is reported separately.",
        },
        {
            "key": "candidate_hotels",
            "label": "Hotels within search range",
            "value": rollups["candidate_hotels"],
            "unit": "hotels",
            "measured_by": "hotel",
            "is_complete": True,
            "note": "A search space, not an allocation.",
        },
    ]
    return {
        "basis": "composed_from_recorded_findings",
        "dimensions": dimensions,
        "completeness": {
            "flights_declared": rollups["flights_affected"],
            "flights_assessed": rollups["flights_affected"],
            "ratio": f"{rollups['flights_affected']}/{rollups['flights_affected']}",
            "is_complete": True,
        },
        "gaps": [],
        "note": (
            "Every figure is repeated from the arrays above. Nothing is estimated, scored or "
            "inferred, and there is deliberately no confidence value."
        ),
    }


def build_payload(scenario: CascadeScenario = BENGALURU_STORM) -> dict:
    impacts = attribute_pairing_impacts(
        affected_flights=scenario.affected_flights,
        pairings=list(scenario.pairings),
        flights=scenario.flights_by_id,
    )
    at_risk = [impact for impact in impacts if impact.is_at_risk]

    flights = [
        {
            "id": spec.flight.flight_id,
            "flight_number": spec.flight.flight_number,
            "route": _route(spec.flight.origin_icao, spec.flight.destination_icao),
            "delay_minutes": spec.flight.delay_minutes,
            "passengers": spec.flight.passengers,
            "state": spec.state,
        }
        for spec in scenario.affected
    ]

    crew_pairings = [
        {
            "pairing_reference": impact.pairing_reference,
            "base_icao": impact.base_icao,
            "source_flight": impact.source_flight_number,
            "affected_leg": impact.affected_leg_label,
            "mechanism": impact.mechanism.value,
            "detail": impact.detail,
            "at_risk": impact.is_at_risk,
        }
        for impact in at_risk
    ]

    opened_at = scenario.injected_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rollups = {
        "flights_affected": len(flights),
        "passengers_affected": sum(flight["passengers"] for flight in flights),
        "connections_at_risk": sum(scenario.at_risk_connections_by_flight.values()),
        "candidate_hotels": scenario.candidate_hotel_target,
        "crew_pairings_affected": len(crew_pairings),
        "note": "Each value is the length of the corresponding array, computed server-side.",
    }

    return {
        "generated_by": GENERATED_BY,
        "note": (
            "Counts are DERIVED from the arrays below. The UI must never render a hardcoded total."
        ),
        "id": 1,
        "reference": GROUP_REFERENCE,
        "root_cause": "weather",
        "airport_icao": scenario.root_airport_icao,
        "severity": "high",
        "state": "executing",
        "opened_at": opened_at,
        "rollups": rollups,
        "flights": flights,
        "crew_pairings": crew_pairings,
        # Phase 2, additive. Every key above is unchanged, so the existing console keeps
        # rendering while the graph and blast-radius views are built against these.
        "graph": _graph(scenario, at_risk),
        "blast_radius": _blast_radius(scenario, rollups),
        "mechanism_legend": MECHANISM_LEGEND,
        "why_nine_not_eight": _why_nine_not_eight(impacts),
        "provenance": {
            "kind": "fixture",
            "provider": "fixture",
            "source_ref": f"fixture:{scenario.scenario_key}:cascade",
        },
    }


def render(payload: dict | None = None) -> str:
    return json.dumps(payload or build_payload(), indent=2, ensure_ascii=False) + "\n"


def write(path: Path = FIXTURE_PATH) -> Path:
    path.write_text(render(), encoding="utf-8")
    return path


if __name__ == "__main__":
    written = write()
    print(f"wrote {written.relative_to(REPO_ROOT)}")
