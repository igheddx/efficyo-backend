from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.access_grant import AccessGrantCreate, AccessGrantRead, AccessGrantUpdate
from app.schemas.approvals import PendingApprovalsPageRead
from app.schemas.recommendation_outcome import SavingsProofSummaryRead
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationDetailRead,
    OrganizationRead,
    OrgMembershipCreate,
    OrgMembershipRead,
    OrgMembershipUpdate,
)
from app.services import (
    access_grant_service,
    access_resolution_service,
    approvals_service,
    org_service,
    recommendation_outcome_service,
    tenant_scope_service,
)

router = APIRouter(prefix="/orgs", tags=["organizations"])


def _member_read(m) -> OrgMembershipRead:
    user = getattr(m, "user", None)
    email = user.email if user is not None else m.user_identifier
    display_name = user.display_name if user is not None else None
    user_status = (user.status if user is not None else "active") or "active"
    return OrgMembershipRead(
        id=m.id,
        organization_id=m.organization_id,
        user_id=m.user_id,
        email=email,
        display_name=display_name,
        user_status=user_status,
        role=m.role,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("", response_model=list[OrganizationRead])
def list_orgs(
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[OrganizationRead]:
    rows = org_service.list_organizations(db_session, ctx)
    return [OrganizationRead.model_validate(r) for r in rows]


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create_org(
    body: OrganizationCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrganizationRead:
    org = org_service.create_organization(db_session, body.name, ctx)
    return OrganizationRead.model_validate(org)


@router.get("/{org_id}", response_model=OrganizationDetailRead)
def get_org(
    org_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrganizationDetailRead:
    org = org_service.get_organization(db_session, org_id, ctx)
    count = org_service.organization_member_count(db_session, org_id)
    return OrganizationDetailRead.model_validate(
        {**OrganizationRead.model_validate(org).model_dump(), "member_count": count}
    )


@router.get("/{org_id}/savings-proof/summary", response_model=SavingsProofSummaryRead)
def org_savings_proof_summary_endpoint(
    org_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> SavingsProofSummaryRead:
    tenant_scope_service.assert_organization_accessible(db_session, ctx, org_id)
    result = recommendation_outcome_service.savings_proof_summary_for_organization(db_session, org_id)
    return SavingsProofSummaryRead(**result)


@router.get("/{org_id}/approvals/pending", response_model=PendingApprovalsPageRead)
def list_pending_approvals_for_org(
    org_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_previews: bool = Query(False),
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> PendingApprovalsPageRead:
    if not access_resolution_service.user_may_list_org_approval_requests(db_session, ctx, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient access to view pending approvals for this organization.",
        )
    tenant_scope_service.assert_organization_accessible(db_session, ctx, org_id)
    return approvals_service.list_pending_approvals_for_organization(
        db_session,
        org_id,
        limit=limit,
        offset=offset,
        include_previews=include_previews,
    )


@router.get("/{org_id}/users", response_model=list[OrgMembershipRead])
def list_org_users(
    org_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[OrgMembershipRead]:
    members = org_service.list_members(db_session, org_id, ctx)
    return [_member_read(m) for m in members]


@router.post("/{org_id}/users", response_model=OrgMembershipRead, status_code=status.HTTP_201_CREATED)
def add_org_user(
    org_id: UUID,
    body: OrgMembershipCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrgMembershipRead:
    login = (body.email or body.user_id or "").strip().lower()
    m = org_service.add_member(
        db_session,
        org_id,
        login,
        body.role,
        ctx,
        password=body.password,
        display_name=body.display_name,
    )
    return _member_read(m)


@router.patch("/{org_id}/users/{user_id}", response_model=OrgMembershipRead)
def patch_org_user(
    org_id: UUID,
    user_id: str,
    body: OrgMembershipUpdate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrgMembershipRead:
    m = org_service.update_member(db_session, org_id, user_id, body.role, ctx)
    return _member_read(m)


@router.delete("/{org_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org_user(
    org_id: UUID,
    user_id: str,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> None:
    org_service.remove_member(db_session, org_id, user_id, ctx)


@router.get("/{org_id}/grants", response_model=list[AccessGrantRead])
def list_access_grants(
    org_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> list[AccessGrantRead]:
    org_service.get_organization(db_session, org_id, ctx)
    rows = access_grant_service.list_grants_for_org(db_session, org_id)
    return [AccessGrantRead.model_validate(r) for r in rows]


@router.post("/{org_id}/grants", response_model=AccessGrantRead, status_code=status.HTTP_201_CREATED)
def create_access_grant(
    org_id: UUID,
    body: AccessGrantCreate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> AccessGrantRead:
    org_service.get_organization(db_session, org_id, ctx)
    row = access_grant_service.create_grant(
        db_session,
        organization_id=org_id,
        user_id=body.user_id,
        tenant_id=body.tenant_id,
        cloud_account_id=body.cloud_account_id,
        access_role=body.access_role,
    )
    return AccessGrantRead.model_validate(row)


@router.patch("/{org_id}/grants/{grant_id}", response_model=AccessGrantRead)
def patch_access_grant(
    org_id: UUID,
    grant_id: UUID,
    body: AccessGrantUpdate,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> AccessGrantRead:
    org_service.get_organization(db_session, org_id, ctx)
    row = access_grant_service.update_grant(
        db_session,
        organization_id=org_id,
        grant_id=grant_id,
        access_role=body.access_role,
    )
    return AccessGrantRead.model_validate(row)


@router.delete("/{org_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_access_grant(
    org_id: UUID,
    grant_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> None:
    org_service.get_organization(db_session, org_id, ctx)
    access_grant_service.delete_grant(db_session, organization_id=org_id, grant_id=grant_id)
