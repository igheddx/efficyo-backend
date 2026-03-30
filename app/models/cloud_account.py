from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class CloudAccount(Base):
    """Tenant-scoped AWS account onboarding record."""

    __tablename__ = "cloud_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "account_id", name="uq_cloud_accounts_tenant_account_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(String(12), nullable=False)
    name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    connection_status = Column(String(32), nullable=False, default="untested")
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    last_validation_error = Column(Text, nullable=True)
    role_arn = Column(String(512), nullable=False)
    region_default = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    tenant = relationship("Tenant", back_populates="cloud_accounts")
    resource_snapshots = relationship("ResourceSnapshot", back_populates="cloud_account", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<CloudAccount(id={self.id}, tenant_id={self.tenant_id}, account_id={self.account_id})>"
