"""供应商无关契约：IndexFactorProvider（指数技术因子）。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lucking.models.index_factor import IndexFactorRequest, ProviderIndexFactorBatch


@runtime_checkable
class IndexFactorProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_index_factors(
        self,
        request: IndexFactorRequest,
        *,
        deadline: float,
    ) -> ProviderIndexFactorBatch: ...
