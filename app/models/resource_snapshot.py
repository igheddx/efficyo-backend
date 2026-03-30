from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class ResourceSnapshot(Base):
    """Read-only snapshot of an AWS resource at a point in time."""

    __tablename__ = "resource_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(String(255), nullable=False)
    resource_type = Column(String(50), nullable=False)
    region = Column(String(64), nullable=False)
    configuration_json = Column(JSONB, nullable=False, default=dict)
    tags_json = Column(JSONB, nullable=False, default=dict)
    captured_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    tenant = relationship("Tenant", back_populates="resource_snapshots")
    cloud_account = relationship("CloudAccount", back_populates="resource_snapshots")

    def __repr__(self) -> str:
        return f"<ResourceSnapshot(id={self.id}, resource_type={self.resource_type}, resource_id={self.resource_id})>"
