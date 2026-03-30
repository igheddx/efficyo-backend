from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.db import Base, utc_now


class Finding(Base):
    """Detection output tied to a specific resource snapshot."""

    __tablename__ = "findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False)
    resource_snapshot_id = Column(UUID(as_uuid=True), ForeignKey("resource_snapshots.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(String(255), nullable=False)
    resource_type = Column(String(50), nullable=False)
    finding_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)
    evidence_json = Column(JSONB, nullable=False, default=dict)
    estimated_savings = Column(Numeric(12, 2), nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    # Ingestion job id for full sync, or per-request UUID for standalone detect/* API calls.
    sync_run_id = Column(UUID(as_uuid=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Finding(id={self.id}, finding_type={self.finding_type}, resource_id={self.resource_id})>"
