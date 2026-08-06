"""兼容别名模块：``IndexRateLimiter`` = 共享 ``RateLimiter``。

006 指数因子上线后的既有 import 保持不变；新功能统一使用
``lucking.integrations.tushare.rate_limiter`` 的 ``RateLimiter``
（research 决策 4，泛化重命名）。
"""

from __future__ import annotations

from lucking.integrations.tushare.rate_limiter import RateLimiter

IndexRateLimiter = RateLimiter

__all__ = ["IndexRateLimiter"]
