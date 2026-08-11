"""Feishu custom-bot adapter with signing, size limits and safe error mapping."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lucking.ports.notification_sender import (
    NotificationDisposition,
    NotificationMessage,
    NotificationResult,
)

type Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, dict[str, object]]]


def _urllib_transport(
    url: str, body: bytes, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, object]]:
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(4096)
            parsed = json.loads(raw) if raw else {}
            return response.status, parsed if isinstance(parsed, dict) else {}
    except HTTPError as exc:
        return exc.code, {}


class FeishuNotificationSender:
    provider_code = "feishu"

    def __init__(
        self,
        webhook_url: str,
        *,
        signing_secret: str | None = None,
        transport: Transport = _urllib_transport,
        timestamp: Callable[[], float] = time.time,
        timeout: float = 10,
    ) -> None:
        self._webhook_url = webhook_url
        self._signing_secret = signing_secret
        self._transport = transport
        self._timestamp = timestamp
        self._timeout = timeout

    def send(self, message: NotificationMessage) -> NotificationResult:
        payload = self._payload(message)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        while len(body) >= 20 * 1024:
            text = str(payload["content"]["text"])
            payload["content"]["text"] = text[: max(0, len(text) - 512)] + "\n…内容已截断"
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        try:
            status, response = self._transport(
                self._webhook_url,
                body,
                {"Content-Type": "application/json"},
                self._timeout,
            )
        except (TimeoutError, URLError, OSError):
            return NotificationResult(
                NotificationDisposition.RETRYABLE_FAILURE,
                self.provider_code,
                error_category="NETWORK",
                error_summary="飞书通知网络请求失败",
            )
        code = response.get("code", response.get("StatusCode", 0))
        if 200 <= status < 300 and code in {0, "0", None}:
            return NotificationResult(NotificationDisposition.DELIVERED, self.provider_code, status)
        if status == 429 or status >= 500:
            disposition = NotificationDisposition.RETRYABLE_FAILURE
            category = "RATE_LIMITED" if status == 429 else "UPSTREAM"
        else:
            disposition = NotificationDisposition.PERMANENT_FAILURE
            category = "REJECTED"
        return NotificationResult(
            disposition,
            self.provider_code,
            status,
            category,
            "飞书通知发送失败",
        )

    def _payload(self, message: NotificationMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": f"【{message.title}】\n{message.text}"},
        }
        if self._signing_secret:
            timestamp = str(int(self._timestamp()))
            key = f"{timestamp}\n{self._signing_secret}".encode()
            digest = hmac.new(key, digestmod=hashlib.sha256).digest()
            payload["timestamp"] = timestamp
            payload["sign"] = base64.b64encode(digest).decode()
        return payload
