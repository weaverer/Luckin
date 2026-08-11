from lucking.ports.notification_sender import (
    NotificationDisposition,
    NotificationMessage,
    NotificationResult,
)


class MemoryNotificationSender:
    provider_code = "memory"

    def __init__(
        self,
        disposition: NotificationDisposition = NotificationDisposition.DELIVERED,
    ) -> None:
        self.disposition = disposition
        self.messages: list[NotificationMessage] = []

    def send(self, message: NotificationMessage) -> NotificationResult:
        self.messages.append(message)
        return NotificationResult(self.disposition, self.provider_code)
