"""Slack Incoming Webhook adapter."""

from __future__ import annotations

import logging

import httpx

from app.services.adapters.base import ChannelAdapter, DeliveryResult
from app.services.notification_formatter import FormattedMessage

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_MAX_BLOCK_TEXT = 3000


def _truncate(text: str, max_len: int = _MAX_BLOCK_TEXT) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


class SlackAdapter(ChannelAdapter):
    provider = "slack"

    # ── Public interface ───────────────────────────────────────────────────────

    def send(self, message: FormattedMessage, integration: object) -> DeliveryResult:
        if not integration.webhook_url:
            return DeliveryResult(success=False, error="No Slack webhook URL configured.")
        payload = self._to_slack_payload(message)
        return self._post(integration.webhook_url, payload)

    def send_test(self, integration: object, org_name: str) -> DeliveryResult:
        if not integration.webhook_url:
            return DeliveryResult(success=False, error="No Slack webhook URL configured.")
        payload = {
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":white_check_mark: *MEEZI — Slack integration connected*\n"
                            f"Organization: *{org_name}*\n"
                            "Your Slack channel will receive MEEZI digest notifications."
                        ),
                    },
                }
            ]
        }
        return self._post(integration.webhook_url, payload)

    def send_direct(
        self,
        message: FormattedMessage,
        integration: object,
        destination: object,
    ) -> DeliveryResult:
        if not integration.webhook_url:
            return DeliveryResult(success=False, error="No Slack webhook URL configured.")
        if not destination.slack_user_id:
            return DeliveryResult(success=False, error="No Slack user mapping configured.")

        mention = f"<@{destination.slack_user_id}>"
        directed = FormattedMessage(
            title=f"{message.title}",
            lines=[f"{mention}"] + list(message.lines),
            footer=message.footer,
            metadata=message.metadata,
        )
        payload = self._to_slack_payload(directed)
        return self._post(integration.webhook_url, payload)

    # ── HTTP delivery ──────────────────────────────────────────────────────────

    def _post(self, webhook_url: str, payload: dict) -> DeliveryResult:
        """POST JSON to a Slack Incoming Webhook. Never logs the URL."""
        try:
            resp = httpx.post(
                webhook_url,
                json=payload,
                timeout=_TIMEOUT,
                follow_redirects=False,
            )
            if resp.status_code == 200 and resp.text == "ok":
                return DeliveryResult(success=True, status_code=200)
            error_text = resp.text[:200]
            logger.warning(
                "Slack webhook returned non-ok: status=%d body=%s",
                resp.status_code,
                error_text,
            )
            return DeliveryResult(success=False, status_code=resp.status_code, error=error_text)
        except httpx.TimeoutException:
            return DeliveryResult(success=False, error="Request timed out")
        except httpx.RequestError as exc:
            return DeliveryResult(success=False, error=f"Network error: {type(exc).__name__}")

    # ── Formatting ─────────────────────────────────────────────────────────────

    def _to_slack_payload(self, msg: FormattedMessage) -> dict:
        items = msg.metadata.get("items")
        app_url = msg.metadata.get("app_url")
        if items:
            return self._digest_blocks(msg, items, app_url)
        return self._simple_blocks(msg)

    def _simple_blocks(self, msg: FormattedMessage) -> dict:
        body = "\n".join([f"*{msg.title}*"] + msg.lines + [f"_{msg.footer}_"])
        return {
            "text": msg.title,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": _truncate(body)},
                }
            ],
        }

    def _digest_blocks(
        self, msg: FormattedMessage, items: list[dict], app_url: str | None
    ) -> dict:
        item_blocks: list[dict] = []
        for i, item in enumerate(items, start=1):
            parts = [f"*{i}. {item['title']}*"]

            meta: list[str] = []
            if item.get("count") is not None:
                cnt = item["count"]
                meta.append(f"{cnt} resource{'s' if cnt != 1 else ''}")
            if item.get("impact"):
                meta.append(f"Impact: _{item['impact']}_")
            if item.get("account_name"):
                meta.append(f"Account: {item['account_name']}")
            if meta:
                parts.append(" · ".join(meta))

            if item.get("reason"):
                parts.append(f"> {item['reason']}")
            if item.get("estimated_savings") is not None:
                parts.append(f":moneybag: Est. savings: ${item['estimated_savings']:,.0f}/mo")
            if item.get("link"):
                parts.append(f"<{item['link']}|View in MEEZI →>")

            item_blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": _truncate("\n".join(parts))},
                }
            )
            if i < len(items):
                item_blocks.append({"type": "divider"})

        blocks = (
            [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": msg.title,
                        "emoji": True,
                    },
                },
                {"type": "divider"},
            ]
            + item_blocks
            + [
                {"type": "divider"},
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": msg.footer}],
                },
            ]
        )
        return {"blocks": blocks, "text": msg.title}
