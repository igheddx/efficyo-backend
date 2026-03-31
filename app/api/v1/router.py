from fastapi import APIRouter

from app.api.v1.approval_requests import router as approval_requests_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.auth import router as auth_router
from app.api.v1.auth_oidc import router as auth_oidc_router
from app.api.v1.orgs import router as orgs_router
from app.api.v1.cloud_accounts import router as cloud_accounts_router
from app.api.v1.cost import router as cost_router
from app.api.v1.copilot import router as copilot_router
from app.api.v1.cost import router as cost_router
from app.api.v1.execution_policies import router as execution_policies_router
from app.api.v1.me import router as me_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.outcomes import router as outcomes_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.sync_pipeline import router as sync_router
from app.api.v1.sync_cost import router as sync_cost_router
from app.api.v1.root.router import router as root_router
from app.api.v1.tenants import router as tenants_router

router = APIRouter(prefix="/v1", tags=["v1"])


@router.get("/", summary="API v1 status")
async def v1_status() -> dict[str, str]:
    return {"message": "v1 router ready"}


router.include_router(auth_router)
router.include_router(auth_oidc_router)
router.include_router(orgs_router)
router.include_router(tenants_router)
router.include_router(me_router)
router.include_router(notifications_router)
router.include_router(approval_requests_router)
router.include_router(approvals_router)
router.include_router(cloud_accounts_router)
router.include_router(cost_router)
router.include_router(outcomes_router)
router.include_router(recommendations_router)
router.include_router(copilot_router)
router.include_router(cost_router)
router.include_router(execution_policies_router)
router.include_router(sync_router)
router.include_router(sync_cost_router)
router.include_router(root_router)
