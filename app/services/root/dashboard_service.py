from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.cloud_account import CloudAccount
from app.models.organization import Organization
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.user import User


def root_dashboard_summary(db: Session) -> dict:
    total_orgs = db.query(func.count(Organization.id)).scalar() or 0
    active_orgs = (
        db.query(func.count(Organization.id)).filter(Organization.status == "active").scalar() or 0
    )
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.status == "active").scalar() or 0
    total_aws = db.query(func.count(CloudAccount.id)).scalar() or 0
    pending = (
        db.query(func.count(RecommendationOutcome.id))
        .filter(RecommendationOutcome.workflow_status == "suggested")
        .scalar()
        or 0
    )
    return {
        "total_organizations": int(total_orgs),
        "active_organizations": int(active_orgs),
        "total_users": int(total_users),
        "active_users": int(active_users),
        "total_aws_accounts": int(total_aws),
        "pending_approvals": int(pending),
    }
