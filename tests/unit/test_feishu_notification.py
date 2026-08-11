import json

from lucking.integrations.feishu.notification_sender import FeishuNotificationSender
from lucking.ports.notification_sender import NotificationDisposition, NotificationMessage


class Transport:
    def __init__(self, status: int, response: dict[str, object]) -> None:
        self.status = status
        self.response = response
        self.body = b""
        self.url = ""

    def __call__(
        self, url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, object]]:
        del headers, timeout
        self.url = url
        self.body = body
        return self.status, self.response


def test_feishu_signs_and_truncates_without_leaking_secret() -> None:
    transport = Transport(200, {"code": 0})
    sender = FeishuNotificationSender(
        "https://example.test/hook/secret-webhook",
        signing_secret="signing-secret",
        transport=transport,
        timestamp=lambda: 1_786_176_000,
    )
    result = sender.send(NotificationMessage("每日汇总", "数据" * 15_000, "digest"))
    payload = json.loads(transport.body)
    assert payload["timestamp"] == "1786176000"
    assert payload["sign"]
    assert len(transport.body) < 20 * 1024
    assert "secret-webhook" not in repr(result)
    assert "signing-secret" not in repr(result)
    assert result.disposition is NotificationDisposition.DELIVERED


def test_feishu_maps_rate_limit_and_permanent_errors() -> None:
    message = NotificationMessage("每日汇总", "内容", "digest")
    limited = FeishuNotificationSender(
        "https://example.test/hook/value", transport=Transport(429, {})
    ).send(message)
    rejected = FeishuNotificationSender(
        "https://example.test/hook/value", transport=Transport(400, {"code": 19001})
    ).send(message)
    assert limited.disposition is NotificationDisposition.RETRYABLE_FAILURE
    assert rejected.disposition is NotificationDisposition.PERMANENT_FAILURE
