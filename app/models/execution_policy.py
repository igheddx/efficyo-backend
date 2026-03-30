from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class ExecutionPolicy(Base):
    """
    Scoped execution mode for a recommendation type + risk class.
    Specificity: cloud_account > tenant > organization > global (all FKs null).
    """

    __tablename__ = "execution_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=True)

    recommendation_type = Column(String(100), nullable=False)
    risk_class = Column(String(20), nullable=False, default="any")

    execution_mode = Column(String(40), nullable=False)
    requires_all_approvals = Column(Boolean, nullable=False, default=True)
    preflight_required = Column(Boolean, nullable=False, default=False)
    rollback_required = Column(Boolean, nullable=False, default=True)
    is_enabled = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    # Optional: last writer for audit (email); policy_audit events store full detail.
    updated_by_email = Column(String(255), nullable=True)
