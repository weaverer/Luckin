"""Tushare trade_cal adapter for the project-owned Provider port."""

from datetime import date, datetime

from lucking.integrations.tushare.client import (
    TushareClient,
    TushareError,
    TushareErrorCategory,
)
from lucking.ports.trading_calendar_provider import (
    MarketCode,
    ProviderAuthenticationError,
    ProviderCalendarDay,
    ProviderError,
    ProviderPayloadError,
    ProviderQuotaExceededError,
    ProviderRateLimitedError,
    ProviderRequestError,
    ProviderUnavailableError,
)


class TushareTradingCalendarProvider:
    provider_code = "tushare"
    source_market = "SSE"
    fields = ("exchange", "cal_date", "is_open", "pretrade_date")

    def __init__(self, client: TushareClient) -> None:
        self._client = client

    def fetch_calendar(
        self, market_code: MarketCode, start_date: date, end_date: date
    ) -> list[ProviderCalendarDay]:
        try:
            MarketCode.enabled(market_code)
        except ProviderRequestError as exc:
            raise ProviderRequestError(self.provider_code, exc.summary) from exc
        try:
            table = self._client.call(
                "trade_cal",
                params={
                    "exchange": self.source_market,
                    "start_date": start_date.strftime("%Y%m%d"),
                    "end_date": end_date.strftime("%Y%m%d"),
                },
                fields=self.fields,
            )
        except TushareError as exc:
            raise _map_client_error(exc) from exc

        result: list[ProviderCalendarDay] = []
        seen: set[date] = set()
        try:
            for row in table.rows:
                if row["exchange"] != self.source_market:
                    raise ValueError("来源交易所不是 SSE")
                calendar_date = _parse_date(row["cal_date"])
                if calendar_date in seen:
                    raise ValueError("来源包含重复日期")
                seen.add(calendar_date)
                is_open_raw = row["is_open"]
                if isinstance(is_open_raw, bool) or is_open_raw not in (0, 1, "0", "1"):
                    raise ValueError("is_open 只能为 0 或 1")
                previous_raw = row["pretrade_date"]
                previous = None if previous_raw in (None, "") else _parse_date(previous_raw)
                result.append(
                    ProviderCalendarDay(
                        market_code=market_code,
                        calendar_date=calendar_date,
                        is_open=int(is_open_raw) == 1,
                        previous_open_date=previous,
                        source=self.provider_code,
                        source_market=self.source_market,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderPayloadError(self.provider_code, str(exc)) from exc
        return result


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("日期字段必须为字符串")
    return datetime.strptime(value, "%Y%m%d").date()


def _map_client_error(error: TushareError) -> ProviderError:
    mapping: dict[TushareErrorCategory, type[ProviderError]] = {
        TushareErrorCategory.NETWORK: ProviderUnavailableError,
        TushareErrorCategory.RATE_LIMITED: ProviderRateLimitedError,
        TushareErrorCategory.QUOTA_EXHAUSTED: ProviderQuotaExceededError,
        TushareErrorCategory.UPSTREAM_UNAVAILABLE: ProviderUnavailableError,
        TushareErrorCategory.AUTHENTICATION: ProviderAuthenticationError,
        TushareErrorCategory.BAD_REQUEST: ProviderRequestError,
        TushareErrorCategory.UPSTREAM_BUSINESS: ProviderRequestError,
        TushareErrorCategory.INVALID_PAYLOAD: ProviderPayloadError,
        TushareErrorCategory.EMPTY_PAYLOAD: ProviderPayloadError,
    }
    return mapping[error.category]("tushare", error.summary, error.status_code)

