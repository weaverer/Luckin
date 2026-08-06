"""跨进程分布式节流器（Redis 滑窗）：账户级限流预算共享。

背景（research 决策 4 修订）：008 三个接口拆分为 3 套独立 Flow 后，
Prefect 每个 flow run 运行于独立子进程，进程级 ``RateLimiter`` 各自
计数会使账户级请求合计超过 400 次/分钟（用户澄清：400/min 是三个接口
**共享的账户预算**，不是每接口各 400/min）。本实现基于 Redis ZSET
滑窗 + Lua 原子判定，让所有进程共享同一预算：
- 任意 60 秒窗口内真实 HTTP 请求数 ≤ rate（跨进程合计）；
- 额外强制最小间隔（默认 60/rate，即 150 毫秒），请求均匀分布；
- Lua 脚本原子完成"清理过期 → 计数 → 判定 → 记录"，避免并发竞态；
- Redis 不可达时**降级为进程级限流**（fail-open：请求仍被本地节流，
  不阻断同步，不把限流基础设施故障变成数据同步故障）。

接口与 ``RateLimiter`` 一致（``wait_before_call``），可替换注入
（shareholder-data-provider.md §5 的"共享节流器"由进程级升级为分布式）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from redis import Redis
from redis.exceptions import RedisError

from lucking.integrations.tushare.rate_limiter import RateLimiter

# Lua：原子滑窗判定与记录。
# KEYS[1]=窗口 ZSET；KEYS[2]=上次调用时间戳；KEYS[3]=序号计数器
# ARGV[1]=now(ms)；ARGV[2]=窗口(ms)；ARGV[3]=rate；ARGV[4]=最小间隔(ms)
# 返回：需要等待的毫秒数（0 = 放行并已记录）。
_LUA_WINDOW = """
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local rate = tonumber(ARGV[3])
local min_interval_ms = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window_ms)
local count = redis.call('ZCARD', KEYS[1])
local wait = 0
local last = tonumber(redis.call('GET', KEYS[2]) or '0')
if last > 0 then
    local elapsed = now - last
    if elapsed < min_interval_ms then
        wait = min_interval_ms - elapsed
    end
end
if count >= rate then
    local members = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local oldest = tonumber(members[2])
    local until_window = window_ms - (now - oldest)
    if until_window > wait then
        wait = until_window
    end
end
if wait > 0 then
    return wait
end
local seq = redis.call('INCR', KEYS[3])
redis.call('ZADD', KEYS[1], now, now .. ':' .. seq)
redis.call('EXPIRE', KEYS[1], math.ceil(window_ms / 1000) + 1)
redis.call('EXPIRE', KEYS[3], 60)
redis.call('SET', KEYS[2], now, 'EX', 60)
return 0
"""


class RedisRateLimiter:
    """跨进程滑窗节流器：任意窗口内合计 ≤ rate，最小间隔 ≥ 60/rate。

    Redis 故障时降级为进程级 ``RateLimiter``（fail-open），并可通过
    ``event_sink`` 上报降级事件供可观测性消费。
    """

    def __init__(
        self,
        redis: Redis,
        *,
        rate_per_minute: int = 400,
        window_seconds: float = 60.0,
        key_prefix: str = "lucking:shareholder:rate",
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_sink: Callable[..., None] | None = None,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute 必须大于 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds 必须大于 0")
        self._redis = redis
        self._rate = rate_per_minute
        self._window_ms = int(window_seconds * 1000)
        self._min_interval_ms = max(1, int(window_seconds * 1000 / rate_per_minute))
        self._key_prefix = key_prefix
        self._monotonic = monotonic
        self._sleep = sleep
        self._event_sink = event_sink or (lambda _event, **_fields: None)
        self._fallback = RateLimiter(
            rate_per_minute=rate_per_minute,
            window_seconds=window_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
        self._degraded = False
        self._script = redis.register_script(_LUA_WINDOW)

    def wait_before_call(self) -> None:
        """阻塞直到本次调用满足账户级节流约束（跨进程合计）。"""
        if self._degraded:
            self._fallback.wait_before_call()
            return
        deadline = self._monotonic() + self._window_ms / 1000.0
        try:
            while True:
                wait_ms = int(
                    self._script(
                        keys=[
                            f"{self._key_prefix}:window",
                            f"{self._key_prefix}:last",
                            f"{self._key_prefix}:seq",
                        ],
                        args=[
                            int(self._monotonic() * 1000),
                            self._window_ms,
                            self._rate,
                            self._min_interval_ms,
                        ],
                    )
                )
                if wait_ms <= 0:
                    return
                if self._monotonic() + wait_ms / 1000.0 >= deadline:
                    # 已等待满一个窗口仍不放行：按窗口语义放行（滑窗最坏
                    # 等待 = 窗口长度，到期即代表最旧记录已过期）
                    self._record_now()
                    return
                self._sleep(wait_ms / 1000.0)
        except RedisError:
            # 降级为进程级限流（fail-open），并上报事件
            self._degraded = True
            self._event_sink(
                "shareholder_rate_limiter_degraded",
                reason="Redis 不可达，降级为进程级限流",
            )
            self._fallback.wait_before_call()

    def _record_now(self) -> None:
        """窗口到期兜底放行时直接记录一次调用（尽力而为）。"""
        try:
            self._script(
                keys=[
                    f"{self._key_prefix}:window",
                    f"{self._key_prefix}:last",
                    f"{self._key_prefix}:seq",
                ],
                args=[
                    int(self._monotonic() * 1000),
                    self._window_ms,
                    self._rate,
                    self._min_interval_ms,
                ],
            )
        except RedisError:
            pass
