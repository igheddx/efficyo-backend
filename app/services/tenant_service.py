from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.demo_aws import (
    LEGACY_TIPWAVE_DEMO_ROLE_ARN,
    LEGACY_TIPWAVE_OPTIMIZATION_ROLE_ARN,
    TIPWAVE_DEMO_AWS_ACCOUNT_ID,
    TIPWAVE_DEMO_ROLE_ARN,
)
from app.models.cloud_account import CloudAccount
from app.models.organization import Organization
from app.models.policy_profile import PolicyProfile
from app.models.tenant import Tenant

# Stable demo IDs for Tipwave (used by UI defaults and docs).
TIPWAVE_TENANT_ID = UUID("115a2eaf-3e53-462e-8e3d-d49b17ceb5ef")
TIPWAVE_CLOUD_ACCOUNT_ROW_ID = UUID("de30447e-752b-41eb-a595-e21a0a1ba54a")
ARCHIVED_TIPWAVE_LEGACY_TENANT_NAME = "Archived Tipwave (old seed)"


def _migrate_legacy_tipwave_demo_cloud_accounts(db_session: Session) -> None:
    """Migrate old Tipwave demo role ARNs (fake account or OptimizationRole) to FptNextReadOnlyRole."""
    legacy_arns = (LEGACY_TIPWAVE_DEMO_ROLE_ARN, LEGACY_TIPWAVE_OPTIMIZATION_ROLE_ARN)
    rows = (
        db_session.query(CloudAccount)
        .filter(CloudAccount.role_arn.in_(legacy_arns))
        .all()
    )
    for ca in rows:
        ca.role_arn = TIPWAVE_DEMO_ROLE_ARN
        if ca.account_id == "123456789012":
            ca.account_id = TIPWAVE_DEMO_AWS_ACCOUNT_ID
    if rows:
        db_session.commit()


def _rename_legacy_tipwave_tenant_label(db_session: Session) -> None:
    """Avoid two 'Tipwave' entries confusing the tenant dropdown."""
    t = db_session.query(Tenant).filter(Tenant.name == "Tipwave (legacy)").first()
    if t is not None and t.id != TIPWAVE_TENANT_ID:
        t.name = ARCHIVED_TIPWAVE_LEGACY_TENANT_NAME
        db_session.commit()


def _ensure_tipwave_stable_demo(db_session: Session) -> None:
    """
    Idempotent: Tipwave tenant + one cloud account row with fixed UUIDs for local/demo.
    If an older "Tipwave" row exists with a different id, it is renamed so the name can be reused.
    """
    existing_by_id = db_session.query(Tenant).filter(Tenant.id == TIPWAVE_TENANT_ID).first()
    if existing_by_id is None:
        clash = db_session.query(Tenant).filter(Tenant.name == "Tipwave").first()
        if clash is not None:
            clash.name = "Tipwave (legacy)"
            db_session.flush()
        tenant = Tenant(id=TIPWAVE_TENANT_ID, name="Tipwave", status="active")
        db_session.add(tenant)
        db_session.flush()
        db_session.add(
            PolicyProfile(
                tenant_id=tenant.id,
                name="default",
                config_json={},
            )
        )
        db_session.commit()
        db_session.refresh(tenant)
    else:
        if existing_by_id.name != "Tipwave":
            # Another row may already own the name "Tipwave" (e.g. duplicate seed / manual data).
            other_tipwave = (
                db_session.query(Tenant)
                .filter(Tenant.name == "Tipwave", Tenant.id != TIPWAVE_TENANT_ID)
                .first()
            )
            if other_tipwave is not None:
                other_tipwave.name = "Tipwave (legacy)"
                db_session.flush()
            existing_by_id.name = "Tipwave"
            db_session.commit()

    ca = db_session.query(CloudAccount).filter(CloudAccount.id == TIPWAVE_CLOUD_ACCOUNT_ROW_ID).first()
    if ca is None:
        ca = CloudAccount(
            id=TIPWAVE_CLOUD_ACCOUNT_ROW_ID,
            tenant_id=TIPWAVE_TENANT_ID,
            account_id=TIPWAVE_DEMO_AWS_ACCOUNT_ID,
            name="Tipwave AWS",
            status="pending",
            role_arn=TIPWAVE_DEMO_ROLE_ARN,
            region_default="us-east-1",
        )
        db_session.add(ca)
        try:
            db_session.commit()
        except IntegrityError:
            db_session.rollback()
    elif ca.tenant_id != TIPWAVE_TENANT_ID:
        # Row exists but points elsewhere; do not overwrite user data.
        pass
    else:
        # Keep Tipwave seed aligned with demo IAM (migrates old 123456789012 placeholder).
        changed = False
        if ca.account_id != TIPWAVE_DEMO_AWS_ACCOUNT_ID:
            ca.account_id = TIPWAVE_DEMO_AWS_ACCOUNT_ID
            changed = True
        if ca.role_arn != TIPWAVE_DEMO_ROLE_ARN:
            ca.role_arn = TIPWAVE_DEMO_ROLE_ARN
            changed = True
        if changed:
            db_session.commit()


def create_tenant(db_session: Session, name: str, *, organization_id: UUID | None = None) -> Tenant:
    tenant = Tenant(name=name, status="active", organization_id=organization_id)
    db_session.add(tenant)
    db_session.flush()

    default_policy = PolicyProfile(
        tenant_id=tenant.id,
        name="default",
        config_json={},
    )
    db_session.add(default_policy)

    db_session.commit()
    db_session.refresh(tenant)
    return tenant


def list_tenants(db_session: Session, skip: int = 0, limit: int = 100) -> list[Tenant]:
    return db_session.query(Tenant).order_by(Tenant.created_at.asc()).offset(skip).limit(limit).all()


def list_tenants_for_organization(
    db_session: Session,
    organization_id: UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[Tenant]:
    return (
        db_session.query(Tenant)
        .filter(Tenant.organization_id == organization_id)
        .order_by(Tenant.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def refresh_tipwave_demo_cloud_account_metadata(db_session: Session) -> None:
    """
    Migrate legacy Tipwave IAM role ARNs and align the stable Tipwave cloud row.
    Call before sync or other AWS work so rows updated in code are applied even if
    the client never hit GET /tenants?include_demo=true.
    """
    if not settings.enable_demo_and_local_seed:
        return
    _migrate_legacy_tipwave_demo_cloud_accounts(db_session)
    _ensure_tipwave_stable_demo(db_session)


def ensure_demo_tenants(db_session: Session) -> None:
    """
    Idempotent lightweight demo seed.
    Creates sample tenants if they do not exist.
    """
    if not settings.enable_demo_and_local_seed:
        return
    refresh_tipwave_demo_cloud_account_metadata(db_session)
    _rename_legacy_tipwave_tenant_label(db_session)

    demo_names = ["Demo Client A", "Demo Client B"]
    for name in demo_names:
        exists = db_session.query(Tenant.id).filter(Tenant.name == name).first()
        if exists:
            continue
        try:
            create_tenant(db_session, name)
        except IntegrityError:
            db_session.rollback()

    from app.core.constants import DEMO_ORG_NAME
    from app.services.org_service import ensure_demo_org_membership

    ensure_demo_org_membership(db_session)
    _link_demo_tenants_to_demo_org(db_session, DEMO_ORG_NAME)


def _link_demo_tenants_to_demo_org(db_session: Session, org_name: str) -> None:
    """Attach seeded demo tenants to the demo org when organization_id is unset (idempotent)."""
    org = db_session.query(Organization).filter(Organization.name == org_name).first()
    if org is None:
        return
    changed = False
    tip = db_session.query(Tenant).filter(Tenant.id == TIPWAVE_TENANT_ID).first()
    if tip is not None and tip.organization_id is None:
        tip.organization_id = org.id
        changed = True
    for name in ("Demo Client A", "Demo Client B"):
        row = db_session.query(Tenant).filter(Tenant.name == name).first()
        if row is not None and row.organization_id is None:
            row.organization_id = org.id
            changed = True
    if changed:
        db_session.commit()


def get_tenant(db_session: Session, tenant_id: UUID) -> Tenant | None:
    return db_session.query(Tenant).filter(Tenant.id == tenant_id).first()
