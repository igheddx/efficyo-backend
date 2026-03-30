from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class RecommendationOutcome(Base):
    """Savings proof snapshot for a recommendation action by the user."""

    __tablename__ = "recommendation_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    cloud_account_id = Column(
        UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False
    )
    recommendation_id = Column(
        UUID(as_uuid=True), ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    resource_id = Column(String(255), nullable=False)
    recommendation_type = Column(String(100), nullable=False)
    recommendation_category = Column(String(20), nullable=False)

    status = Column(String(20), nullable=False, default="pending")
    acted_on_at = Column(DateTime(timezone=True), nullable=True)

    baseline_monthly_cost = Column(Numeric(12, 2), nullable=True)
    current_monthly_cost = Column(Numeric(12, 2), nullable=True)
    estimated_savings_at_action = Column(Numeric(12, 2), nullable=True)
    realized_savings = Column(Numeric(12, 2), nullable=True)

    # Proof-of-savings: same rolling 30d UnblendedCost account total as summary/cost-summary (cost_explorer_service).
    before_cost = Column(Numeric(14, 2), nullable=True)
    after_cost = Column(Numeric(14, 2), nullable=True)
    estimated_savings = Column(Numeric(14, 2), nullable=True)
    savings_verified_at = Column(DateTime(timezone=True), nullable=True)

    impact_status = Column(String(20), nullable=True)
    impact_summary = Column(Text, nullable=True)
    follow_up_recommendation = Column(Text, nullable=True)
    last_evaluated_at = Column(DateTime(timezone=True), nullable=True)

    workflow_status = Column(String(20), nullable=False, default="suggested")
    approved_by = Column(String(255), nullable=True)
    approved_role = Column(String(20), nullable=True)
    approved_membership_role = Column(String(32), nullable=True)
    approved_access_role = Column(String(20), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_comment = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejected_by = Column(String(255), nullable=True)
    applied_by = Column(String(255), nullable=True)
    applied_role = Column(String(20), nullable=True)
    applied_membership_role = Column(String(32), nullable=True)
    applied_access_role = Column(String(20), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    execution_notes = Column(Text, nullable=True)

    # Last successful preflight ("ready") — used when execution policy requires preflight.
    preflight_passed_at = Column(DateTime(timezone=True), nullable=True)
    applied_via_auto = Column(Boolean, nullable=False, default=False)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return (
            f"<RecommendationOutcome(id={self.id}, recommendation_id={self.recommendation_id}, "
            f"status={self.status})>"
        )

