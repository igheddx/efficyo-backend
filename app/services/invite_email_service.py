from __future__ import annotations

import logging
from urllib.parse import quote_plus

from app.core.config import settings

logger = logging.getLogger(__name__)


def _login_url_for_email(recipient_email: str) -> str:
    base = (settings.frontend_url or "http://localhost:5173").rstrip("/")
    return f"{base}/login?email={quote_plus(recipient_email)}&invite=temporary_password"


def _app_url() -> str:
    return (settings.frontend_url or "http://localhost:5173").rstrip("/")


def _app_description_line() -> str:
    return (
        "MEEZI helps your team reduce cloud waste, improve governance, and safely execute remediation actions."
    )


def _should_send_to(email: str) -> bool:
    if not settings.email_allowlist:
        return True
    allowed = {x.strip().lower() for x in settings.email_allowlist.split(",") if x.strip()}
    return email.strip().lower() in allowed


def _ses_client():
    import boto3
    ses_kwargs: dict = {"region_name": settings.ses_region}
    if settings.ses_aws_access_key_id and settings.ses_aws_secret_access_key:
        ses_kwargs["aws_access_key_id"] = settings.ses_aws_access_key_id
        ses_kwargs["aws_secret_access_key"] = settings.ses_aws_secret_access_key
    return boto3.client("ses", **ses_kwargs)


def _html_email(*, title: str, preheader: str, body_html: str) -> str:
    """Minimal responsive HTML email wrapper."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;">
<span style="display:none;max-height:0;overflow:hidden;">{preheader}</span>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:32px 0;">
  <tr><td align="center">
    <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden;">
      <tr><td style="background:#111827;padding:24px 32px;">
        <span style="color:#ffffff;font-size:20px;font-weight:bold;letter-spacing:1px;">MEEZI</span>
      </td></tr>
      <tr><td style="padding:32px;">
        {body_html}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:32px 0 24px;">
        <p style="margin:0;font-size:12px;color:#9ca3af;">
          This is an automated message from MEEZI. If you did not expect this email, you can safely ignore it.
        </p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def _cta_button(url: str, label: str) -> str:
    return (
        f'<p style="margin:24px 0;">'
        f'<a href="{url}" style="display:inline-block;background:#2563eb;color:#ffffff;'
        f'text-decoration:none;padding:12px 28px;border-radius:6px;font-size:15px;font-weight:bold;">'
        f'{label}</a></p>'
    )


def send_local_user_invitation_email(
    *,
    recipient_email: str,
    recipient_name: str,
    temporary_password: str,
    expires_in_days: int,
) -> None:
    login_url = _login_url_for_email(recipient_email)
    subject = "Welcome to MEEZI – Your account is ready"
    greeting_name = (recipient_name or "there").strip() or "there"

    body_text = "\n".join(
        [
            f"Hi {greeting_name},",
            "",
            "Your MEEZI account has been created.",
            _app_description_line(),
            "",
            f"Email:             {recipient_email}",
            f"Temporary password: {temporary_password}",
            "",
            f"Sign in at: {_app_url()}",
            "",
            f"This temporary password expires in {expires_in_days} days.",
            "After signing in, you will be asked to set your permanent password.",
            "",
            "If you did not expect this invitation, please contact your administrator.",
        ]
    )

    body_html = f"""
        <p style="margin:0 0 16px;font-size:16px;color:#111827;">Hi {greeting_name},</p>
        <p style="margin:0 0 16px;color:#374151;">Your MEEZI account has been created.</p>
        <p style="margin:0 0 24px;color:#374151;">{_app_description_line()}</p>
        <table cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:16px 20px;margin-bottom:8px;">
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;width:160px;">Email</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;font-weight:bold;">{recipient_email}</td></tr>
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;">Temporary password</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;font-weight:bold;font-family:monospace;">{temporary_password}</td></tr>
        </table>
        <p style="margin:0 0 8px;font-size:13px;color:#9ca3af;">
          This temporary password expires in {expires_in_days} days. You will be asked to set a permanent password after your first sign-in.
        </p>
        {_cta_button(login_url, "Sign in to MEEZI &rarr;")}"""

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
            _ses_client().send_email(
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                        "Html": {"Data": _html_email(title=subject, preheader="Your MEEZI account is ready.", body_html=body_html), "Charset": "UTF-8"},
                    },
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
    badge_color = "#16a34a" if decision == "approved" else "#dc2626"
    greeting_name = (recipient_name or "there").strip() or "there"
    subject = f"Approval {action_label}: {recommendation_summary[:80]}"
    app_url = _app_url()

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
        f"View in MEEZI: {app_url}",
        "",
        "This is an automated notification.",
    ]
    body_text = "\n".join(lines)

    comment_html = (
        f'<p style="margin:16px 0 0;padding:12px 16px;background:#f9fafb;border-left:3px solid #e5e7eb;'
        f'font-size:14px;color:#374151;font-style:italic;">&ldquo;{comment}&rdquo;</p>'
        if comment else ""
    )
    body_html = f"""
        <p style="margin:0 0 16px;font-size:16px;color:#111827;">Hi {greeting_name},</p>
        <p style="margin:0 0 20px;color:#374151;">An approver has <strong>{action_label}</strong> your approval request.</p>
        <table cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:16px 20px;margin-bottom:8px;width:100%;">
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;width:140px;vertical-align:top;">Recommendation</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;">{recommendation_summary}</td></tr>
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;">Decision</td>
              <td style="padding:4px 0;"><span style="display:inline-block;background:{badge_color};color:#fff;font-size:12px;font-weight:bold;padding:2px 10px;border-radius:4px;letter-spacing:.5px;">{action_label.upper()}</span></td></tr>
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;">Approver</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;">{approver_name}</td></tr>
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;">Date / Time</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;">{acted_at_iso}</td></tr>
        </table>
        {comment_html}
        {_cta_button(app_url, "View in MEEZI &rarr;")}"""

    if settings.email_provider == "ses":
        try:
            _ses_client().send_email(
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                        "Html": {"Data": _html_email(title=subject, preheader=f"Your request has been {action_label}.", body_html=body_html), "Charset": "UTF-8"},
                    },
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

    app_url = _app_url()
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
        f"View in MEEZI: {app_url}",
        "",
        "This is an automated notification.",
    ]
    body_text = "\n".join(lines)

    body_html = f"""
        <p style="margin:0 0 16px;font-size:16px;color:#111827;">Hi {greeting_name},</p>
        <p style="margin:0 0 20px;color:#374151;">
          An approval request you were assigned to has been cancelled —
          <strong>{rejecter_name}</strong> rejected it. No action is required from you.
        </p>
        <table cellpadding="0" cellspacing="0" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;padding:16px 20px;margin-bottom:24px;width:100%;">
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;width:140px;vertical-align:top;">Recommendation</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;">{recommendation_summary}</td></tr>
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;">Rejected by</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;">{rejecter_name}</td></tr>
          <tr><td style="padding:4px 0;font-size:14px;color:#6b7280;">Date / Time</td>
              <td style="padding:4px 0;font-size:14px;color:#111827;">{acted_at_iso}</td></tr>
        </table>
        {_cta_button(app_url, "View in MEEZI &rarr;")}"""

    if settings.email_provider == "ses":
        try:
            _ses_client().send_email(
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient_email]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": body_text, "Charset": "UTF-8"},
                        "Html": {"Data": _html_email(title=subject, preheader="An approval request assigned to you has been cancelled.", body_html=body_html), "Charset": "UTF-8"},
                    },
                },
            )
            return
        except Exception:
            logger.exception(
                "Failed to send co-approver cancellation email through SES. email=%s", recipient_email
            )
            return

    logger.warning("Unknown email provider '%s'. Co-approver cancellation email not sent.", settings.email_provider)
