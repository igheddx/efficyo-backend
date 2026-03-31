"""Multi-approver approval requests for recommendations (MSP workflow)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recommendation_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    submitted_by = Column(String(320), nullable=True)
    submitted_by_role = Column(String(32), nullable=True)
    requested_tag_values_json = Column(JSONB, nullable=True)
    approval_mode = Column(String(32), nullable=False, default="all_required")
    status = Column(String(32), nullable=False, default="submitted", index=True)

    submitted_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    assignments = relationship(
        "ApprovalAssignment",
        back_populates="approval_request",
        cascade="all, delete-orphan",
    )
    execution_owner_assignments = relationship(
        "ExecutionOwnerAssignment",
        cascade="all, delete-orphan",
        back_populates="approval_request",
    )


class ApprovalAssignment(Base):
    __tablename__ = "approval_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    approval_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    approver_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    approver_name_snapshot = Column(String(255), nullable=False)
    approver_role_snapshot = Column(String(32), nullable=False)

    status = Column(String(20), nullable=False, default="pending", index=True)
    acted_at = Column(DateTime(timezone=True), nullable=True)
    comment = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    approval_request = relationship("ApprovalRequest", back_populates="assignments")
