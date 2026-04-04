from __future__ import annotations

import logging
from urllib.parse import quote_plus

from app.core.config import settings

logger = logging.getLogger(__name__)


def _login_url_for_email(recipient_email: str) -> str:
    base = (settings.frontend_url or "http://localhost:5173").rstrip("/")
    return f"{base}/login?email={quote_plus(recipient_email)}&invite=temporary_password"


def _app_description_line() -> str:
    return (
        "MEEZI helps your team reduce cloud waste, improve governance, and safely execute remediation actions."
    )


def _should_send_to(email: str) -> bool:
    if not settings.email_allowlist:
        return True
    allowed = {x.strip().lower() for x in settings.email_allowlist.split(",") if x.strip()}
    return email.strip().lower() in allowed


def send_local_user_invitation_email(
    *,
    recipient_email: str,
    recipient_name: str,
    temporary_password: str,
    expires_in_days: int,
) -> None:
    login_url = _login_url_for_email(recipient_email)
    subject = "Welcome to MEEZI - Your temporary password"
    greeting_name = (recipient_name or "there").strip() or "there"

    body_text = "\n".join(
        [
            f"Hi {greeting_name},",
            "",
            "Your MEEZI account has been created.",
            _app_description_line(),
            "",
            f"Sign in URL: {login_url}",
            f"Email: {recipient_email}",
            f"Temporary password: {temporary_password}",
            "",
            f"This temporary password expires in {expires_in_days} days.",
            "After signing in, you will be asked to set your permanent password.",
            "",
            "If you did not expect this invitation, please contact your administrator.",
        ]
    )

    if not settings.email_enabled:
        logger.info(
            "Invitation email disabled. Share manually with user. email=%s login_url=%s temp_password=%s",
            recipient_email,
            login_url,
            temporary_password,
        )
        return

    if not _should_send_to(recipient_email):
        logger.info("Invitation email skipped due to allowlist. email=%s", recipient_email)
        return

    if settings.email_provider == "ses":
        try:
            import boto3

            ses_kwargs: dict = {"region_name": settings.ses_region}
            if settings.ses_aws_access_key_id and settings.ses_aws_secret_access_key:
                ses_kwargs["aws_access_key_id"] = settings.ses_aws_access_key_id
                ses_kwargs["aws_secret_access_key"] = settings.ses_aws_secret_access_key
            ses = boto3.client("ses", **ses_kwargs)
            ses.send_email(
                # SES identity checks can reject display-name formatted Source values
                # in sandbox/strict configurations even when the raw email is verified.
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
                },
            )
            return
        except Exception:
            logger.exception("Failed to send invitation email through SES. email=%s", recipient_email)
            return

def send_approval_decision_email(
    *,
    recipient_email: str,
    recipient_name: str,
    approver_name: str,
    decision: str,
    acted_at_iso: str,
    recommendation_summary: str,
    comment: str | None,
) -> None:
    """Email the submitter when an approver acts on their approval request."""
    if not settings.email_enabled:
        logger.info(
            "Approval decision email disabled. decision=%s approver=%s recipient=%s",
            decision,
            approver_name,
            recipient_email,
        )
        return

    if not _should_send_to(recipient_email):
        logger.info("Approval decision email skipped due to allowlist. email=%s", recipient_email)
        return

    action_label = "approved" if decision == "approved" else "rejected"
    greeting_name = (recipient_name or "there").strip() or "there"
    subject = f"Approval {action_label}: {recommendation_summary[:80]}"

    lines = [
        f"Hi {greeting_name},",
        "",
        f"An approver has {action_label} your approval request.",
        "",
        f"Recommendation: {recommendation_summary}",
        f"Decision:       {action_label.upper()}",
        f"Approver:       {approver_name}",
        f"Date/Time:      {acted_at_iso}",
    ]
    if comment:
        lines += ["", f'Reason/Comment: "{comment}"']
    lines += [
        "",
        "Log in to MEEZI to view the full approval request.",
        "",
        "This is an automated notification. You can turn off these emails in your account settings.",
    ]
    body_text = "\n".join(lines)

    if settings.email_provider == "ses":
        try:
            import boto3

            ses_kwargs: dict = {"region_name": settings.ses_region}
            if settings.ses_aws_access_key_id and settings.ses_aws_secret_access_key:
                ses_kwargs["aws_access_key_id"] = settings.ses_aws_access_key_id
                ses_kwargs["aws_secret_access_key"] = settings.ses_aws_secret_access_key
            ses = boto3.client("ses", **ses_kwargs)
            ses.send_email(
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
                },
            )
            return
        except Exception:
            logger.exception(
                "Failed to send approval decision email through SES. email=%s", recipient_email
            )
            return

    logger.warning("Unknown email provider '%s'. Approval decision email not sent.", settings.email_provider)


def send_co_approver_cancellation_email(
    *,
    recipient_email: str,
    recipient_name: str,
    rejecter_name: str,
    acted_at_iso: str,
    recommendation_summary: str,
) -> None:
    """Email a co-approver when a peer rejects — their pending action is no longer needed."""
    if not settings.email_enabled:
        logger.info(
            "Co-approver cancellation email disabled. rejecter=%s recipient=%s",
            rejecter_name,
            recipient_email,
        )
        return

    if not _should_send_to(recipient_email):
        logger.info("Co-approver cancellation email skipped due to allowlist. email=%s", recipient_email)
        return

    greeting_name = (recipient_name or "there").strip() or "there"
    subject = f"Approval Cancelled: {recommendation_summary[:80]}"

    lines = [
        f"Hi {greeting_name},",
        "",
        "An approval request that you were assigned to has been cancelled.",
        f"{rejecter_name} rejected it on {acted_at_iso}. No action is required from you.",
        "",
        f"Recommendation: {recommendation_summary}",
        f"Rejected by:    {rejecter_name}",
        f"Date/Time:      {acted_at_iso}",
        "",
        "Log in to MEEZI to view the full approval request.",
        "",
        "This is an automated notification. You can turn off these emails in your account settings.",
    ]
    body_text = "\n".join(lines)

    if settings.email_provider == "ses":
        try:
            import boto3

            ses_kwargs: dict = {"region_name": settings.ses_region}
            if settings.ses_aws_access_key_id and settings.ses_aws_secret_access_key:
                ses_kwargs["aws_access_key_id"] = settings.ses_aws_access_key_id
                ses_kwargs["aws_secret_access_key"] = settings.ses_aws_secret_access_key
            ses = boto3.client("ses", **ses_kwargs)
            ses.send_email(
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
                },
            )
            return
        except Exception:
            logger.exception(
                "Failed to send co-approver cancellation email through SES. email=%s", recipient_email
            )
            return

    logger.warning("Unknown email provider '%s'. Co-approver cancellation email not sent.", settings.email_provider)
