from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base, utc_now


class User(Base):
    """
    Authenticated identity (local password today; OIDC/SAML later).

    Org-scoped authorization is never stored here except the platform flag
    `is_root_admin` (break-glass / operator). Effective UI/API roles come from
    `OrgMembership` for the current organization. For SSO-only users, `password_hash`
    may become nullable in a future migration.
    """

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(String(320), nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=True)
    display_name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active")
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    is_root_admin = Column(Boolean, nullable=False, default=False)
    auth_provider = Column(String(64), nullable=False, default="local")
    external_subject_id = Column(String(512), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    default_organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    default_tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    default_cloud_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")
    memberships = relationship("OrgMembership", back_populates="user")
    access_grants = relationship("AccessGrant", back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    """Opaque server-side session; cookie holds raw token, DB stores SHA-256 hash."""

    __tablename__ = "auth_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    current_organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    user = relationship("User", back_populates="sessions")
