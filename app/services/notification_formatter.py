"""Generic message formatter for MEEZI notification events.

Input:  NotificationEvent
Output: FormattedMessage (provider-agnostic title + lines + footer + metadata)

Provider adapters (Slack / Teams / Telegram) consume FormattedMessage and
convert it to their own wire format independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.notification_event import EventType, NotificationEvent


@dataclass
class FormattedMessage:
    """Provider-agnostic notification message.

    Attributes:
        title:    One-line subject / heading.
        lines:    Body lines rendered in order. May contain sub-lines (\\n).
        footer:   Single trailing line (org name, timestamp, etc.).
        metadata: Extra hints for adapters that support richer formatting
                  (e.g. items list for Slack Block Kit, app_url for links).
    """

    title: str
    lines: list[str]
    footer: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Public entry point ─────────────────────────────────────────────────────────

def format_event(event: NotificationEvent) -> FormattedMessage:
    """Convert a NotificationEvent into a provider-agnostic FormattedMessage.

    Raises ValueError for unknown event types.
    """
    _fmt = {
        EventType.top_actions: _fmt_top_actions,
        EventType.critical_alert: _fmt_critical_alert,
        EventType.approval_pending: _fmt_approval_pending,
        EventType.execution_failed: _fmt_execution_failed,
    }
    fn = _fmt.get(event.event_type)
    if fn is None:
        raise ValueError(f"No formatter registered for event type: {event.event_type!r}")
    return fn(event)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _utc_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _org_footer(org_name: str, extra: str | None = None) -> str:
    parts = [f"Organization: {org_name}", _utc_str()]
    if extra:
        parts.append(extra)
    return " · ".join(parts)


# ── Event formatters ───────────────────────────────────────────────────────────

def _fmt_top_actions(event: NotificationEvent) -> FormattedMessage:
    items = event.payload.get("items", [])
    app_url = event.payload.get("app_url")
    n = len(items)
    title = f"MEEZI — Top {n} items needing attention"

    lines: list[str] = []
    for i, item in enumerate(items, start=1):
        meta: list[str] = []
        if item.get("count") is not None:
            cnt = item["count"]
            meta.append(f"{cnt} resource{'s' if cnt != 1 else ''}")
        if item.get("impact"):
            meta.append(f"Impact: {item['impact']}")
        if item.get("account_name"):
            meta.append(f"Account: {item['account_name']}")

        line = f"{i}. {item['title']}"
        if meta:
            line += f" [{', '.join(meta)}]"
        if item.get("reason"):
            line += f"\n   {item['reason']}"
        if item.get("estimated_savings") is not None:
            line += f"\n   Est. savings: ${item['estimated_savings']:,.0f}/mo"
        lines.append(line)

    return FormattedMessage(
        title=title,
        lines=lines,
        footer=_org_footer(event.org_name),
        metadata={"items": items, "app_url": app_url},
    )


def _fmt_critical_alert(event: NotificationEvent) -> FormattedMessage:
    p = event.payload
    title = f"MEEZI — Critical Alert: {p.get('alert_title', 'Attention required')}"
    lines: list[str] = []
    if p.get("description"):
        lines.append(p["description"])
    if p.get("affected_resource"):
        lines.append(f"Affected resource: {p['affected_resource']}")
    if p.get("account_name"):
        lines.append(f"Account: {p['account_name']}")
    return FormattedMessage(
        title=title,
        lines=lines,
        footer=_org_footer(event.org_name, f"Priority: {event.priority.value.upper()}"),
    )


def _fmt_approval_pending(event: NotificationEvent) -> FormattedMessage:
    p = event.payload
    title = "MEEZI — Approval Required"
    lines: list[str] = []
    if p.get("approval_title"):
        lines.append(p["approval_title"])
    if p.get("requested_by"):
        lines.append(f"Requested by: {p['requested_by']}")
    if p.get("action_count"):
        lines.append(f"Actions: {p['action_count']} pending")
    app_url = p.get("app_url")
    if app_url:
        lines.append(f"Review: {app_url.rstrip('/')}/approvals")
    return FormattedMessage(title=title, lines=lines, footer=_org_footer(event.org_name))


def _fmt_execution_failed(event: NotificationEvent) -> FormattedMessage:
    p = event.payload
    title = "MEEZI — Execution Failed"
    lines: list[str] = []
    if p.get("action_title"):
        lines.append(f"Action: {p['action_title']}")
    if p.get("error_message"):
        lines.append(f"Error: {p['error_message']}")
    if p.get("resource_id"):
        lines.append(f"Resource: {p['resource_id']}")
    return FormattedMessage(title=title, lines=lines, footer=_org_footer(event.org_name))
