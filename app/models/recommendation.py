from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class Recommendation(Base):
    """User-facing recommendation derived from a finding."""

    __tablename__ = "recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False)
    finding_id = Column(UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(String(255), nullable=False)
    resource_type = Column(String(50), nullable=False)
    finding_type = Column(String(120), nullable=False, default="")
    recommendation_type = Column(String(100), nullable=False)
    recommendation_category = Column(String(20), nullable=False)
    summary = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    risk_level = Column(String(20), nullable=False)
    impact_score = Column(String(20), nullable=True)
    effort_score = Column(String(20), nullable=True)
    recommended_action = Column(String(255), nullable=False)
    confidence_score = Column(String(20), nullable=False, default="medium")
    actionability_type = Column(String(32), nullable=True)
    safe_to_apply = Column(Boolean, nullable=True)
    caution_note = Column(Text, nullable=True)
    estimated_savings = Column(Numeric(12, 2), nullable=True)
    savings_basis = Column(Text, nullable=True)
    confidence_reason = Column(Text, nullable=True)
    state = Column(String(24), nullable=False, default="active")
    snoozed_until = Column(DateTime(timezone=True), nullable=True)
    snoozed_by = Column(String(255), nullable=True)
    dismissed_reason = Column(String(64), nullable=True)
    dismissed_reason_note = Column(Text, nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)
    dismissed_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_source = Column(String(24), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    def __repr__(self) -> str:
        return (
            f"<Recommendation(id={self.id}, recommendation_type={self.recommendation_type}, "
            f"resource_id={self.resource_id})>"
        )
