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

    return {
        "generated_by": GENERATED_BY,
        "note": (
            "Counts are DERIVED from the arrays below. The UI must never render a hardcoded total."
        ),
        "id": 1,
        "reference": "GRP-2026-0820-VOBL",
        "root_cause": "weather",
        "airport_icao": scenario.root_airport_icao,
        "severity": "high",
        "state": "executing",
        "opened_at": opened_at,
        "rollups": {
            "flights_affected": len(flights),
            "passengers_affected": sum(flight["passengers"] for flight in flights),
            "connections_at_risk": sum(scenario.at_risk_connections_by_flight.values()),
            "candidate_hotels": scenario.candidate_hotel_target,
            "crew_pairings_affected": len(crew_pairings),
            "note": "Each value is the length of the corresponding array, computed server-side.",
        },
        "flights": flights,
        "crew_pairings": crew_pairings,
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
