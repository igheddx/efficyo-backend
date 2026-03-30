from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class PolicyProfile(Base):
    """Tenant-specific policy configuration."""

    __tablename__ = "policy_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_policy_profiles_tenant_name"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    config_json = Column(JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    # Relationships
    tenant = relationship("Tenant", back_populates="policy_profiles")

    def __repr__(self) -> str:
        return f"<PolicyProfile(id={self.id}, tenant_id={self.tenant_id}, name={self.name})>"
