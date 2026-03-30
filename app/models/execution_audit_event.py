from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.db import Base, utc_now


class ExecutionAuditEvent(Base):
    """Governance audit: policy changes and execution attempts (manual vs auto)."""

    __tablename__ = "execution_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    event_type = Column(String(40), nullable=False)
    organization_id = Column(UUID(as_uuid=True), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True)
    cloud_account_id = Column(UUID(as_uuid=True), nullable=True)
    recommendation_id = Column(UUID(as_uuid=True), nullable=True)
    execution_policy_id = Column(
        UUID(as_uuid=True), ForeignKey("execution_policies.id", ondelete="SET NULL"), nullable=True
    )

    actor_user_id = Column(UUID(as_uuid=True), nullable=True)
    actor_email = Column(String(255), nullable=True)
    execution_trigger = Column(String(24), nullable=True)

    allowed = Column(Boolean, nullable=True)
    blocking_reason = Column(Text, nullable=True)
    detail_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
