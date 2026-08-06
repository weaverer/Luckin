"""进程级请求节流器：任意滑窗内真实请求数不超过限流（spec FR-005/NFR-004）。

共享模块（research 决策 4）：由 006 的 ``index_rate_limiter.py`` 泛化重命名，
供指数因子、股票技术面因子等所有 Tushare 同步链路复用。设计要点：
限流只负责“不超过每分钟 30 次”，不替代错误重试；`monotonic`/`sleep`
可注入以便测试与时间旅行；线程安全供回补与增量共享。
008 另提供跨进程的 ``RedisRateLimiter``（账户级预算共享，research 决策 4
修订），与 ``RateLimiter`` 实现同一 ``Throttle`` 契约可互换注入。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class Throttle(Protocol):
    """节流器契约：进程级 RateLimiter 与分布式 RedisRateLimiter 共用。"""

    def wait_before_call(self) -> None: ...


class RateLimiter:
    """滑窗节流器：任意 window_seconds 窗口内放行 ≤ rate_per_minute 次调用。

    额外强制最小间隔（默认 window / rate，即 2 秒），保证请求在窗口内均匀
    分布而非瞬时突发。
    """

    def __init__(
        self,
        *,
        rate_per_minute: int = 30,
        window_seconds: float = 60.0,
        min_interval_seconds: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute 必须大于 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须大于 0")
        if min_interval_seconds is None:
            min_interval_seconds = window_seconds / rate_per_minute
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds 必须大于 0")
        self._rate = rate_per_minute
        self._window = window_seconds
        self._min_interval = min_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()
        self._last_call: float | None = None

    def wait_before_call(self) -> None:
        """阻塞直到本次调用满足节流约束（任意窗口内 ≤ rate 次、间隔 ≥ 最小间隔）。"""
        with self._lock:
            now = self._monotonic()
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if self._last_call is not None:
                elapsed = now - self._last_call
                if elapsed < self._min_interval:
                    self._sleep(self._min_interval - elapsed)
            if len(self._timestamps) >= self._rate:
                wait = self._timestamps[0] + self._window - self._monotonic()
                if wait > 0:
                    self._sleep(wait)
            now = self._monotonic()
            self._last_call = now
            self._timestamps.append(now)
