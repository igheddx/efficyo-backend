# Test configuration and fixtures

import os
from typing import Generator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_element, _compiler, **_kwargs):
    return "JSON"

# Use in-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def db() -> Generator[Session, None, None]:
    """Create a fresh database for each test."""
    from app.core.db import Base
    from app.models.organization import Organization  # noqa: F401
    from app.models.platform_setting import PlatformSetting  # noqa: F401
    from app.models.user import AuthSession, User  # noqa: F401
    from app.models.cloud_account import CloudAccount  # noqa: F401
    from app.models.finding import Finding  # noqa: F401
    from app.models.ingestion_job import IngestionJob  # noqa: F401
    from app.models.policy_profile import PolicyProfile  # noqa: F401
    from app.models.recommendation import Recommendation  # noqa: F401
    from app.models.recommendation_outcome import RecommendationOutcome  # noqa: F401
    from app.models.resource_snapshot import ResourceSnapshot  # noqa: F401
    from app.models.tenant import Tenant  # noqa: F401
    from app.models.notification import Notification  # noqa: F401
    from app.models.notification_delivery_log import NotificationDeliveryLog  # noqa: F401
    from app.models.org_integration import OrgIntegration  # noqa: F401
    from app.models.user_notification_destination import UserNotificationDestination  # noqa: F401
    from app.models.approval_request import ApprovalAssignment, ApprovalRequest  # noqa: F401
    from app.models.execution_owner import ExecutionOwnerAssignment  # noqa: F401
    from app.models.access_grant import AccessGrant  # noqa: F401
    from app.models.execution_audit_event import ExecutionAuditEvent  # noqa: F401
    from app.models.execution_policy import ExecutionPolicy  # noqa: F401
    from app.models.account_tag_key import AccountTagKey  # noqa: F401
    from app.models.cost_snapshot import CostApiUsageLog, CostFetchLock, CostSnapshot, CostSyncPolicy  # noqa: F401
    from app.models.tagging_batch import TaggingBatch, TaggingBatchResource  # noqa: F401
    from app.models.notification_policy import NotificationPolicy  # noqa: F401
    from app.models.notification_schedule import NotificationSchedule  # noqa: F401
    from app.models.notification_snooze import NotificationSnooze  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db: Session):
    """Create a test client with database dependency override."""
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import app

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def dev_org_scope(db: Session) -> dict:
    """
    Organization + dev headers for org-scoped API tests (multi-tenant enforcement).

    Use headers on requests to /tenants and /tenants/{id}/cloud-accounts/...
    Attach Tenant.organization_id to the same org in the DB.
    """
    from app.models.organization import Organization

    slug = f"test-scope-{uuid4().hex[:10]}"
    org = Organization(name=f"Scoped {slug}", slug=slug, status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    return {
        "org": org,
        "headers": {
            "X-User": "api-tester",
            "X-Role": "org_admin",
            "X-Current-Organization-Id": str(org.id),
        },
    }


@pytest.fixture
def dev_org_scope_admin(db: Session) -> dict:
    """Organization + real user with membership role `admin` (MSP ops) for permission tests."""
    from app.models.organization import Organization, OrgMembership
    from app.services import auth_service

    slug = f"test-scope-admin-{uuid4().hex[:8]}"
    org = Organization(name=f"Scoped {slug}", slug=slug, status="active")
    db.add(org)
    db.commit()
    db.refresh(org)
    email = f"msp-{slug[:6]}@test.local"
    user = auth_service.create_user(
        db,
        email=email,
        password="testpass12",
        display_name="MSP Admin",
        is_root_admin=False,
    )
    db.add(
        OrgMembership(
            organization_id=org.id,
            user_id=user.id,
            user_identifier=user.email,
            role="admin",
        )
    )
    db.commit()
    return {
        "org": org,
        "user": user,
        "headers": {
            "X-User": email,
            "X-Role": "admin",
            "X-Current-Organization-Id": str(org.id),
        },
    }
