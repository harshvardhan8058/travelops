"""Policy pack loader — STREAM B.

Enforces the status ladder. This is where POLICY_MODE is honoured:

    demo     loads a fictional fixture. No citation, no real figure.
    charter  loads `official_guidance_dated`. Real cited figures, dated badge.
    verified loads ONLY `approved` packs whose verified_mode_eligible is true.

The charter pack MUST be rejected in verified mode with PACK_NOT_VERIFIED_ELIGIBLE. Test
case `verified_mode_rejects_this_pack` exists for exactly this.

Everything here fails closed. A pack that cannot be read, cannot be trusted or cannot be
matched to the running mode is not loaded at all, because the alternative — loading it and
labelling it optimistically — is how a dated figure gets presented as current law.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import PolicyMode
from app.errors import PackNotVerifiedEligible, PolicyPackUnavailable
from app.models.enums import PolicyPackStatus

#: Rule-level review states. `approved` is deliberately absent from what a non-approved pack
#: may contain: a rule cannot out-rank the pack that carries it.
RULE_STATUS_APPROVED: Final = "approved"

#: Which pack statuses each mode will accept.
#:
#: `charter` accepts an approved pack as well as a dated one. An approved pack is better
#: sourced than the mode requires, and the only consequence is a more cautious badge.
#: `draft` and `retired` compute nothing in any mode.
_ALLOWED_STATUSES: Final[dict[PolicyMode, frozenset[PolicyPackStatus]]] = {
    PolicyMode.demo: frozenset({PolicyPackStatus.draft, PolicyPackStatus.official_guidance_dated}),
    PolicyMode.charter: frozenset(
        {PolicyPackStatus.official_guidance_dated, PolicyPackStatus.approved}
    ),
    PolicyMode.verified: frozenset({PolicyPackStatus.approved}),
}

#: Files whose contents determine what the pack MEANS, and therefore its identity.
#:
#: `test_cases.yaml` and `source-metadata.yaml` are deliberately excluded. A reviewer adding a
#: test case or recording a retrieval date does not change any entitlement, and it must not
#: invalidate the pack hash that a past evaluation was pinned to — otherwise a replay would
#: look like it referenced a different pack when the rules were identical.
_HASHED_FILES: Final[tuple[str, ...]] = (
    "pack.yaml",
    "applicability.yaml",
    "rules.yaml",
    "review.yaml",
)


class PackRule(BaseModel):
    """One rule as authored in rules.yaml.

    `extra="allow"` because entitlement shapes differ by rule family — a cap, a percentage,
    a per-kg rate, a passenger choice. The engine reads those from `entitlement`; the loader
    only validates the fields every rule must have.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    status: str
    scope: str = "all"
    source_clause_refs: list[str] = Field(default_factory=list)
    interpretation: str | None = None

    when: dict[str, Any] | None = None
    entitlement: dict[str, Any] | None = None
    effect: dict[str, Any] | None = None

    requires_facts: list[str] = Field(default_factory=list)
    on_missing_required_fact: str | None = None

    #: A suspected-superseded rule NEVER evaluates. The engine surfaces a notice instead.
    excluded_from_evaluation: bool = False
    supersession_note: str | None = None

    procedural_obligation: str | None = None
    out_of_mvp_scope: bool = False

    @property
    def is_computational(self) -> bool:
        """True when the rule can produce an entitlement or apply an effect."""
        return self.entitlement is not None or self.effect is not None


class LoadedPack(BaseModel):
    """An immutable, hash-pinned view of a policy pack, valid for one POLICY_MODE."""

    model_config = ConfigDict(extra="forbid")

    pack_id: str
    version: str
    jurisdiction: str
    authority: str
    document: str | None = None
    document_date: str | None = None
    currency: str | None = None

    status: PolicyPackStatus
    verified_mode_eligible: bool
    ui_label: str
    demo_fixture: bool = False

    #: Source-document integrity, read from source-metadata.yaml. The archived primary document
    #: and its hash are the legal source; extracted text is not. Recorded on every load so a
    #: dated pack can report the truth about its source without being refused for it.
    source_archived: bool = False
    source_content_sha256: str | None = None
    source_document_verified: bool = False
    source_integrity_reason: str | None = None

    #: Overlap handling. False means an overlap resolves to needs_human rather than a guess.
    conflict_rules_defined: bool = False
    on_conflict: str = "needs_human"

    required_context: list[str] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=list)
    conditionally_required_facts: dict[str, list[str]] = Field(default_factory=dict)
    applies_when: dict[str, Any] = Field(default_factory=dict)
    on_missing_required_fact: str = "undetermined"
    on_undetermined: str = "needs_human"

    rules: list[PackRule] = Field(default_factory=list)

    pack_hash: str
    source: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    loaded_mode: PolicyMode
    directory: str

    @property
    def evaluable_rules(self) -> list[PackRule]:
        """Rules the engine may evaluate. Excluded rules are never in this list."""
        return [
            rule
            for rule in self.rules
            if not rule.excluded_from_evaluation
            and not rule.out_of_mvp_scope
            and rule.is_computational
        ]

    @property
    def excluded_rules(self) -> list[PackRule]:
        return [rule for rule in self.rules if rule.excluded_from_evaluation]

    @property
    def may_be_called_current_law(self) -> bool:
        """Only an approved, verified-eligible pack with a verified source may be current law.

        Source integrity is part of this and not a separate courtesy check. A pack whose primary
        document is unarchived, missing or hash-mismatched has nothing behind its figures, and
        "approved" recorded in review.yaml is a statement about a document nobody can produce.
        """
        return (
            self.status is PolicyPackStatus.approved
            and self.verified_mode_eligible
            and self.source_document_verified
        )

    @property
    def citations_permitted(self) -> bool:
        """A fictional fixture has nothing to cite."""
        return not self.demo_fixture

    def rule(self, rule_id: str) -> PackRule | None:
        return next((rule for rule in self.rules if rule.id == rule_id), None)


def _read_yaml(path: Path, *, pack_ref: str) -> dict[str, Any]:
    if not path.is_file():
        raise PolicyPackUnavailable(
            f"{pack_ref} is missing {path.name}; no authoritative result can be produced",
            details={"path": str(path), "reason_code": "POLICY_PACK_UNAVAILABLE"},
        )
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise PolicyPackUnavailable(
            f"{pack_ref} has unreadable {path.name}",
            details={"path": str(path), "reason_code": "POLICY_PACK_UNAVAILABLE"},
        ) from exc

    if not isinstance(parsed, dict):
        raise PolicyPackUnavailable(
            f"{pack_ref} has a malformed {path.name}: expected a mapping",
            details={"path": str(path), "reason_code": "POLICY_PACK_UNAVAILABLE"},
        )
    return parsed


def compute_pack_hash(directory: Path) -> str:
    """Hash the semantic files of a pack, so any rule edit changes the pack identity.

    File names are included, so a missing file is a different pack rather than a silently
    shorter hash. Truncated to sixteen characters to match the config hash convention and the
    `pack_hash` the UI renders.
    """
    digest = hashlib.sha256()
    for name in _HASHED_FILES:
        path = directory / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"")
        digest.update(b"\0")
    return digest.hexdigest()[:16]


#: Recorded when a pack's primary document has not been archived and hashed yet. Treated as an
#: absent hash, never as a valid one.
PENDING_ARCHIVAL: Final = "PENDING_ARCHIVAL"

#: Reason code for a pack whose source document cannot be shown to be the one that was reviewed.
#: Carried in `details` rather than added to `app/errors.py`, which Stream A owns.
REASON_SOURCE_DOCUMENT_UNVERIFIED: Final = "SOURCE_DOCUMENT_UNVERIFIED"

_SHA256_HEX_LENGTH: Final = 64


def _hash_file(path: Path) -> str:
    """SHA-256 of a file, read in chunks so a large PDF does not load into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_source_document(*, directory: Path, source: dict[str, Any]) -> tuple[bool, str | None]:
    """Check the recorded hash against the archived file. Returns `(verified, reason)`.

    Pure of policy: it answers whether the document on disk is the one the metadata claims, and
    nothing about whether that entitles the pack to anything. `load_pack` decides what to do with
    the answer, which differs by mode.

    The existing `content_sha256` field and the existing `local_path` are used as they are. No new
    hash format, no second algorithm: the recorded value must be lowercase SHA-256 hex, which is
    what `PENDING_ARCHIVAL` is a placeholder for.

    Every failure is a refusal, never a pass with a warning:

      * `archived: false` — nothing has been archived, so there is nothing to verify
      * `content_sha256` absent, null or PENDING_ARCHIVAL — a missing value is not a valid one
      * a recorded hash that is not SHA-256 hex — unverifiable by construction
      * `local_path` absent or missing on disk — the hash refers to a document nobody holds
      * digest mismatch — the file is not the document that was reviewed
    """
    if not bool(source.get("archived", False)):
        return False, "source document is not archived (`archived: false`)"

    recorded = source.get("content_sha256")
    if recorded is None or str(recorded).strip() == "":
        return False, "`content_sha256` is absent, and a missing hash is not a verified one"
    recorded = str(recorded).strip()

    if recorded == PENDING_ARCHIVAL:
        return False, f"`content_sha256` is still {PENDING_ARCHIVAL}"

    normalised = recorded.lower()
    if len(normalised) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in normalised
    ):
        return False, f"`content_sha256` '{recorded}' is not SHA-256 hex"

    local_path = source.get("local_path")
    if not local_path:
        return False, "`local_path` is absent, so the recorded hash refers to no document"

    document = directory / str(local_path)
    if not document.is_file():
        return False, f"archived document '{local_path}' is not present at {document}"

    actual = _hash_file(document)
    if actual != normalised:
        return (
            False,
            f"archived document '{local_path}' hashes to {actual}, and the pack records "
            f"{normalised}; this is not the document that was reviewed",
        )

    return True, None


def _reject_for_source_integrity(
    *,
    pack_ref: str,
    status: PolicyPackStatus,
    mode: PolicyMode,
    verified: bool,
    reason: str | None,
) -> None:
    """Refuse a load that would present unverifiable figures as reviewed law.

    Applied where the architecture already expects source integrity to matter: verified mode, and
    any pack claiming `approved`. A dated pack in charter mode records the truth about its source
    and continues, which is the whole point of the status ladder — the charter's figures are
    citable and clearly labelled, and its PDF being unarchived is exactly why it is not verified.
    """
    if verified:
        return
    if mode is not PolicyMode.verified and status is not PolicyPackStatus.approved:
        return

    raise PackNotVerifiedEligible(
        f"{pack_ref} cannot be loaded in {mode.value} mode: {reason}",
        details={
            "reason_code": REASON_SOURCE_DOCUMENT_UNVERIFIED,
            "status": status.value,
            "mode": mode.value,
            "detail": reason,
        },
    )


def _reject_for_mode(
    *, pack_ref: str, status: PolicyPackStatus, verified_eligible: bool, mode: PolicyMode
) -> None:
    """Apply the status ladder. Raises rather than returning a degraded pack."""
    if mode is PolicyMode.verified:
        # Both conditions produce the same code, because the caller's remedy is identical:
        # supply the current primary regulation and an SME sign-off.
        if status is not PolicyPackStatus.approved:
            raise PackNotVerifiedEligible(
                f"{pack_ref} has status '{status.value}', and verified mode requires 'approved'",
                details={
                    "reason_code": "PACK_NOT_VERIFIED_ELIGIBLE",
                    "status": status.value,
                    "required_status": PolicyPackStatus.approved.value,
                },
            )
        if not verified_eligible:
            raise PackNotVerifiedEligible(
                f"{pack_ref} is not marked verified_mode_eligible",
                details={"reason_code": "PACK_NOT_VERIFIED_ELIGIBLE", "status": status.value},
            )
        return

    allowed = _ALLOWED_STATUSES[mode]
    if status not in allowed:
        raise PolicyPackUnavailable(
            f"{pack_ref} has status '{status.value}', which POLICY_MODE={mode.value} does not "
            f"accept (expected one of {sorted(s.value for s in allowed)})",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE", "status": status.value},
        )


def _validate_rules(
    *, pack_ref: str, rules: list[PackRule], status: PolicyPackStatus, review: dict[str, Any]
) -> None:
    """Guards that keep an unreviewed pack from behaving like a reviewed one."""
    for rule in rules:
        # A rule cannot out-rank the pack that carries it. Marking one rule `approved` inside
        # a dated pack would let a single edit smuggle a figure past the review gate.
        if rule.status == RULE_STATUS_APPROVED and status is not PolicyPackStatus.approved:
            raise PolicyPackUnavailable(
                f"{pack_ref} rule '{rule.id}' is marked approved inside a pack with status "
                f"'{status.value}'; a rule cannot out-rank its pack",
                details={"reason_code": "POLICY_PACK_UNAVAILABLE", "rule": rule.id},
            )

        # A suspected-superseded rule must be excluded, not merely annotated.
        if rule.status == "superseded_suspected" and not rule.excluded_from_evaluation:
            raise PolicyPackUnavailable(
                f"{pack_ref} rule '{rule.id}' is superseded_suspected but not excluded from "
                "evaluation",
                details={"reason_code": "POLICY_PACK_UNAVAILABLE", "rule": rule.id},
            )

    if status is not PolicyPackStatus.approved:
        return

    # An approved pack is the only thing that may be called current law, so its evidence has
    # to be complete. These checks are why POLICY_MODE=verified is unreachable today.
    if not review.get("approval") or not review.get("reviewer_name"):
        raise PolicyPackUnavailable(
            f"{pack_ref} is marked approved without a recorded reviewer and approval in "
            "review.yaml",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE"},
        )

    uncited = [rule.id for rule in rules if rule.is_computational and not rule.source_clause_refs]
    if uncited:
        raise PolicyPackUnavailable(
            f"{pack_ref} is approved but {len(uncited)} rule(s) lack source_clause_refs: "
            f"{', '.join(uncited)}",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE", "rules": uncited},
        )


def load_pack(*, pack_dir: Path, pack_id: str, version: str, mode: PolicyMode) -> LoadedPack:
    """Load, validate and return a policy pack.

    `pack_dir` is the packs root, e.g. `./policy_packs`; the pack is read from
    `pack_dir / pack_id / version`.

    Rejects when:
      * the pack directory or pack.yaml is missing            -> POLICY_PACK_UNAVAILABLE
      * mode is verified and status != approved               -> PACK_NOT_VERIFIED_ELIGIBLE
      * mode is verified and verified_mode_eligible is false  -> PACK_NOT_VERIFIED_ELIGIBLE
      * mode is demo and the pack is not a fictional fixture  -> POLICY_PACK_UNAVAILABLE
      * a rule lacks source_clause_refs while status=approved -> POLICY_PACK_UNAVAILABLE
      * a rule claims approved inside a non-approved pack     -> POLICY_PACK_UNAVAILABLE
      * a superseded_suspected rule is not excluded           -> POLICY_PACK_UNAVAILABLE

    Returns the pack with its hash, so every entitlement can be pinned to the exact rule
    text it was computed from.
    """
    directory = Path(pack_dir) / pack_id / version
    pack_ref = f"policy pack {pack_id}@{version}"

    if not directory.is_dir():
        raise PolicyPackUnavailable(
            f"{pack_ref} not found at {directory}",
            details={"path": str(directory), "reason_code": "POLICY_PACK_UNAVAILABLE"},
        )

    manifest = _read_yaml(directory / "pack.yaml", pack_ref=pack_ref)
    applicability = _read_yaml(directory / "applicability.yaml", pack_ref=pack_ref)
    rules_document = _read_yaml(directory / "rules.yaml", pack_ref=pack_ref)
    review = _read_yaml(directory / "review.yaml", pack_ref=pack_ref)
    source = _read_yaml(directory / "source-metadata.yaml", pack_ref=pack_ref)

    declared_id = str(manifest.get("id", ""))
    declared_version = str(manifest.get("version", ""))
    if declared_id != pack_id or declared_version != version:
        # A pack that disagrees with its own path cannot be pinned by id and version.
        raise PolicyPackUnavailable(
            f"{pack_ref} declares id '{declared_id}' version '{declared_version}', which does "
            "not match its directory",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE"},
        )

    try:
        status = PolicyPackStatus(str(manifest.get("status", "")))
    except ValueError as exc:
        raise PolicyPackUnavailable(
            f"{pack_ref} has an unrecognised status '{manifest.get('status')}'",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE"},
        ) from exc

    verified_eligible = bool(manifest.get("verified_mode_eligible", False))
    demo_fixture = bool(manifest.get("demo_fixture", False))

    _reject_for_mode(
        pack_ref=pack_ref, status=status, verified_eligible=verified_eligible, mode=mode
    )

    if mode is PolicyMode.demo and not demo_fixture:
        # Demo mode exists to prove the engine without citing anything. Loading a real
        # authority's pack here would put genuine figures behind a fictional label.
        raise PolicyPackUnavailable(
            f"{pack_ref} is not a fictional fixture, and POLICY_MODE=demo loads only packs "
            "marked demo_fixture: true",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE", "status": status.value},
        )

    try:
        rules = [PackRule.model_validate(entry) for entry in rules_document.get("rules") or []]
    except ValidationError as exc:
        raise PolicyPackUnavailable(
            f"{pack_ref} has a malformed rule",
            details={
                "reason_code": "POLICY_PACK_UNAVAILABLE",
                "errors": exc.errors(include_url=False),
            },
        ) from exc

    if not rules:
        raise PolicyPackUnavailable(
            f"{pack_ref} contains no rules",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE"},
        )

    duplicates = sorted({rule.id for rule in rules if [r.id for r in rules].count(rule.id) > 1})
    if duplicates:
        raise PolicyPackUnavailable(
            f"{pack_ref} has duplicate rule ids: {', '.join(duplicates)}",
            details={"reason_code": "POLICY_PACK_UNAVAILABLE", "rules": duplicates},
        )

    _validate_rules(pack_ref=pack_ref, rules=rules, status=status, review=review)

    source_verified, source_reason = verify_source_document(directory=directory, source=source)
    _reject_for_source_integrity(
        pack_ref=pack_ref,
        status=status,
        mode=mode,
        verified=source_verified,
        reason=source_reason,
    )

    precedence = manifest.get("precedence") or {}
    document_date = manifest.get("document_date")

    return LoadedPack(
        pack_id=pack_id,
        version=version,
        jurisdiction=str(manifest.get("jurisdiction", "")),
        authority=str(manifest.get("authority", "")),
        document=manifest.get("document"),
        document_date=str(document_date) if document_date is not None else None,
        currency=manifest.get("currency"),
        status=status,
        verified_mode_eligible=verified_eligible,
        ui_label=str(manifest.get("ui_label", "")),
        demo_fixture=demo_fixture,
        source_archived=bool(source.get("archived", False)),
        source_content_sha256=(
            str(source["content_sha256"]) if source.get("content_sha256") is not None else None
        ),
        source_document_verified=source_verified,
        source_integrity_reason=source_reason,
        conflict_rules_defined=bool(precedence.get("conflict_rules_defined", False)),
        on_conflict=str(precedence.get("on_conflict", "needs_human")),
        required_context=list(manifest.get("required_context") or []),
        required_facts=list(applicability.get("required_facts") or []),
        conditionally_required_facts={
            str(family): list(facts or [])
            for family, facts in (applicability.get("conditionally_required_facts") or {}).items()
        },
        applies_when=applicability.get("applies_when") or {},
        on_missing_required_fact=str(applicability.get("on_missing_required_fact", "undetermined")),
        on_undetermined=str(applicability.get("on_undetermined", "needs_human")),
        rules=rules,
        pack_hash=compute_pack_hash(directory),
        source=source,
        review=review,
        loaded_mode=mode,
        directory=str(directory),
    )


def load_test_cases(*, pack_dir: Path, pack_id: str, version: str) -> list[dict[str, Any]]:
    """Read the pack's executable expectations.

    Separate from `load_pack` because the cases are the pack's specification, not part of the
    data the engine evaluates against.
    """
    directory = Path(pack_dir) / pack_id / version
    document = _read_yaml(
        directory / "test_cases.yaml", pack_ref=f"policy pack {pack_id}@{version}"
    )
    return list(document.get("cases") or [])
