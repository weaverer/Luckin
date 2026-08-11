from lucking.ports.notification_sender import (
    NotificationDisposition,
    NotificationMessage,
    NotificationSender,
)
from tests.contract.memory_notification_sender import MemoryNotificationSender


def test_memory_sender_is_a_replaceable_notification_port() -> None:
    sender = MemoryNotificationSender()
    assert isinstance(sender, NotificationSender)
    result = sender.send(NotificationMessage("每日汇总", "全部成功", "2026-08-08"))
    assert result.disposition is NotificationDisposition.DELIVERED
    assert sender.messages[0].idempotency_key == "2026-08-08"


def test_memory_sender_can_represent_retryable_failure() -> None:
    sender = MemoryNotificationSender(NotificationDisposition.RETRYABLE_FAILURE)
    result = sender.send(NotificationMessage("每日汇总", "存在失败", "2026-08-08"))
    assert result.disposition is NotificationDisposition.RETRYABLE_FAILURE
