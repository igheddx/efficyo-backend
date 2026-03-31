"""Execution ownership assignment for recommendation accountability."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class ExecutionOwnerAssignment(Base):
    __tablename__ = "execution_owner_assignments"
    __table_args__ = (UniqueConstraint("approval_request_id", name="uq_execution_owner_by_request"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    approval_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_name_snapshot = Column(String(255), nullable=False)
    owner_role_snapshot = Column(String(32), nullable=False)
    assigned_by = Column(String(320), nullable=True)
    assigned_by_role = Column(String(32), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    approval_request = relationship(
        "ApprovalRequest",
        back_populates="execution_owner_assignments",
    )

