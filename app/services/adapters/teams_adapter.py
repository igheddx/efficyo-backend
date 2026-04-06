"""Microsoft Teams Incoming Webhook adapter.

Uses the legacy MessageCard format for maximum Connector compatibility.
Teams returns HTTP 200 with body "1" on success.
"""

from __future__ import annotations

import logging

import httpx

from app.services.adapters.base import ChannelAdapter, DeliveryResult
from app.services.notification_formatter import FormattedMessage

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class TeamsAdapter(ChannelAdapter):
    provider = "teams"

    # ── Public interface ───────────────────────────────────────────────────────

    def send(self, message: FormattedMessage, integration: object) -> DeliveryResult:
        if not integration.webhook_url:
            return DeliveryResult(success=False, error="No Teams webhook URL configured.")
        payload = self._to_teams_payload(message)
        return self._post(integration.webhook_url, payload)

    def send_test(self, integration: object, org_name: str) -> DeliveryResult:
        if not integration.webhook_url:
            return DeliveryResult(success=False, error="No Teams webhook URL configured.")
        payload = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": "MEEZI — Teams connection test",
            "themeColor": "0078D4",
            "title": "MEEZI — Teams integration connected",
            "text": (
                f"Organization: **{org_name}**  \n"
                "Your Teams channel will receive MEEZI notifications."
            ),
        }
        return self._post(integration.webhook_url, payload)

    def send_direct(
        self,
        message: FormattedMessage,
        integration: object,
        destination: object,
    ) -> DeliveryResult:
        return DeliveryResult(
            success=False,
            error="Teams direct targeting not supported in Phase 3; using org fallback.",
        )

    # ── HTTP delivery ──────────────────────────────────────────────────────────

    def _post(self, webhook_url: str, payload: dict) -> DeliveryResult:
        """POST JSON to a Teams Incoming Webhook. Never logs the URL."""
        try:
            resp = httpx.post(
                webhook_url,
                json=payload,
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers={"Content-Type": "application/json"},
            )
            # Teams connectors return HTTP 200 with body "1" on success
            if resp.status_code in (200, 202):
                return DeliveryResult(success=True, status_code=resp.status_code)
            error_text = resp.text[:200]
            logger.warning(
                "Teams webhook returned non-ok: status=%d body=%s",
                resp.status_code,
                error_text,
            )
            return DeliveryResult(success=False, status_code=resp.status_code, error=error_text)
        except httpx.TimeoutException:
            return DeliveryResult(success=False, error="Request timed out")
        except httpx.RequestError as exc:
            return DeliveryResult(success=False, error=f"Network error: {type(exc).__name__}")

    # ── Formatting ─────────────────────────────────────────────────────────────

    def _to_teams_payload(self, msg: FormattedMessage) -> dict:
        """Convert FormattedMessage to a Teams MessageCard payload."""
        facts: list[dict] = []
        for i, line in enumerate(msg.lines, start=1):
            clean = line.replace("\n", "  ").strip()
            if clean:
                facts.append({"name": str(i), "value": clean})

        sections: list[dict] = []
        if facts:
            sections.append({"facts": facts, "markdown": True})

        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": msg.title,
            "themeColor": "0078D4",
            "title": msg.title,
            "sections": sections,
            "text": msg.footer,
        }
