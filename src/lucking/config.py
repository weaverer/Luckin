"""Environment-backed application configuration."""

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings shared by the composition root and workflow."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "mysql+pymysql://lucking:change-me@127.0.0.1:3306/lucking"
    trading_calendar_provider: str = "tushare"
    tushare_token: SecretStr | None = None
    tushare_api_url: str = "https://api.tushare.pro"
    trading_calendar_log_dir: Path = Path("logs")
    trading_calendar_timezone: str = "Asia/Shanghai"
    stock_list_provider: str = "tushare"
    stock_list_scope: str = "CN-S"
    stock_list_timezone: str = "Asia/Shanghai"
    stock_list_log_dir: Path = Path("logs")
    stock_list_log_filename: str = "stock-list-sync.jsonl"
    stock_list_fetch_deadline_seconds: int = 1500
    stock_list_timeliness_target_ms: int = 1_800_000
    stock_list_segment_row_cap: int = 6000
    broker_recommendation_provider: str = "tushare"
    broker_recommendation_timezone: str = "Asia/Shanghai"
    broker_recommendation_log_dir: Path = Path("logs")
    broker_recommendation_log_filename: str = "broker-recommendation-sync.jsonl"
    broker_recommendation_fetch_deadline_seconds: int = 1500
    broker_recommendation_run_lease_seconds: int = 2100
    broker_recommendation_timeliness_target_ms: int = 1_800_000
    broker_recommendation_page_limit: int = 1000
    broker_recommendation_max_pages: int = 100
    broker_recommendation_tushare_pagination_enabled: bool = False
    broker_recommendation_backfill_max_months: int = 120

    @field_validator(
        "trading_calendar_provider",
        "stock_list_provider",
        "broker_recommendation_provider",
    )
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("TRADING_CALENDAR_PROVIDER 不能为空")
        return normalized

    @field_validator("tushare_api_url")
    @classmethod
    def validate_tushare_url(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("TUSHARE_API_URL 必须是 HTTP(S) URL")
        return value.rstrip("/")

    @field_validator(
        "trading_calendar_timezone",
        "stock_list_timezone",
        "broker_recommendation_timezone",
    )
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"未知时区：{value}") from exc
        return value

    @field_validator("stock_list_scope")
    @classmethod
    def validate_stock_list_scope(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != "CN-S":
            raise ValueError("STOCK_LIST_SCOPE 首期只允许 CN-S")
        return normalized

    @field_validator(
        "stock_list_fetch_deadline_seconds",
        "stock_list_timeliness_target_ms",
        "stock_list_segment_row_cap",
    )
    @classmethod
    def validate_positive_stock_list_setting(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("股票列表数值配置必须大于 0")
        return value

    @field_validator(
        "broker_recommendation_fetch_deadline_seconds",
        "broker_recommendation_run_lease_seconds",
        "broker_recommendation_timeliness_target_ms",
        "broker_recommendation_page_limit",
        "broker_recommendation_max_pages",
        "broker_recommendation_backfill_max_months",
    )
    @classmethod
    def validate_positive_broker_recommendation_setting(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("券商金股数值配置必须大于 0")
        return value

    @model_validator(mode="after")
    def validate_broker_recommendation_invariants(self) -> "Settings":
        if self.broker_recommendation_page_limit != 1000:
            raise ValueError("BROKER_RECOMMENDATION_PAGE_LIMIT 固定为 1000")
        if self.broker_recommendation_run_lease_seconds != 2100:
            raise ValueError("BROKER_RECOMMENDATION_RUN_LEASE_SECONDS 固定为 2100")
        if (
            self.broker_recommendation_run_lease_seconds
            <= self.broker_recommendation_fetch_deadline_seconds
        ):
            raise ValueError("运行租约必须大于 Provider 截止时间")
        if self.broker_recommendation_backfill_max_months != 120:
            raise ValueError("BROKER_RECOMMENDATION_BACKFILL_MAX_MONTHS 固定为 120")
        return self

    def require_tushare_token(self) -> str:
        """Return the token only when the selected adapter is constructed."""
        if self.tushare_token is None or not self.tushare_token.get_secret_value():
            raise ValueError("选择 tushare Provider 时必须配置 TUSHARE_TOKEN")
        return self.tushare_token.get_secret_value()
