"""Telegram Bot API adapter.

Sends messages via the sendMessage endpoint using plain text (MarkdownV2 not used
to keep formatting simple and avoid escaping edge-cases).

Required OrgIntegration fields:
    bot_token   — Telegram Bot API token (from @BotFather)
    chat_id     — Target chat / channel ID (string representation)
"""

from __future__ import annotations

import logging

import httpx

from app.services.adapters.base import ChannelAdapter, DeliveryResult
from app.services.notification_formatter import FormattedMessage

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_MSG_LEN = 4096  # Telegram hard limit


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_MSG_LEN else text[: _MAX_MSG_LEN - 1] + "…"


class TelegramAdapter(ChannelAdapter):
    provider = "telegram"

    # ── Public interface ───────────────────────────────────────────────────────

    def send(self, message: FormattedMessage, integration: object) -> DeliveryResult:
        if not integration.bot_token or not integration.chat_id:
            return DeliveryResult(
                success=False,
                error="Telegram bot token and chat ID are required.",
            )
        text = self._to_telegram_text(message)
        return self._post(integration.bot_token, integration.chat_id, text)

    def send_test(self, integration: object, org_name: str) -> DeliveryResult:
        if not integration.bot_token or not integration.chat_id:
            return DeliveryResult(
                success=False,
                error="Telegram bot token and chat ID are required.",
            )
        text = (
            f"\u2705 MEEZI \u2014 Telegram integration connected\n"
            f"Organization: {org_name}\n"
            "Your Telegram chat will receive MEEZI notifications."
        )
        return self._post(integration.bot_token, integration.chat_id, text)

    def send_direct(
        self,
        message: FormattedMessage,
        integration: object,
        destination: object,
    ) -> DeliveryResult:
        if not integration.bot_token:
            return DeliveryResult(success=False, error="Telegram bot token is required.")
        if not destination.telegram_chat_id:
            return DeliveryResult(success=False, error="No Telegram chat mapping configured.")
        text = self._to_telegram_text(message)
        return self._post(integration.bot_token, destination.telegram_chat_id, text)

    # ── HTTP delivery ──────────────────────────────────────────────────────────

    def _post(self, bot_token: str, chat_id: str, text: str) -> DeliveryResult:
        """POST to Telegram sendMessage. Never logs the bot token."""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            resp = httpx.post(
                url,
                json={"chat_id": chat_id, "text": text},
                timeout=_TIMEOUT,
                follow_redirects=False,
            )
            ct = resp.headers.get("content-type", "")
            body: dict = resp.json() if "application/json" in ct else {}
            if resp.status_code == 200 and body.get("ok"):
                return DeliveryResult(success=True, status_code=200)
            error_text = str(body.get("description", resp.text[:200]))[:200]
            logger.warning(
                "Telegram API returned non-ok: status=%d desc=%s",
                resp.status_code,
                error_text,
            )
            return DeliveryResult(
                success=False, status_code=resp.status_code, error=error_text
            )
        except httpx.TimeoutException:
            return DeliveryResult(success=False, error="Request timed out")
        except httpx.RequestError as exc:
            return DeliveryResult(success=False, error=f"Network error: {type(exc).__name__}")

    # ── Formatting ─────────────────────────────────────────────────────────────

    def _to_telegram_text(self, msg: FormattedMessage) -> str:
        """Convert FormattedMessage to plain text for Telegram."""
        parts = [msg.title, ""] + msg.lines + ["", msg.footer]
        return _truncate("\n".join(parts))
