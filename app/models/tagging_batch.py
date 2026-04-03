from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class TaggingBatch(Base):
    __tablename__ = "tagging_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    cloud_account_id = Column(UUID(as_uuid=True), ForeignKey("cloud_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    approval_request_id = Column(UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, unique=True)

    recommendation_type = Column(String(128), nullable=False)
    title = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, default="pending")

    requested_by = Column(String(320), nullable=True)
    requested_by_role = Column(String(32), nullable=True)
    execution_notes = Column(Text, nullable=True)

    required_tag_keys_json = Column(JSONB, nullable=True)
    shared_tags_json = Column(JSONB, nullable=True)
    summary_json = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    resources = relationship(
        "TaggingBatchResource",
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class TaggingBatchResource(Base):
    __tablename__ = "tagging_batch_resources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("tagging_batches.id", ondelete="CASCADE"), nullable=False, index=True)

    recommendation_id = Column(UUID(as_uuid=True), ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_id = Column(String(255), nullable=False)
    resource_type = Column(String(64), nullable=False)

    current_tags_json = Column(JSONB, nullable=True)
    missing_required_tags_json = Column(JSONB, nullable=True)
    proposed_tags_json = Column(JSONB, nullable=False)
    context_json = Column(JSONB, nullable=True)

    execution_status = Column(String(32), nullable=False, default="pending")
    execution_error = Column(Text, nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    batch = relationship("TaggingBatch", back_populates="resources")
