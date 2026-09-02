"""Who wrote a proposal, and what that entitles them to — STREAM B, new in Phase 3.

Phase 3 puts a language model in front of the gate. One thing about that changes assurance, and
it is not the obvious one.

The obvious risks are already covered. A model cannot invent an action, because `ActionType` is
a closed enum and `PlanTask` is `extra="forbid"`. It cannot submit a confidence score, for the
same reason. It cannot fabricate an entity, because `entities_valid` resolves every ref. It
cannot fabricate an entitlement, because `policy.cash_matches_engine` compares the payload
figure to the one the engine computed.

The risk that is new: **the payload the gate checks is now written by the model.**
`PlanTask.inputs` is `dict[str, Any]` and flows straight into `GateInputs.payload`. Several gate
constraints are assertions *about* that payload. A model that writes the right key satisfies the
check that was meant to constrain it — `presented_as_current_law: false` reads as compliant, and
`rate_inr: 1` passes a rate cap while the service books something else.

So authorship becomes a fact the gate needs. A field that asserts something about the system's
own determinations may only be set by the system, and a model-authored payload containing one is
**refused, not sanitised**: stripping it silently would hide that a model tried to assert
authority it does not have.

Two things this module deliberately does not do:

  * It does not make a model-authored proposal harder to approve. Provenance never changes a
    tier, a threshold or an approval rule — that would be gating on the wrong thing. It only
    refuses claims the author was not entitled to make. For identical, legitimate inputs a
    model-authored proposal and a deterministic one reach the same decision, and
    `test_authorship.py` asserts that as a property.
  * It never reads `model_self_report`. There is no threshold, no weighting and no config key
    that could introduce one.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.assurance.checks import dedupe
from app.config import resolve_repo_path
from app.errors import PolicyPackUnavailable

DEFAULT_AUTHORITY_PATH: Final = "./config/proposal_authority.v1.yaml"

#: The orchestrator's own token for the deterministic playbook. Stated here rather than imported
#: so the assurance layer keeps no import edge back into the orchestrator; a unit test asserts the
#: two are identical, which is the cheap half of the trade and catches any drift immediately.
FALLBACK_GENERATOR: Final = "fallback-playbook"

CONSTRAINT_SYSTEM_AUTHORED: Final = "authorship.system_authored_field"
CONSTRAINT_UNCORROBORATED_EVIDENCE: Final = "authorship.uncorroborated_evidence"
CONSTRAINT_AUTHORITY_UNAVAILABLE: Final = "authorship.authority_unavailable"


class Authorship(StrEnum):
    """Who wrote the proposal.

    `deterministic` covers the fallback playbook and anything the orchestrator composed itself.
    `model` covers any reasoning agent, in any LLM_MODE — a fixture response is still a model
    response, and treating it as trusted in fixture mode would mean the demo path and the live
    path have different safety properties.
    """

    deterministic = "deterministic"
    model = "model"

    @property
    def is_model(self) -> bool:
        return self is Authorship.model


class ProposalAuthorship(BaseModel):
    """Provenance of one proposal, as assurance needs it.

    A projection of Stream A's `ModelCallAudit`, not a replacement: this carries only what the
    gate is allowed to consider. `model_self_report` is absent by construction — it is not a
    field this layer may see, so there is nothing to be tempted by.
    """

    model_config = ConfigDict(extra="forbid")

    authored_by: Authorship
    #: 'groq:llama-3.3-70b-versatile' | 'fallback-playbook'. Recorded, never branched on beyond
    #: the authored_by distinction.
    generator: str | None = None
    prompt_version: str | None = None

    @classmethod
    def deterministic(cls, generator: str = "fallback-playbook") -> ProposalAuthorship:
        return cls(authored_by=Authorship.deterministic, generator=generator)

    @classmethod
    def from_model(cls, generator: str, prompt_version: str | None = None) -> ProposalAuthorship:
        return cls(authored_by=Authorship.model, generator=generator, prompt_version=prompt_version)


class EvidencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_corroboration: bool = True


class LoadedAuthority(BaseModel):
    """The versioned authorship rules, with the digest they came from."""

    model_config = ConfigDict(extra="forbid")

    version: str
    digest: str
    system_authored_fields: list[str] = Field(default_factory=list)
    system_authored_by_action: dict[str, list[str]] = Field(default_factory=dict)
    evidence: EvidencePolicy = Field(default_factory=EvidencePolicy)

    def system_fields_for(self, action_type: str) -> list[str]:
        return dedupe(
            [*self.system_authored_fields, *(self.system_authored_by_action.get(action_type) or [])]
        )


def load_authority(path: str | Path = DEFAULT_AUTHORITY_PATH) -> LoadedAuthority:
    """Load the versioned authorship rules.

    Raises when the file is absent, unreadable or invalid. An unreadable authority file must not
    mean "a model may author anything": that would turn a configuration mistake into the exact
    permission this module exists to withhold.
    """
    resolved = resolve_repo_path(Path(path))

    if not resolved.is_file():
        raise PolicyPackUnavailable(
            f"proposal authority rules not found at {resolved}",
            details={"path": str(resolved)},
        )

    raw = resolved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    try:
        parsed = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise PolicyPackUnavailable(
            f"proposal authority rules at {resolved} are not readable YAML",
            details={"path": str(resolved)},
        ) from exc

    if not isinstance(parsed, dict):
        raise PolicyPackUnavailable(
            f"proposal authority rules at {resolved} are not a mapping",
            details={"path": str(resolved)},
        )

    try:
        return LoadedAuthority(
            version=str(parsed.get("version") or "unversioned"),
            digest=digest,
            system_authored_fields=list(parsed.get("system_authored_fields") or []),
            system_authored_by_action={
                str(action): list(fields or [])
                for action, fields in (parsed.get("system_authored_by_action") or {}).items()
            },
            evidence=EvidencePolicy.model_validate(parsed.get("evidence") or {}),
        )
    except ValidationError as exc:
        raise PolicyPackUnavailable(
            f"proposal authority rules at {resolved} are invalid",
            details={"path": str(resolved), "errors": exc.errors(include_url=False)},
        ) from exc


def _refuse(constraint_id: str, reason: str) -> dict[str, Any]:
    """A constraint no payload can satisfy, so the refusal travels through the normal gate path."""
    return {"id": constraint_id, "unsatisfiable": True, "reason": reason}


def authorship_constraints(
    *,
    action_type: str,
    payload: dict[str, Any],
    authorship: ProposalAuthorship | None,
    authority: LoadedAuthority | None = None,
    proposed_evidence_refs: list[str] | None = None,
    known_evidence_refs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Constraints that refuse claims this author was not entitled to make.

    Returns `[]` for a deterministic proposal and for a caller that supplied no authorship —
    Phase 1 and Phase 2 behaviour is unchanged, which is what keeps the frozen gate backward
    compatible.

    Feed the result into the same `GateInputs.constraints` list as every other constraint. The
    refusal then arrives as `POLICY_CONSTRAINT_BREACH` on `policy_compliant`, which
    `blocking.py` classifies as a **conflict** — so it is not approvable, which is correct: an
    operator cannot make a fabricated assertion true by agreeing with it.
    """
    if authorship is None or not authorship.authored_by.is_model:
        return []

    if authority is None:
        try:
            authority = load_authority()
        except PolicyPackUnavailable as exc:
            return [_refuse(CONSTRAINT_AUTHORITY_UNAVAILABLE, exc.message)]

    constraints: list[dict[str, Any]] = []

    # ------------------------------------------------- a model asserting a system determination
    reserved = authority.system_fields_for(action_type)
    asserted = [field for field in reserved if field in payload]
    if asserted:
        constraints.append(
            _refuse(
                CONSTRAINT_SYSTEM_AUTHORED,
                f"'{authorship.generator or 'model'}' proposed {len(asserted)} field(s) only the "
                f"system may author: {', '.join(asserted)}. A proposal cannot assert the outcome "
                "of the checks that authorise it.",
            )
        )

    # ------------------------------------------------------------- a model inventing a citation
    if authority.evidence.require_corroboration and known_evidence_refs is not None:
        known = set(known_evidence_refs)
        uncorroborated = dedupe([ref for ref in (proposed_evidence_refs or []) if ref not in known])
        if uncorroborated:
            constraints.append(
                _refuse(
                    CONSTRAINT_UNCORROBORATED_EVIDENCE,
                    f"{len(uncorroborated)} cited reference(s) are not in the recorded evidence "
                    f"set: {', '.join(uncorroborated)}. A reference nobody can trace is not "
                    "evidence, however plausible it looks.",
                )
            )

    return constraints


def authorship_for_generator(generator: str | None) -> Authorship:
    """Who wrote a plan, from its recorded generator token.

    Extracted so exactly one rule answers this question. It previously lived inline in the
    orchestrator, which meant every other surface that needed the answer re-derived it — and the
    console re-derived it *in the browser*, by string-matching `plan.generator` against two
    literals. That classifier returned "unclassified" for the committed fixture's
    `fallback-playbook · deterministic`, so the Recovery Workspace told operators it could not
    tell who wrote a plan that was plainly the deterministic playbook.

    The rule is the conservative one the assurance gate already used: anything that is not the
    fallback playbook is treated as model-authored. A generator this function does not recognise
    is model-authored too, because assuming a stranger is deterministic would hand it the weaker
    gate.
    """
    if generator is None:
        return Authorship.model
    return (
        Authorship.deterministic
        if generator.strip().startswith(FALLBACK_GENERATOR)
        else Authorship.model
    )


def authorship_record(authorship: ProposalAuthorship | None) -> dict[str, Any]:
    """What to persist about authorship on an evaluation.

    Deliberately small, and deliberately without a self-report field. An audit record should be
    able to say a model wrote this and which prompt version produced it; it has no business
    recording a number the gate is forbidden to read.
    """
    if authorship is None:
        return {}
    return {
        "authored_by": authorship.authored_by.value,
        "generator": authorship.generator,
        "prompt_version": authorship.prompt_version,
    }
