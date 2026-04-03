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

            ses = boto3.client("ses", region_name=settings.ses_region)
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

    logger.warning("Unknown email provider '%s'. Invitation email not sent.", settings.email_provider)
