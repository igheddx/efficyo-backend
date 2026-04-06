"""Notification channel adapter registry.

Usage:
    from app.services.adapters import ADAPTER_REGISTRY, DeliveryResult
    adapter = ADAPTER_REGISTRY["slack"]
    result  = adapter.send(formatted_msg, integration)
"""

from app.services.adapters.base import ChannelAdapter, DeliveryResult
from app.services.adapters.slack_adapter import SlackAdapter
from app.services.adapters.teams_adapter import TeamsAdapter
from app.services.adapters.telegram_adapter import TelegramAdapter

ADAPTER_REGISTRY: dict[str, ChannelAdapter] = {
    "slack": SlackAdapter(),
    "teams": TeamsAdapter(),
    "telegram": TelegramAdapter(),
}

SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(ADAPTER_REGISTRY.keys())

__all__ = [
    "ChannelAdapter",
    "DeliveryResult",
    "SlackAdapter",
    "TeamsAdapter",
    "TelegramAdapter",
    "ADAPTER_REGISTRY",
    "SUPPORTED_PROVIDERS",
]
