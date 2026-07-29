"""Explicit Provider registry and composition root."""

from collections.abc import Callable

from lucking.config import Settings
from lucking.integrations.tushare.client import TushareClient
from lucking.integrations.tushare.trading_calendar_provider import (
    TushareTradingCalendarProvider,
)
from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationProvider,
)
from lucking.ports.broker_recommendation_provider import (
    ProviderConfigurationError as BrokerRecommendationProviderConfigurationError,
)
from lucking.ports.stock_list_provider import (
    ProviderConfigurationError as StockListProviderConfigurationError,
)
from lucking.ports.stock_list_provider import (
    StockListProvider,
)
from lucking.ports.trading_calendar_provider import (
    ProviderConfigurationError,
    TradingCalendarProvider,
)

ProviderFactory = Callable[[Settings], TradingCalendarProvider]
StockListProviderFactory = Callable[[Settings], StockListProvider]
BrokerRecommendationProviderFactory = Callable[[Settings], BrokerRecommendationProvider]


def build_tushare_trading_calendar_provider(settings: Settings) -> TradingCalendarProvider:
    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise ProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    return TushareTradingCalendarProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url)
    )


PROVIDERS: dict[str, ProviderFactory] = {
    "tushare": build_tushare_trading_calendar_provider,
}
STOCK_LIST_PROVIDERS: dict[str, StockListProviderFactory] = {}
BROKER_RECOMMENDATION_PROVIDERS: dict[str, BrokerRecommendationProviderFactory] = {}


def build_trading_calendar_provider(
    provider_code: str, settings: Settings
) -> TradingCalendarProvider:
    try:
        factory = PROVIDERS[provider_code]
    except KeyError as exc:
        raise ProviderConfigurationError(provider_code, "Provider 未注册") from exc
    return factory(settings)


def register_stock_list_provider(provider_code: str, factory: StockListProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    STOCK_LIST_PROVIDERS[normalized] = factory


def build_stock_list_provider(provider_code: str, settings: Settings) -> StockListProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = STOCK_LIST_PROVIDERS[normalized]
    except KeyError as exc:
        raise StockListProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_stock_list_provider(settings: Settings) -> StockListProvider:
    from lucking.integrations.tushare.stock_list_provider import TushareStockListProvider
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise StockListProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.stock_list_log_dir,
        filename=settings.stock_list_log_filename,
    )
    return TushareStockListProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        row_cap=settings.stock_list_segment_row_cap,
        event_sink=log_store.write,
    )


register_stock_list_provider("tushare", build_tushare_stock_list_provider)


def register_broker_recommendation_provider(
    provider_code: str, factory: BrokerRecommendationProviderFactory
) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    BROKER_RECOMMENDATION_PROVIDERS[normalized] = factory


def build_broker_recommendation_provider(
    provider_code: str, settings: Settings
) -> BrokerRecommendationProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = BROKER_RECOMMENDATION_PROVIDERS[normalized]
    except KeyError as exc:
        raise BrokerRecommendationProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_broker_recommendation_provider(
    settings: Settings,
) -> BrokerRecommendationProvider:
    from lucking.integrations.tushare.broker_recommendation_provider import (
        TushareBrokerRecommendationProvider,
    )
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise BrokerRecommendationProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.broker_recommendation_log_dir,
        filename=settings.broker_recommendation_log_filename,
    )
    return TushareBrokerRecommendationProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.broker_recommendation_page_limit,
        max_pages=settings.broker_recommendation_max_pages,
        pagination_enabled=settings.broker_recommendation_tushare_pagination_enabled,
        event_sink=log_store.write,
    )


register_broker_recommendation_provider("tushare", build_tushare_broker_recommendation_provider)
