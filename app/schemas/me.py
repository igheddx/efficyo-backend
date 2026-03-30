from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrganizationSummary(BaseModel):
    id: UUID
    name: str


class TenantSummary(BaseModel):
    id: UUID
    name: str


class CloudAccountSummary(BaseModel):
    id: UUID
    name: str
    account_id: str | None = None


class MembershipRead(BaseModel):
    organization_id: UUID
    organization_name: str
    role: str


class UserContextDefaultsRead(BaseModel):
    """Persisted defaults for login and preferences (must stay within accessible scope)."""

    organization_id: UUID | None = None
    tenant_id: UUID | None = None
    cloud_account_id: UUID | None = None


class MeRead(BaseModel):
    """
    Stable bootstrap contract for the SPA and future OIDC/SAML clients.

    Identity fields describe the authenticated principal; authorization is
    expressed through memberships, current_organization, and current_role.
    """

    id: UUID | None = None
    email: str
    display_name: str
    auth_provider: str = Field(
        default="local",
        description="local | oidc | saml for enterprise; dev_header only for separated test override.",
    )
    external_subject_id: str | None = Field(
        default=None,
        description="IdP subject when auth_provider is not local.",
    )
    memberships: list[MembershipRead]
    accessible_organizations: list[OrganizationSummary] = Field(
        default_factory=list,
        description="Orgs this principal may target (session-backed); mirrors list-orgs visibility.",
    )
    current_organization: OrganizationSummary | None
    current_role: Literal["root_admin", "org_admin", "admin", "approver", "viewer", "member"]
    current_org_role: str = Field(
        default="member",
        description="Organization membership role for administration (org_admin | member | root_admin).",
    )
    effective_access_role: Literal["viewer", "approver", "admin", "none"] | None = Field(
        default=None,
        description="Operational access in the selected tenant/cloud context when query params are provided.",
    )
    accessible_tenants: list[TenantSummary] = Field(default_factory=list)
    accessible_cloud_accounts: list[CloudAccountSummary] = Field(default_factory=list)
    is_root_admin: bool = Field(
        default=False,
        description="User.is_root_admin — platform operator; use for UI that must not depend on current org role.",
    )
    can_manage_platform_orgs: bool = Field(
        default=False,
        description="Same as is_root_admin today — may administer all orgs regardless of current org role.",
    )
    can_manage_current_organization: bool = Field(
        default=False,
        description="True when the principal may add/remove members, tenants, and cloud accounts for current_organization.",
    )
    context_defaults: UserContextDefaultsRead = Field(
        default_factory=UserContextDefaultsRead,
        description="Saved default MSP org / tenant / AWS account for bootstrap and preferences.",
    )


class ContextDefaultsPatch(BaseModel):
    """Omit keys you do not want to change; send null to clear tenant or cloud default."""

    model_config = ConfigDict(extra="forbid")

    default_organization_id: UUID | None = None
    default_tenant_id: UUID | None = None
    default_cloud_account_id: UUID | None = None


class CurrentOrganizationUpdate(BaseModel):
    organization_id: UUID


class AttentionTaskItem(BaseModel):
    """Scored operational attention item (same model as Copilot scored_attention_top rows)."""

    item_kind: str
    entity_type: str
    entity_id: str
    recommendation_id: str | None = None
    title: str
    priority_score: float
    priority_bucket: Literal["critical", "high", "medium", "low"]
    why_action_needed: str
    top_reason: str
    impact_score: float
    urgency_score: float
    risk_score: float
    readiness_score: float
    friction_score: float
    action_type: Literal["execute_now", "approve", "review", "investigate", "fix_failure"]


class AttentionTasksResponse(BaseModel):
    items: list[AttentionTaskItem] = Field(default_factory=list)
