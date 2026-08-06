"""供应商无关契约：ShareholderDataProvider（股东数据三接口）。

三个提取方法分别被三个独立 Flow/Service 链路消费（prefect-flow.md §1/§3）；
Provider 实例与节流器为共享资源，某一方法的失败只影响对应链路，
不影响其他两个方法（故障隔离契约，用户显式要求）。
错误与 ``RetrievalEvidence`` 复用 ``lucking.ports.market_data_common``
与 ``lucking.models.market_data``。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lucking.models.shareholder_data import (
    ProviderShareholderBatch,
    ProviderShareholderCountBatch,
    ShareholderDataRequest,
)


@runtime_checkable
class ShareholderDataProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_top10_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch: ...

    def fetch_top10_float_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch: ...

    def fetch_holder_count(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderCountBatch: ...
