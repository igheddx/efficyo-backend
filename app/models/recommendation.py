from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, Text
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
    recommendation_type = Column(String(100), nullable=False)
    recommendation_category = Column(String(20), nullable=False)
    summary = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    risk_level = Column(String(20), nullable=False)
    recommended_action = Column(String(255), nullable=False)
    confidence_score = Column(String(20), nullable=False, default="medium")
    estimated_savings = Column(Numeric(12, 2), nullable=True)
    savings_basis = Column(Text, nullable=True)
    confidence_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    def __repr__(self) -> str:
        return (
            f"<Recommendation(id={self.id}, recommendation_type={self.recommendation_type}, "
            f"resource_id={self.resource_id})>"
        )
