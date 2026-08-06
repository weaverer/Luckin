"""供应商无关契约：StockFactorProvider（股票技术面因子）。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lucking.models.stock_factor import ProviderStockFactorBatch, StockFactorRequest


@runtime_checkable
class StockFactorProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_stock_factors(
        self,
        request: StockFactorRequest,
        *,
        deadline: float,
    ) -> ProviderStockFactorBatch: ...
