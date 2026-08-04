"""IndexRateLimiter 滑窗节流单元测试（时间旅行）。"""

from __future__ import annotations

import threading

from lucking.integrations.tushare.index_rate_limiter import IndexRateLimiter


class FakeClock:
    """可注入的单调时钟与 sleep：sleep 直接推进虚拟时间。"""

    def __init__(self) -> None:
        self._now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sleep 负数")
        self._now += seconds
        self.slept.append(seconds)


def test_min_interval_enforced() -> None:
    clock = FakeClock()
    limiter = IndexRateLimiter(
        rate_per_minute=30, monotonic=clock.monotonic, sleep=clock.sleep
    )
    limiter.wait_before_call()  # 第一次立即放行
    limiter.wait_before_call()  # 第二次必须等待 ≥ 2 秒（60/30）
    assert clock.slept and clock.slept[-1] >= 2.0 - 1e-9


def test_window_cap_at_rate_per_minute() -> None:
    clock = FakeClock()
    limiter = IndexRateLimiter(
        rate_per_minute=30, monotonic=clock.monotonic, sleep=clock.sleep
    )
    timestamps: list[float] = []
    for _ in range(30):
        limiter.wait_before_call()
        timestamps.append(clock.monotonic())
    # 30 次放行后，第 31 次必须等待最早一次滑出 60 秒窗口
    limiter.wait_before_call()
    assert clock.monotonic() >= timestamps[0] + 60.0 - 1e-9


def test_window_count_never_exceeds_rate() -> None:
    clock = FakeClock()
    limiter = IndexRateLimiter(
        rate_per_minute=30, monotonic=clock.monotonic, sleep=clock.sleep
    )
    releases: list[float] = []
    for _ in range(200):
        limiter.wait_before_call()
        releases.append(clock.monotonic())
    for index, release in enumerate(releases):
        window_start = release - 60.0
        in_window = sum(1 for earlier in releases[:index] if earlier > window_start)
        assert in_window <= 30, f"第 {index} 次调用所在 60 秒窗口内已有 {in_window} 次"


def test_thread_safety_with_shared_limiter() -> None:
    clock = FakeClock()
    limiter = IndexRateLimiter(
        rate_per_minute=30, monotonic=clock.monotonic, sleep=clock.sleep
    )
    releases: list[float] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(20):
                limiter.wait_before_call()
                releases.append(clock.monotonic())
        except Exception as exc:  # pragma: no cover - 仅记录异常
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(releases) == 60
    for index, release in enumerate(releases):
        window_start = release - 60.0
        in_window = sum(1 for earlier in releases[:index] if earlier > window_start)
        assert in_window <= 30


def test_defaults_are_thirty_per_minute() -> None:
    clock = FakeClock()
    limiter = IndexRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
    limiter.wait_before_call()
    limiter.wait_before_call()
    # 默认窗口 60 秒、速率 30 → 最小间隔 2 秒
    assert clock.slept and clock.slept[-1] >= 2.0 - 1e-9
