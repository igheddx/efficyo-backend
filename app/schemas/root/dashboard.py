from pydantic import BaseModel, Field


class RootDashboardSummary(BaseModel):
    total_organizations: int = Field(..., ge=0)
    active_organizations: int = Field(..., ge=0)
    total_users: int = Field(..., ge=0)
    active_users: int = Field(..., ge=0)
    total_aws_accounts: int = Field(..., ge=0)
    pending_approvals: int = Field(..., ge=0)
