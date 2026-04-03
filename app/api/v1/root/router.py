from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.root.deps import require_platform_root
from app.core.db import get_db
from app.core.user_context import UserContext
from app.schemas.organization import OrgMembershipRead
from app.schemas.root.common import Paginated
from app.schemas.root.dashboard import RootDashboardSummary
from app.schemas.root.ops import RootAlertThresholds, RootAlertThresholdsPatch
from app.schemas.root.resource_coverage import RootResourceCoverageSummary
from app.schemas.root.orgs import (
    RootOrganizationCreate,
    RootOrganizationDetail,
    RootOrganizationListItem,
    RootOrganizationStatusUpdate,
)
from app.schemas.root.users import (
    RootGlobalUserRow,
    RootUserCreate,
    RootUserDetail,
    RootUserStatusUpdate,
)
from app.services import org_service
from app.services.root import (
    dashboard_service,
    ops_service,
    orgs_service,
    resource_coverage_service,
    users_service,
)

router = APIRouter(prefix="/root", tags=["root-admin"])


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


@router.get("/dashboard", response_model=RootDashboardSummary)
def root_dashboard(
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootDashboardSummary:
    return RootDashboardSummary.model_validate(dashboard_service.root_dashboard_summary(db_session))


@router.get("/resource-coverage", response_model=RootResourceCoverageSummary)
def root_resource_coverage(
    _ctx: UserContext = Depends(require_platform_root),
) -> RootResourceCoverageSummary:
    return resource_coverage_service.build_resource_coverage_summary()


@router.get("/alerts/thresholds", response_model=RootAlertThresholds)
def root_get_alert_thresholds(
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootAlertThresholds:
    thresholds, row = ops_service.get_alert_thresholds(db_session)
    return RootAlertThresholds(
        **thresholds,
        updated_at=getattr(row, "updated_at", None),
        updated_by_email=getattr(row, "updated_by_email", None),
    )


@router.patch("/alerts/thresholds", response_model=RootAlertThresholds)
def root_patch_alert_thresholds(
    body: RootAlertThresholdsPatch,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(require_platform_root),
) -> RootAlertThresholds:
    try:
        thresholds, row = ops_service.patch_alert_thresholds(
            db_session,
            patch=body.model_dump(exclude_unset=True),
            updated_by_email=ctx.email,
        )
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return RootAlertThresholds(
        **thresholds,
        updated_at=row.updated_at,
        updated_by_email=row.updated_by_email,
    )


@router.get("/organizations", response_model=Paginated[RootOrganizationListItem])
def root_list_organizations(
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: str | None = Query(None, description="active or disabled"),
) -> Paginated[RootOrganizationListItem]:
    st = status.strip().lower() if status else None
    if st is not None and st not in {"active", "disabled"}:
        st = None
    items, total = orgs_service.list_root_organizations(
        db_session,
        page=page,
        page_size=page_size,
        search=search,
        status_filter=st,
    )
    return Paginated(
        items=[RootOrganizationListItem.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/organizations",
    response_model=RootOrganizationDetail,
    status_code=status.HTTP_201_CREATED,
)
def root_create_organization(
    body: RootOrganizationCreate,
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootOrganizationDetail:
    org = orgs_service.create_root_organization(
        db_session,
        name=body.name,
        slug=body.slug,
    )
    return RootOrganizationDetail.model_validate(orgs_service.get_root_organization(db_session, org.id))


@router.get("/organizations/{org_id}", response_model=RootOrganizationDetail)
def root_get_organization(
    org_id: UUID,
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootOrganizationDetail:
    return RootOrganizationDetail.model_validate(orgs_service.get_root_organization(db_session, org_id))


@router.patch("/organizations/{org_id}", response_model=RootOrganizationDetail)
def root_patch_organization(
    org_id: UUID,
    body: RootOrganizationStatusUpdate,
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootOrganizationDetail:
    row = orgs_service.patch_root_organization_status(
        db_session, org_id, new_status=body.status
    )
    return RootOrganizationDetail.model_validate(row)


@router.get("/organizations/{org_id}/users", response_model=list[OrgMembershipRead])
def root_list_org_users(
    org_id: UUID,
    db_session: Session = Depends(get_db),
    ctx: UserContext = Depends(require_platform_root),
) -> list[OrgMembershipRead]:
    members = org_service.list_members(db_session, org_id, ctx)
    return [_member_read(m) for m in members]


@router.get("/users", response_model=Paginated[RootGlobalUserRow])
def root_list_users(
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    org_id: UUID | None = Query(None),
    role: str | None = Query(None),
    user_status: str | None = Query(None, description="active, pending, or disabled"),
) -> Paginated[RootGlobalUserRow]:
    st = user_status.strip().lower() if user_status else None
    if st is not None and st not in {"active", "pending", "disabled"}:
        st = None
    items, total = users_service.list_root_global_users(
        db_session,
        page=page,
        page_size=page_size,
        search=search,
        org_id=org_id,
        role=role,
        user_status=st,
    )
    return Paginated(
        items=[RootGlobalUserRow.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/users", response_model=RootGlobalUserRow, status_code=status.HTTP_201_CREATED)
def root_create_user(
    body: RootUserCreate,
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootGlobalUserRow:
    row = users_service.root_create_org_user(
        db_session,
        organization_id=body.organization_id,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        role=body.role,
    )
    return RootGlobalUserRow.model_validate(row)


@router.get("/users/{user_id}", response_model=RootUserDetail)
def root_get_user(
    user_id: UUID,
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootUserDetail:
    return RootUserDetail.model_validate(users_service.get_root_user_detail(db_session, user_id))


@router.patch("/users/{user_id}/status", response_model=RootUserDetail)
def root_patch_user_status(
    user_id: UUID,
    body: RootUserStatusUpdate,
    db_session: Session = Depends(get_db),
    _ctx: UserContext = Depends(require_platform_root),
) -> RootUserDetail:
    return RootUserDetail.model_validate(
        users_service.patch_root_user_status(db_session, user_id, new_status=body.status)
    )
