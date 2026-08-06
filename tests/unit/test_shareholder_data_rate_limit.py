"""共享 RateLimiter 400 次/分钟复用验证（T007，research 决策 4）。

复用 007 泛化的 ``RateLimiter``（`src/lucking/integrations/tushare/
rate_limiter.py`）：以 shareholder_data_rate_limit_per_minute=400 配置，
任意 60 秒窗口 ≤ 400 次、最小间隔 ≥ 150 毫秒（60/400）。
"""

from __future__ import annotations

from lucking.integrations.tushare.rate_limiter import RateLimiter


def test_min_interval_150ms_at_400_per_minute() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        sleeps.append(seconds)

    limiter = RateLimiter(rate_per_minute=400, monotonic=monotonic, sleep=sleep)
    limiter.wait_before_call()
    limiter.wait_before_call()
    assert sleeps and sleeps[-1] >= 0.15 - 1e-9  # 60/400 = 150 毫秒


def test_window_cap_400_calls_per_60_seconds() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds
        sleeps.append(seconds)

    limiter = RateLimiter(rate_per_minute=400, monotonic=monotonic, sleep=sleep)
    for _ in range(400):
        limiter.wait_before_call()
    # 第 401 次调用必须等待（首请求在窗口内的记录过期前不可放行）
    limiter.wait_before_call()
    assert sleeps and sleeps[-1] > 0


def test_old_window_records_expire() -> None:
    clock = {"now": 0.0}
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    limiter = RateLimiter(rate_per_minute=400, monotonic=monotonic, sleep=sleep)
    for _ in range(400):
        limiter.wait_before_call()
    clock["now"] += 61.0  # 窗口整体滑出
    limiter.wait_before_call()
    assert not sleeps  # 无等待 → 窗口内 1 次调用直接放行


def test_rejects_invalid_rate() -> None:
    import pytest

    with pytest.raises(ValueError):
        RateLimiter(rate_per_minute=0)
