"""Typed error contract.

Every error response has the shape defined in docs/26-implementation-contracts.md:

    {"error": {"code", "message", "correlation_id", "details"}}

Owner: Stream A. Other streams raise these; they do not invent new response shapes.
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    """Stable machine-readable codes. Add here rather than inventing strings inline."""

    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    ASSURANCE_BLOCKED = "ASSURANCE_BLOCKED"
    ASSURANCE_CONFIG_MISSING = "ASSURANCE_CONFIG_MISSING"
    PACK_NOT_VERIFIED_ELIGIBLE = "PACK_NOT_VERIFIED_ELIGIBLE"
    POLICY_PACK_UNAVAILABLE = "POLICY_PACK_UNAVAILABLE"
    MISSING_REQUIRED_FACT = "MISSING_REQUIRED_FACT"
    UNKNOWN_ACTION_TYPE = "UNKNOWN_ACTION_TYPE"
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    WORKFLOW_LIMIT_EXCEEDED = "WORKFLOW_LIMIT_EXCEEDED"
    DEMO_ACTION_FORBIDDEN = "DEMO_ACTION_FORBIDDEN"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class TravelOpsError(Exception):
    """Base class. Carries an HTTP status, a stable code, and safe details."""

    status_code: int = 500
    code: str = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailed(TravelOpsError):
    status_code = 422
    code = ErrorCode.VALIDATION_FAILED


class EntityNotFound(TravelOpsError):
    status_code = 404
    code = ErrorCode.ENTITY_NOT_FOUND


class InvalidStateTransition(TravelOpsError):
    status_code = 409
    code = ErrorCode.INVALID_STATE_TRANSITION


class AssuranceBlocked(TravelOpsError):
    """A proposed action was not authorised. This is a normal outcome, not a bug."""

    status_code = 409
    code = ErrorCode.ASSURANCE_BLOCKED


class AssuranceConfigMissing(TravelOpsError):
    status_code = 503
    code = ErrorCode.ASSURANCE_CONFIG_MISSING


class PolicyPackUnavailable(TravelOpsError):
    status_code = 503
    code = ErrorCode.POLICY_PACK_UNAVAILABLE


class PackNotVerifiedEligible(TravelOpsError):
    status_code = 503
    code = ErrorCode.PACK_NOT_VERIFIED_ELIGIBLE


class MissingRequiredFact(TravelOpsError):
    """Required input absent: route to human review rather than guessing."""

    status_code = 409
    code = ErrorCode.MISSING_REQUIRED_FACT


class UnknownActionType(TravelOpsError):
    status_code = 422
    code = ErrorCode.UNKNOWN_ACTION_TYPE


class ProviderUnavailable(TravelOpsError):
    status_code = 503
    code = ErrorCode.PROVIDER_UNAVAILABLE


class WorkflowLimitExceeded(TravelOpsError):
    status_code = 409
    code = ErrorCode.WORKFLOW_LIMIT_EXCEEDED


class DemoActionForbidden(TravelOpsError):
    """Destructive demo helpers are refused outside demo/development."""

    status_code = 403
    code = ErrorCode.DEMO_ACTION_FORBIDDEN


def error_payload(
    code: str, message: str, correlation_id: str | None, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": correlation_id,
            "details": details or {},
        }
    }
