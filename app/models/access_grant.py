"""Tenant / cloud-account scoped access within an MSP organization."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class AccessGrant(Base):
    """
    Operational access to a customer tenant (optionally one AWS account).

    Org membership (`OrgMembership.role`) is org_admin | member and controls org administration.
    This table controls viewer / approver / admin for dashboard, approvals, and execution.

    If cloud_account_id is null, the grant applies to all cloud accounts in the tenant.
    If set, the grant applies only to that account and overrides the tenant-wide grant for that account.
    """

    __tablename__ = "access_grants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    access_role = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="access_grants", foreign_keys=[user_id])
    organization = relationship("Organization", foreign_keys=[organization_id])
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    cloud_account = relationship("CloudAccount", foreign_keys=[cloud_account_id])
