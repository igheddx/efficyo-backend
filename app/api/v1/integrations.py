"""Org-level integration management API.

Routes (generic provider — supports slack / teams / telegram):
    GET    /orgs/{org_id}/integrations/{provider}          — get provider config
    PUT    /orgs/{org_id}/integrations/{provider}          — create/update config
    POST   /orgs/{org_id}/integrations/{provider}/test     — send a test message
    POST   /orgs/{org_id}/integrations/{provider}/digest   — send top-N digest now
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.user_context import UserContext, get_user_context
from app.schemas.org_integration import (
    IntegrationActionResult,
    OrgIntegrationRead,
    SlackIntegrationUpsert,
    TeamsIntegrationUpsert,
    TelegramIntegrationUpsert,
)
from app.services import slack_digest_service
from app.services.notification_dispatcher import dispatch_single, dispatch_test
from app.services.notification_event import EventType, NotificationEvent
from app.services.org_service import get_organization

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])

_SUPPORTED_PROVIDERS = frozenset({"slack", "teams", "telegram"})


# -- Helpers ------------------------------------------------------------------

def _require_writable_org(db: Session, ctx: UserContext, org_id: UUID):
    """get_organization raises 404/403 itself when the org is inaccessible."""
    return get_organization(db, org_id, ctx)


def _validate_provider(provider: str) -> None:
    if provider not in _SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{provider}'. Supported: {sorted(_SUPPORTED_PROVIDERS)}",
        )


def _check_configured(row, provider: str) -> None:
    """Raise 422 if the integration is missing required config for sending."""
    if provider in ("slack", "teams") and not row.webhook_url:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Save a {provider.title()} webhook URL before sending.",
        )
    if provider == "telegram" and (not row.bot_token or not row.chat_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Save a Telegram bot token and chat ID before sending.",
        )


def _row_to_read(row) -> OrgIntegrationRead:
    return OrgIntegrationRead(
        id=row.id,
        organization_id=row.organization_id,
        provider=row.provider,
        is_enabled=row.is_enabled,
        webhook_url_masked=row.masked_webhook_url(),
        channel_name=row.channel_name,
        bot_token_masked=row.masked_bot_token(),
        chat_id=row.chat_id,
        last_test_sent_at=row.last_test_sent_at,
        last_digest_sent_at=row.last_digest_sent_at,
        last_delivery_status=row.last_delivery_status,
        last_delivery_error=row.last_delivery_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# -- Generic endpoints --------------------------------------------------------

@router.get(
    "/orgs/{org_id}/integrations/{provider}",
    response_model=OrgIntegrationRead,
    summary="Get integration config for a provider",
)
def get_integration(
    org_id: UUID,
    provider: str,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrgIntegrationRead:
    _validate_provider(provider)
    _require_writable_org(db, ctx, org_id)
    row = slack_digest_service.get_integration(db, org_id, provider)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {provider} integration configured.",
        )
    return _row_to_read(row)


@router.put(
    "/orgs/{org_id}/integrations/{provider}",
    response_model=OrgIntegrationRead,
    summary="Create or update integration config for a provider",
)
def upsert_integration(
    org_id: UUID,
    provider: str,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrgIntegrationRead:
    _validate_provider(provider)
    _require_writable_org(db, ctx, org_id)

    # Validate and extract per-provider fields
    if provider == "slack":
        parsed = SlackIntegrationUpsert(**body)
        row = slack_digest_service.upsert_integration(
            db,
            org_id,
            provider="slack",
            is_enabled=parsed.is_enabled,
            webhook_url=parsed.webhook_url,
            channel_name=parsed.channel_name,
        )
    elif provider == "teams":
        parsed = TeamsIntegrationUpsert(**body)
        row = slack_digest_service.upsert_integration(
            db,
            org_id,
            provider="teams",
            is_enabled=parsed.is_enabled,
            webhook_url=parsed.webhook_url,
            channel_name=parsed.channel_name,
        )
    else:  # telegram
        parsed = TelegramIntegrationUpsert(**body)
        row = slack_digest_service.upsert_integration(
            db,
            org_id,
            provider="telegram",
            is_enabled=parsed.is_enabled,
            bot_token=parsed.bot_token,
            chat_id=parsed.chat_id,
            channel_name=parsed.channel_name,
        )

    return _row_to_read(row)


@router.post(
    "/orgs/{org_id}/integrations/{provider}/test",
    response_model=IntegrationActionResult,
    summary="Send a test message via the specified provider",
)
def test_integration(
    org_id: UUID,
    provider: str,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> IntegrationActionResult:
    _validate_provider(provider)
    org = _require_writable_org(db, ctx, org_id)
    row = slack_digest_service.get_integration(db, org_id, provider)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No {provider} integration configured. Save config first.",
        )
    _check_configured(row, provider)

    result = dispatch_test(db, row, org.name)
    return IntegrationActionResult(
        success=result.success,
        message="Test message sent successfully." if result.success else f"Delivery failed: {result.error}",
        provider=provider,
        last_delivery_status=row.last_delivery_status,
        last_delivery_error=row.last_delivery_error,
    )


@router.post(
    "/orgs/{org_id}/integrations/{provider}/digest",
    response_model=IntegrationActionResult,
    summary="Send the top-N attention digest via the specified provider",
)
def send_digest(
    org_id: UUID,
    provider: str,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> IntegrationActionResult:
    _validate_provider(provider)
    org = _require_writable_org(db, ctx, org_id)
    row = slack_digest_service.get_integration(db, org_id, provider)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No {provider} integration configured. Save config first.",
        )
    if not row.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{provider.title()} integration is disabled for this organization.",
        )
    _check_configured(row, provider)

    items = slack_digest_service.build_top_n_for_org(db, org_id, n=5)
    if not items:
        return IntegrationActionResult(
            success=False,
            message="No active recommendations found for this organization. Nothing to send.",
            provider=provider,
            last_delivery_status=row.last_delivery_status,
        )

    app_url = (settings.frontend_url or "").rstrip("/") or None
    event = NotificationEvent(
        event_type=EventType.top_actions,
        org_id=org_id,
        org_name=org.name,
        payload={"items": items, "app_url": app_url},
    )
    result = dispatch_single(db, event, row)
    return IntegrationActionResult(
        success=result.success,
        message=(
            f"Digest sent with {len(items)} item(s)."
            if result.success
            else f"Delivery failed: {result.error}"
        ),
        provider=provider,
        last_delivery_status=row.last_delivery_status,
        last_delivery_error=row.last_delivery_error,
    )


# -- Backward-compatible Slack Phase 1 routes ---------------------------------

@router.get(
    "/orgs/{org_id}/integrations/slack",
    response_model=OrgIntegrationRead,
    summary="Get Slack integration config for an org",
)
def get_slack_integration_legacy(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrgIntegrationRead:
    return get_integration(org_id=org_id, provider="slack", db=db, ctx=ctx)


@router.put(
    "/orgs/{org_id}/integrations/slack",
    response_model=OrgIntegrationRead,
    summary="Create or update Slack integration config for an org",
)
def upsert_slack_integration_legacy(
    org_id: UUID,
    body: SlackIntegrationUpsert,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> OrgIntegrationRead:
    return upsert_integration(
        org_id=org_id,
        provider="slack",
        body=body.model_dump(),
        db=db,
        ctx=ctx,
    )


@router.post(
    "/orgs/{org_id}/integrations/slack/test",
    response_model=IntegrationActionResult,
    summary="Send a test Slack message",
)
def test_slack_integration_legacy(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> IntegrationActionResult:
    return test_integration(org_id=org_id, provider="slack", db=db, ctx=ctx)


@router.post(
    "/orgs/{org_id}/integrations/slack/digest",
    response_model=IntegrationActionResult,
    summary="Send the top-5 attention digest to Slack now",
)
def send_slack_digest_legacy(
    org_id: UUID,
    db: Session = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
) -> IntegrationActionResult:
    return send_digest(org_id=org_id, provider="slack", db=db, ctx=ctx)
