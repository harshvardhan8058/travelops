"""ORM model registry.

Import every model here so Alembic autogenerate and `Base.metadata` see the full schema.

Owner: Stream C. Other streams import models; they do not add tables.
"""

from __future__ import annotations

from app.db.base import Base
from app.models.crew import (
    CrewMember,
    CrewPairingAssignment,
    Pairing,
    PairingImpact,
    PairingLeg,
)
from app.models.policy import (
    BusinessConstraint,
    EntitlementEvaluation,
    PolicyApplicability,
    PolicyClause,
    PolicyPack,
    PolicyRule,
    PolicySourceDocument,
)
from app.models.reference import (
    Airport,
    Booking,
    BookingSegment,
    Flight,
    Hotel,
    Passenger,
    Runway,
    TransportVendor,
    WeatherObservation,
)
from app.models.workflow import (
    Action,
    AssuranceEvaluation,
    DecisionLog,
    HotelReservation,
    HumanDecision,
    Incident,
    IncidentGroup,
    IncidentOutcome,
    Notification,
    Plan,
    PlanTask,
    Prediction,
)

__all__ = [
    "Action",
    "Airport",
    "AssuranceEvaluation",
    "Base",
    "Booking",
    "BookingSegment",
    "BusinessConstraint",
    "CrewMember",
    "CrewPairingAssignment",
    "DecisionLog",
    "EntitlementEvaluation",
    "Flight",
    "Hotel",
    "HotelReservation",
    "HumanDecision",
    "Incident",
    "IncidentGroup",
    "IncidentOutcome",
    "Notification",
    "Pairing",
    "PairingImpact",
    "PairingLeg",
    "Passenger",
    "Plan",
    "PlanTask",
    "PolicyApplicability",
    "PolicyClause",
    "PolicyPack",
    "PolicyRule",
    "PolicySourceDocument",
    "Prediction",
    "Runway",
    "TransportVendor",
    "WeatherObservation",
]
