"""Explicit Provider registry and composition root."""

from collections.abc import Callable

from lucking.config import Settings
from lucking.integrations.tushare.client import TushareClient
from lucking.integrations.tushare.trading_calendar_provider import (
    TushareTradingCalendarProvider,
)
from lucking.ports.adj_factor_provider import AdjFactorProvider
from lucking.ports.broker_recommendation_provider import (
    BrokerRecommendationProvider,
)
from lucking.ports.broker_recommendation_provider import (
    ProviderConfigurationError as BrokerRecommendationProviderConfigurationError,
)
from lucking.ports.daily_basic_provider import DailyBasicProvider
from lucking.ports.daily_quote_provider import DailyQuoteProvider
from lucking.ports.index_factor_common import IndexFactorProvider
from lucking.ports.market_data_common import (
    ProviderConfigurationError as MarketDataProviderConfigurationError,
)
from lucking.ports.shareholder_data_common import ShareholderDataProvider
from lucking.ports.stock_factor_common import StockFactorProvider
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
from lucking.ports.weekly_monthly_kline_provider import WeeklyMonthlyKlineProvider

ProviderFactory = Callable[[Settings], TradingCalendarProvider]
StockListProviderFactory = Callable[[Settings], StockListProvider]
BrokerRecommendationProviderFactory = Callable[[Settings], BrokerRecommendationProvider]
DailyQuoteProviderFactory = Callable[[Settings], DailyQuoteProvider]
AdjFactorProviderFactory = Callable[[Settings], AdjFactorProvider]
IndexFactorProviderFactory = Callable[[Settings], IndexFactorProvider]
StockFactorProviderFactory = Callable[[Settings], StockFactorProvider]
ShareholderDataProviderFactory = Callable[[Settings], ShareholderDataProvider]
DailyBasicProviderFactory = Callable[[Settings], DailyBasicProvider]
KlineProviderFactory = Callable[[Settings], WeeklyMonthlyKlineProvider]


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


DAILY_QUOTE_PROVIDERS: dict[str, DailyQuoteProviderFactory] = {}
ADJ_FACTOR_PROVIDERS: dict[str, AdjFactorProviderFactory] = {}


def register_daily_quote_provider(provider_code: str, factory: DailyQuoteProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    DAILY_QUOTE_PROVIDERS[normalized] = factory


def build_daily_quote_provider(provider_code: str, settings: Settings) -> DailyQuoteProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = DAILY_QUOTE_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_daily_quote_provider(settings: Settings) -> DailyQuoteProvider:
    from lucking.integrations.tushare.daily_quote_provider import TushareDailyQuoteProvider
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.market_data_log_dir,
        filename=settings.market_data_log_filename,
    )
    return TushareDailyQuoteProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.market_data_page_limit,
        max_pages=settings.market_data_max_pages,
        pagination_enabled=settings.market_data_tushare_pagination_enabled,
        event_sink=log_store.write,
    )


def register_adj_factor_provider(provider_code: str, factory: AdjFactorProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    ADJ_FACTOR_PROVIDERS[normalized] = factory


def build_adj_factor_provider(provider_code: str, settings: Settings) -> AdjFactorProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = ADJ_FACTOR_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_adj_factor_provider(settings: Settings) -> AdjFactorProvider:
    from lucking.integrations.tushare.adj_factor_provider import TushareAdjFactorProvider
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.market_data_log_dir,
        filename=settings.market_data_log_filename,
    )
    return TushareAdjFactorProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.market_data_page_limit,
        max_pages=settings.market_data_max_pages,
        pagination_enabled=settings.market_data_tushare_pagination_enabled,
        event_sink=log_store.write,
    )


register_daily_quote_provider("tushare", build_tushare_daily_quote_provider)
register_adj_factor_provider("tushare", build_tushare_adj_factor_provider)


DAILY_BASIC_PROVIDERS: dict[str, DailyBasicProviderFactory] = {}
KLINE_PROVIDERS: dict[str, KlineProviderFactory] = {}


def register_daily_basic_provider(provider_code: str, factory: DailyBasicProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    DAILY_BASIC_PROVIDERS[normalized] = factory


def build_daily_basic_provider(provider_code: str, settings: Settings) -> DailyBasicProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = DAILY_BASIC_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_daily_basic_provider(settings: Settings) -> DailyBasicProvider:
    from lucking.integrations.tushare.daily_basic_provider import TushareDailyBasicProvider
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.market_data_log_dir,
        filename=settings.market_data_log_filename,
    )
    return TushareDailyBasicProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.market_data_page_limit,
        max_pages=settings.market_data_max_pages,
        pagination_enabled=settings.market_data_tushare_pagination_enabled,
        event_sink=log_store.write,
    )


def register_kline_provider(provider_code: str, factory: KlineProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    KLINE_PROVIDERS[normalized] = factory


def build_kline_provider(provider_code: str, settings: Settings) -> WeeklyMonthlyKlineProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = KLINE_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_kline_provider(settings: Settings) -> WeeklyMonthlyKlineProvider:
    from lucking.integrations.tushare.weekly_monthly_kline_provider import (
        TushareWeeklyMonthlyKlineProvider,
    )
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.market_data_log_dir,
        filename=settings.market_data_log_filename,
    )
    return TushareWeeklyMonthlyKlineProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.market_data_page_limit,
        max_pages=settings.market_data_max_pages,
        pagination_enabled=settings.market_data_tushare_pagination_enabled,
        event_sink=log_store.write,
    )


register_daily_basic_provider("tushare", build_tushare_daily_basic_provider)
register_kline_provider("tushare", build_tushare_kline_provider)


INDEX_FACTOR_PROVIDERS: dict[str, IndexFactorProviderFactory] = {}


def register_index_factor_provider(provider_code: str, factory: IndexFactorProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    INDEX_FACTOR_PROVIDERS[normalized] = factory


def build_index_factor_provider(provider_code: str, settings: Settings) -> IndexFactorProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = INDEX_FACTOR_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_index_factor_provider(settings: Settings) -> IndexFactorProvider:
    from lucking.integrations.tushare.index_factor_provider import (
        TushareIndexFactorProvider,
    )
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.index_factor_log_dir,
        filename=settings.index_factor_log_filename,
    )
    return TushareIndexFactorProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.index_factor_page_limit,
        rate_per_minute=settings.index_factor_rate_limit_per_minute,
        event_sink=log_store.write,
    )


register_index_factor_provider("tushare", build_tushare_index_factor_provider)


STOCK_FACTOR_PROVIDERS: dict[str, StockFactorProviderFactory] = {}


def register_stock_factor_provider(provider_code: str, factory: StockFactorProviderFactory) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    STOCK_FACTOR_PROVIDERS[normalized] = factory


def build_stock_factor_provider(provider_code: str, settings: Settings) -> StockFactorProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = STOCK_FACTOR_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_stock_factor_provider(settings: Settings) -> StockFactorProvider:
    from lucking.integrations.tushare.stock_factor_provider import (
        TushareStockFactorProvider,
    )
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.stock_factor_log_dir,
        filename=settings.stock_factor_log_filename,
    )
    return TushareStockFactorProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.stock_factor_page_limit,
        rate_per_minute=settings.stock_factor_rate_limit_per_minute,
        event_sink=log_store.write,
    )


register_stock_factor_provider("tushare", build_tushare_stock_factor_provider)


SHAREHOLDER_DATA_PROVIDERS: dict[str, ShareholderDataProviderFactory] = {}


def register_shareholder_data_provider(
    provider_code: str, factory: ShareholderDataProviderFactory
) -> None:
    normalized = provider_code.strip().lower()
    if not normalized:
        raise ValueError("Provider code 不能为空")
    SHAREHOLDER_DATA_PROVIDERS[normalized] = factory


def build_shareholder_data_provider(
    provider_code: str, settings: Settings
) -> ShareholderDataProvider:
    normalized = provider_code.strip().lower()
    try:
        factory = SHAREHOLDER_DATA_PROVIDERS[normalized]
    except KeyError as exc:
        raise MarketDataProviderConfigurationError(
            normalized or "<empty>", "Provider 未注册"
        ) from exc
    return factory(settings)


def build_tushare_shareholder_data_provider(settings: Settings) -> ShareholderDataProvider:
    from lucking.integrations.tushare.rate_limiter import Throttle
    from lucking.integrations.tushare.redis_rate_limiter import RedisRateLimiter
    from lucking.integrations.tushare.shareholder_data_provider import (
        TushareShareholderDataProvider,
    )
    from lucking.logging import JsonlLogStore

    try:
        token = settings.require_tushare_token()
    except ValueError as exc:
        raise MarketDataProviderConfigurationError("tushare", "缺少所需秘密配置") from exc
    log_store = JsonlLogStore(
        settings.shareholder_data_log_dir,
        filename=settings.shareholder_data_log_filename,
    )
    limiter: Throttle | None = None
    if settings.shareholder_data_rate_limiter == "redis":
        # 账户级共享预算（400/min 三接口合计，跨进程）：Redis 分布式节流器，
        # 三接口的所有 flow run 进程共用同一预算（research 决策 4 修订）。
        # Redis 不可达时内部降级为进程级限流（fail-open），不阻断同步。
        from redis import Redis

        limiter = RedisRateLimiter(
            Redis.from_url(
                settings.redis_url,
                password=(
                    settings.redis_password.get_secret_value()
                    if settings.redis_password is not None
                    else None
                ),
            ),
            rate_per_minute=settings.shareholder_data_rate_limit_per_minute,
            event_sink=log_store.write,
        )
    return TushareShareholderDataProvider(
        TushareClient(token=token, api_url=settings.tushare_api_url),
        page_limit=settings.shareholder_data_page_limit,
        rate_per_minute=settings.shareholder_data_rate_limit_per_minute,
        limiter=limiter,
        event_sink=log_store.write,
    )


register_shareholder_data_provider("tushare", build_tushare_shareholder_data_provider)
