"""The airline's own commercial limits, as gate constraints — STREAM B.

Statutory entitlements come from a policy pack. These are different: a nightly rate cap, an
occupancy assumption, a minimum connection time. They live in Stream C's `business_constraint`
table so an operator can change one with an audit trail.

Until now the gate could not see them. A service read its own cap and refused internally, which
means the refusal surfaced as a service failure rather than as an authorisation decision, and
nothing recorded that a commercial limit had been reached. This module translates a stored row
into the constraint DSL `policy_compliant` already understands, so the limit is checked BEFORE
the action runs and the refusal lands in the assurance record.

Two rules hold it honest:

  * **Values are never restated here.** Every number comes from the row at evaluation time; only
    the mapping from (service, constraint_key) to a payload field lives in
    `config/action_requirements.v1.yaml`. A cap written down twice eventually disagrees with
    itself.
  * **`is_hard` is obeyed, not decided.** A hard row FAILs, a soft row WARNs. Stream C owns that
    distinction because they own the row.

Pure functions. The rows arrive as the caller's own query result — `app.db.scenario_queries.
load_business_constraints` already returns exactly the shape read here — so nothing in this
module touches a database.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.assurance.checks import CONSTRAINT_OPERATORS
from app.config import resolve_repo_path
from app.errors import PolicyPackUnavailable

DEFAULT_MAPPING_PATH: Final = "./config/action_requirements.v1.yaml"


class ConstraintMapping(BaseModel):
    """How one stored business constraint becomes a gate constraint."""

    model_config = ConfigDict(extra="forbid")

    service: str
    constraint_key: str
    field: str
    op: str
    #: Dotted path into the row's `constraint_value` JSON. Absent means use the value whole.
    value_path: str | None = None
    applies_to_actions: list[str] = Field(default_factory=list)
    #: When true a hard row FAILs and a soft row WARNs. When false everything FAILs.
    honour_is_hard: bool = True
    #: When true, a payload that never mentions `field` is a MISSING_REQUIRED_FACT rather than
    #: vacuously compliant. Off by default: demanding a field Stream A does not send would
    #: refuse every action.
    require_field: bool = False
    description: str | None = None

    @property
    def constraint_id(self) -> str:
        return f"business.{self.service}.{self.constraint_key}"


class LoadedMappings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    digest: str
    mappings: list[ConstraintMapping] = Field(default_factory=list)

    def for_action(self, action_type: str) -> list[ConstraintMapping]:
        return [
            mapping
            for mapping in self.mappings
            if not mapping.applies_to_actions or action_type in mapping.applies_to_actions
        ]


def load_mappings(path: str | Path = DEFAULT_MAPPING_PATH) -> LoadedMappings:
    """Load the versioned mapping file.

    Raises PolicyPackUnavailable when the file is absent, unreadable or invalid. A mapping file
    that will not parse must not silently become "no business limits apply": that would turn a
    configuration mistake into a permanently permissive gate.
    """
    resolved = resolve_repo_path(Path(path))

    if not resolved.is_file():
        raise PolicyPackUnavailable(
            f"business constraint mappings not found at {resolved}",
            details={"path": str(resolved), "reason_code": "POLICY_PACK_UNAVAILABLE"},
        )

    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    try:
        parsed = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise PolicyPackUnavailable(
            f"business constraint mappings at {resolved} are not readable YAML",
            details={"path": str(resolved)},
        ) from exc

    if not isinstance(parsed, dict):
        raise PolicyPackUnavailable(
            f"business constraint mappings at {resolved} are not a mapping",
            details={"path": str(resolved)},
        )

    try:
        mappings = [
            ConstraintMapping.model_validate(entry) for entry in parsed.get("mappings") or []
        ]
    except ValidationError as exc:
        raise PolicyPackUnavailable(
            f"business constraint mappings at {resolved} are invalid",
            details={"path": str(resolved), "errors": exc.errors(include_url=False)},
        ) from exc

    unknown = sorted({m.op for m in mappings} - CONSTRAINT_OPERATORS)
    if unknown:
        # An operator the gate cannot evaluate would be skipped, and a skipped limit is a limit
        # that does not exist. Refuse the file instead.
        raise PolicyPackUnavailable(
            f"business constraint mappings at {resolved} use unsupported operator(s): "
            f"{', '.join(unknown)}",
            details={"path": str(resolved), "operators": unknown},
        )

    return LoadedMappings(
        version=str(parsed.get("version") or "unversioned"), digest=digest, mappings=mappings
    )


def _value_from(row_value: Any, value_path: str | None) -> Any:
    if value_path is None:
        return row_value
    current = row_value
    for segment in value_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current[segment]
    return current


def constraints_from_rows(
    *,
    action_type: str,
    rows: list[dict[str, Any]],
    mappings: LoadedMappings,
) -> list[dict[str, Any]]:
    """Translate stored business constraints into gate constraints for one action.

    `rows` is the shape `load_business_constraints` returns: service, constraint_key,
    constraint_value, is_hard, version.

    A mapped row whose value cannot be read is emitted as an unsatisfiable constraint rather
    than dropped. A limit nobody can evaluate must block, not disappear — dropping it would let
    an action through precisely because its governing constraint was malformed.
    """
    by_key = {(row.get("service"), row.get("constraint_key")): row for row in rows}
    constraints: list[dict[str, Any]] = []

    for mapping in mappings.for_action(action_type):
        row = by_key.get((mapping.service, mapping.constraint_key))
        if row is None:
            # Not seeded for this dataset. Absence of a limit is not a violation, and inventing
            # one would be Stream B writing commercial policy.
            continue

        value = _value_from(row.get("constraint_value"), mapping.value_path)
        if value is None:
            constraints.append(
                {
                    "id": mapping.constraint_id,
                    "unsatisfiable": True,
                    "reason": (
                        f"{mapping.constraint_id} is stored but its value could not be read at "
                        f"'{mapping.value_path}'; a limit nobody can evaluate must block"
                    ),
                }
            )
            continue

        is_hard = bool(row.get("is_hard", True))
        constraint: dict[str, Any] = {
            "id": mapping.constraint_id,
            "field": mapping.field,
            "op": mapping.op,
            "value": value,
        }
        if mapping.honour_is_hard and not is_hard:
            constraint["soft"] = True
        constraints.append(constraint)

        if mapping.require_field:
            constraints.append(
                {
                    "id": f"{mapping.constraint_id}.present",
                    "field": mapping.field,
                    "op": "required",
                }
            )

    return constraints


def business_constraint_versions(rows: list[dict[str, Any]]) -> dict[str, str]:
    """service.constraint_key -> version, for the audit record.

    A recorded evaluation should be able to say which version of a commercial limit it was
    decided against, exactly as it records the policy pack version.
    """
    return {
        f"{row.get('service')}.{row.get('constraint_key')}": str(row.get("version") or "unknown")
        for row in rows
    }
