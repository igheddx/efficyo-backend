from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.db import Base, utc_now


class AccountTagKey(Base):
    __tablename__ = "account_tag_keys"
    __table_args__ = (
        UniqueConstraint("cloud_account_id", "key_name", name="uq_account_tag_keys_cloud_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    cloud_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
