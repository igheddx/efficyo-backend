"""Slack digest service — builds the 'Top N items needing attention' list for an org.

Phase 1: reads active Recommendations across all tenants/cloud_accounts in the org,
scores them by a simple composite (impact + savings + risk), and returns the top N
as plain dicts ready for slack_service.build_digest_payload().

Architecture note:
    This is intentionally kept separate from the more complex attention_scoring_service
    which is task/copilot focused. The digest service is scheduled/batch oriented —
    it needs a fast DB-only query, no async calls.

    Later phases can plug in failure alerts, pending approvals, and cost spikes here.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cloud_account import CloudAccount
from app.models.org_integration import OrgIntegration
from app.models.recommendation import Recommendation
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

_IMPACT_ORDER = {"high": 3, "medium": 2, "low": 1}
_RISK_ORDER = {"high": 3, "medium": 2, "low": 1}
_IMPACT_LABEL = {"high": "High", "medium": "Medium", "low": "Low"}

_ACTIVE_STATES = {"active", "accepted", "in_progress"}


def _impact_score(rec: Recommendation) -> float:
    """Numeric sort score: savings (primary) + impact/risk tiebreakers."""
    savings = float(rec.estimated_savings or 0)
    impact = _IMPACT_ORDER.get((rec.impact_score or "").lower(), 0)
    risk = _RISK_ORDER.get((rec.risk_level or "").lower(), 0)
    return savings * 10 + impact * 5 + risk * 2


def build_top_n_for_org(
    db: Session,
    organization_id: UUID,
    n: int = 5,
) -> list[dict]:
    """Return the top-N recommendation items across all accounts in an org.

    Returns a list of dicts compatible with slack_service.build_digest_payload().
    """
    # 1. Find all tenants in this org
    tenant_ids = [
        row.id
        for row in db.query(Tenant.id)
        .filter(Tenant.organization_id == organization_id, Tenant.status == "active")
        .all()
    ]
    if not tenant_ids:
        logger.info("digest: no active tenants for org=%s", organization_id)
        return []

    # 2. Find all cloud accounts for those tenants
    account_rows = (
        db.query(CloudAccount.id, CloudAccount.name, CloudAccount.account_id)
        .filter(CloudAccount.tenant_id.in_(tenant_ids), CloudAccount.status != "inactive")
        .all()
    )
    if not account_rows:
        logger.info("digest: no cloud accounts for org=%s", organization_id)
        return []

    account_ids = [r.id for r in account_rows]
    account_name_by_id = {r.id: (r.name or r.account_id or str(r.id)) for r in account_rows}

    # 3. Query active recommendations across those accounts
    recs = (
        db.query(Recommendation)
        .filter(
            Recommendation.cloud_account_id.in_(account_ids),
            Recommendation.state.in_(_ACTIVE_STATES),
        )
        .all()
    )
    if not recs:
        return []

    # 4. Deduplicate by (recommendation_type, cloud_account_id) — count occurrences
    #    and keep the highest-scoring representative record.
    from collections import defaultdict
    groups: dict[tuple, list[Recommendation]] = defaultdict(list)
    for rec in recs:
        key = (rec.recommendation_type, str(rec.cloud_account_id))
        groups[key].append(rec)

    candidates = []
    for (rec_type, acct_id_str), group_recs in groups.items():
        # Pick the representative with the highest individual score
        rep = max(group_recs, key=_impact_score)
        acct_id = group_recs[0].cloud_account_id
        total_savings = sum(float(r.estimated_savings or 0) for r in group_recs)
        candidates.append(
            {
                "_score": _impact_score(rep) + float(total_savings) * 0.5,
                "title": rep.summary or rep.recommendation_type.replace("_", " ").title(),
                "count": len(group_recs),
                "impact": _IMPACT_LABEL.get((rep.impact_score or "").lower()),
                "reason": rep.recommended_action[:120] if rep.recommended_action else None,
                "account_name": account_name_by_id.get(acct_id),
                "estimated_savings": round(total_savings, 2) if total_savings > 0 else None,
                "link": _app_link(settings.frontend_url),
            }
        )

    # 5. Sort descending by composite score, take top N
    candidates.sort(key=lambda x: x["_score"], reverse=True)
    result = [{k: v for k, v in c.items() if k != "_score"} for c in candidates[:n]]
    return result


def _app_link(frontend_url: str | None) -> str | None:
    if not frontend_url:
        return None
    return frontend_url.rstrip("/") + "/dashboard"


# ── OrgIntegration CRUD helpers (shared by both service + API) ─────────────────

def get_integration(
    db: Session,
    organization_id: UUID,
    provider: str = "slack",
) -> OrgIntegration | None:
    return (
        db.query(OrgIntegration)
        .filter(
            OrgIntegration.organization_id == organization_id,
            OrgIntegration.provider == provider,
        )
        .first()
    )


def upsert_integration(
    db: Session,
    organization_id: UUID,
    *,
    provider: str = "slack",
    is_enabled: bool | None = None,
    webhook_url: str | None = ...,  # type: ignore[assignment]
    channel_name: str | None = ...,  # type: ignore[assignment]
    bot_token: str | None = ...,  # type: ignore[assignment]
    chat_id: str | None = ...,  # type: ignore[assignment]
) -> OrgIntegration:
    """Create or update the integration row. Pass `...` (Ellipsis) to leave a field unchanged."""
    row = get_integration(db, organization_id, provider)
    if row is None:
        row = OrgIntegration(
            organization_id=organization_id,
            provider=provider,
            is_enabled=True,
        )
        db.add(row)

    if is_enabled is not None:
        row.is_enabled = is_enabled

    # Only update webhook/channel if explicitly supplied (not Ellipsis)
    if webhook_url is not ...:  # type: ignore[comparison-overlap]
        row.webhook_url = webhook_url
    if channel_name is not ...:  # type: ignore[comparison-overlap]
        row.channel_name = channel_name
    if bot_token is not ...:  # type: ignore[comparison-overlap]
        row.bot_token = bot_token
    if chat_id is not ...:  # type: ignore[comparison-overlap]
        row.chat_id = chat_id

    db.commit()
    db.refresh(row)
    return row
