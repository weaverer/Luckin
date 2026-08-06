"""RedisRateLimiter 分布式节流器测试（T007 补充，research 决策 4 修订）。

验证账户级共享语义（400/min 是三个接口合计预算，跨进程共享）：
- 两个限流器实例（模拟两个 flow run 进程）合计 ≤ rate/窗口；
- 最小间隔 ≥ 60/rate；
- 窗口过期后放行；
- Redis 不可达时降级为进程级限流（fail-open，不抛异常）。

依赖本地 compose Redis（lucking-redis）；Redis 不可达时跳过分布式用例。
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from redis import Redis
from redis.exceptions import RedisError

from lucking.config import Settings
from lucking.integrations.tushare.redis_rate_limiter import RedisRateLimiter


def _redis() -> Redis | None:
    settings = Settings()
    try:
        client = Redis.from_url(
            settings.redis_url,
            password=(
                settings.redis_password.get_secret_value()
                if settings.redis_password is not None
                else None
            ),
            socket_connect_timeout=1,
        )
        client.ping()
        return client
    except RedisError:
        return None


@pytest.fixture()
def redis_client() -> Redis:
    client = _redis()
    if client is None:
        pytest.skip("Redis 不可达（本地 compose Redis 未运行）")
    yield client
    prefix = f"lucking:shareholder:rate:{_PREFIX}"
    for key in (f"{prefix}:window", f"{prefix}:last", f"{prefix}:seq"):
        client.delete(key)


_PREFIX = uuid4().hex[:8]


def _limiter(
    redis: Redis,
    *,
    rate: int = 400,
    window_seconds: float = 60.0,
    key_prefix: str | None = None,
) -> RedisRateLimiter:
    return RedisRateLimiter(
        redis,
        rate_per_minute=rate,
        window_seconds=window_seconds,
        key_prefix=key_prefix or f"lucking:shareholder:rate:{_PREFIX}",
    )


def test_two_instances_share_account_budget(redis_client: Redis) -> None:
    """账户级共享：两个实例（模拟两个进程）合计 ≤ rate/窗口。"""
    prefix = f"lucking:shareholder:rate:{_PREFIX}"
    limiter_a = _limiter(redis_client, rate=5, window_seconds=1.0, key_prefix=prefix)
    limiter_b = _limiter(redis_client, rate=5, window_seconds=1.0, key_prefix=prefix)
    for _ in range(3):
        limiter_a.wait_before_call()  # A 进程 3 次（合计 3）
    for _ in range(2):
        limiter_b.wait_before_call()  # B 进程 2 次（合计 5 = 上限）
    # 第 6 次（跨进程合计）必须等待窗口最旧记录过期（窗口 1s / rate 5）
    started = time.monotonic()
    limiter_b.wait_before_call()
    waited = time.monotonic() - started
    assert waited >= 0.1  # 预算耗尽必须等待（最小间隔 0.2s 起步，含窗口等待）
    redis_client.delete(f"{prefix}:window", f"{prefix}:last", f"{prefix}:seq")


def test_min_interval_between_calls(redis_client: Redis) -> None:
    """最小间隔 ≥ 60/rate（rate=400 → 150 毫秒）。"""
    limiter = _limiter(redis_client, rate=400)
    limiter.wait_before_call()
    started = time.monotonic()
    limiter.wait_before_call()
    assert time.monotonic() - started >= 0.15 - 0.02  # 150ms 间隔（留误差）


def test_window_expiry_allows_next_call(redis_client: Redis) -> None:
    """窗口滑出后放行：短窗口（1s/rate=2）第 3 次调用等待后放行。"""
    prefix = f"lucking:shareholder:rate:{_PREFIX}"
    limiter = _limiter(redis_client, rate=2, window_seconds=1.0, key_prefix=prefix)
    limiter.wait_before_call()
    limiter.wait_before_call()
    started = time.monotonic()
    limiter.wait_before_call()  # 第 3 次：等待最旧记录过期（~0.5s）
    waited = time.monotonic() - started
    assert 0.1 < waited <= 1.5  # 窗口 1s 内放行
    redis_client.delete(f"{prefix}:window", f"{prefix}:last", f"{prefix}:seq")


def test_redis_unreachable_degrades_to_process_limiter() -> None:
    """Redis 不可达 → 降级为进程级限流（fail-open，不抛异常）。"""
    events: list[str] = []
    limiter = RedisRateLimiter(
        Redis.from_url("redis://127.0.0.1:6399/0", socket_connect_timeout=0.3),
        rate_per_minute=400,
        event_sink=lambda _event, **_fields: events.append(str(_event)),
    )
    limiter.wait_before_call()  # 不应抛异常
    assert limiter._degraded  # noqa: SLF001  # 降级标志（测试专用）
    assert "shareholder_rate_limiter_degraded" in events
