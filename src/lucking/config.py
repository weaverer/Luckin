"""Environment-backed application configuration."""

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
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

    @field_validator("trading_calendar_provider", "stock_list_provider")
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

    @field_validator("trading_calendar_timezone", "stock_list_timezone")
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

    def require_tushare_token(self) -> str:
        """Return the token only when the selected adapter is constructed."""
        if self.tushare_token is None or not self.tushare_token.get_secret_value():
            raise ValueError("选择 tushare Provider 时必须配置 TUSHARE_TOKEN")
        return self.tushare_token.get_secret_value()
