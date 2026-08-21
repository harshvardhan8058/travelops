"""Policy pack persistence.

Statutory policy and internal business constraints are deliberately separate tables.
Applicability is tri-state so a missing fact can never collapse into a false legal
conclusion.

Owner: Stream C (schema) / Stream B (population and evaluation).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ApplicabilityStatus, PolicyPackStatus

JSON_TYPE = JSONB().with_variant(JSON(), "sqlite")


class PolicyPack(Base):
    __tablename__ = "policy_pack"

    id: Mapped[int] = mapped_column(primary_key=True)
    pack_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(8), nullable=False)
    authority: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[str | None] = mapped_column(Text)

    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))

    status: Mapped[PolicyPackStatus] = mapped_column(String(32), nullable=False)
    # False for anything that is not the current primary regulation.
    verified_mode_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Rendered verbatim by the UI badge. Never upgraded by hand.
    ui_label: Mapped[str] = mapped_column(Text, nullable=False)

    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pack_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    rules: Mapped[list[PolicyRule]] = relationship(back_populates="pack")
    documents: Mapped[list[PolicySourceDocument]] = relationship(back_populates="pack")

    __table_args__ = (
        UniqueConstraint("pack_key", "version", name="policy_pack_version_unique"),
        CheckConstraint(
            "status IN ('draft','official_guidance_dated','approved','retired')",
            name="policy_pack_status_valid",
        ),
    )


class PolicySourceDocument(Base):
    __tablename__ = "policy_source_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_pack_id: Mapped[int] = mapped_column(ForeignKey("policy_pack.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_revision: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'PENDING_ARCHIVAL' until the binary is committed and hashed.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    licence_note: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text)

    pack: Mapped[PolicyPack] = relationship(back_populates="documents")
    clauses: Mapped[list[PolicyClause]] = relationship(back_populates="document")


class PolicyClause(Base):
    __tablename__ = "policy_clause"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("policy_source_document.id"), nullable=False
    )
    clause_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    text_content: Mapped[str] = mapped_column("text", Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(32), nullable=False)

    document: Mapped[PolicySourceDocument] = relationship(back_populates="clauses")

    __table_args__ = (
        UniqueConstraint("source_document_id", "clause_ref", name="policy_clause_unique"),
    )


class PolicyRule(Base):
    __tablename__ = "policy_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    policy_pack_id: Mapped[int] = mapped_column(ForeignKey("policy_pack.id"), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(96), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    condition_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    entitlement_json: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    source_clause_refs: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)

    # draft | superseded_suspected | informational | approved
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Suspected-superseded rules must never evaluate.
    excluded_from_evaluation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interpretation: Mapped[str | None] = mapped_column(Text)

    pack: Mapped[PolicyPack] = relationship(back_populates="rules")

    __table_args__ = (UniqueConstraint("policy_pack_id", "rule_key", name="policy_rule_unique"),)


class PolicyApplicability(Base):
    """Tri-state resolver outcome, with the evidence for it."""

    __tablename__ = "policy_applicability"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident.id"), nullable=False, index=True)
    policy_pack_id: Mapped[int] = mapped_column(ForeignKey("policy_pack.id"), nullable=False)

    status: Mapped[ApplicabilityStatus] = mapped_column(String(20), nullable=False)
    basis: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    required_facts: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    missing_facts: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON_TYPE, nullable=False, default=list)
    conflict_disposition: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    resolver_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('applicable','not_applicable','undetermined')",
            name="policy_applicability_status_valid",
        ),
    )


class EntitlementEvaluation(Base):
    """Immutable entitlement result pinned to pack, rule, applicability and inputs."""

    __tablename__ = "entitlement_evaluation"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incident.id"), nullable=False, index=True)
    applicability_id: Mapped[int] = mapped_column(
        ForeignKey("policy_applicability.id"), nullable=False
    )
    policy_pack_id: Mapped[int] = mapped_column(ForeignKey("policy_pack.id"), nullable=False)
    policy_rule_id: Mapped[int] = mapped_column(ForeignKey("policy_rule.id"), nullable=False)

    input_facts: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    result: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessConstraint(Base):
    """Internal commercial limits. Deliberately NOT mixed with statutory policy."""

    __tablename__ = "business_constraint"

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(String(48), nullable=False)
    constraint_key: Mapped[str] = mapped_column(String(48), nullable=False)
    constraint_value: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    is_hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("service", "constraint_key", "version", name="business_constraint_unique"),
    )
