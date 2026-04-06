"""Base interface for notification channel adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.notification_formatter import FormattedMessage


@dataclass
class DeliveryResult:
    """Outcome of a single delivery attempt. Never raises — errors are captured here."""

    success: bool
    status_code: int | None = None
    error: str | None = None


class ChannelAdapter(ABC):
    """Abstract base for all notification provider adapters.

    Implementors must:
    - Set the ``provider`` class attribute to the canonical provider string.
    - Implement ``send()`` — deliver a formatted message via this channel.
    - Implement ``send_test()`` — deliver a minimal test/ping message.
    - Never raise — return DeliveryResult(success=False, error=...) on failure.
    """

    provider: str  # must be overridden: "slack" | "teams" | "telegram"

    @abstractmethod
    def send(self, message: FormattedMessage, integration: object) -> DeliveryResult:
        """Deliver a formatted message to this channel.

        Args:
            message:     Provider-agnostic formatted message.
            integration: OrgIntegration model row (typed as object to avoid circular import).

        Returns:
            DeliveryResult — never raises.
        """

    @abstractmethod
    def send_test(self, integration: object, org_name: str) -> DeliveryResult:
        """Send a minimal test/ping message to verify configuration.

        Args:
            integration: OrgIntegration model row.
            org_name:    Human-readable org name for the message body.

        Returns:
            DeliveryResult — never raises.
        """

    @abstractmethod
    def send_direct(
        self,
        message: FormattedMessage,
        integration: object,
        destination: object,
    ) -> DeliveryResult:
        """Deliver a message directly to a mapped user destination.

        Args:
            message: Provider-agnostic formatted message.
            integration: OrgIntegration row for this provider.
            destination: UserNotificationDestination row containing provider identity mapping.

        Returns:
            DeliveryResult — never raises.
        """
