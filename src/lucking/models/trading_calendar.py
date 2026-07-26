"""Current-value trading calendar table."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, String
from sqlalchemy.dialects.mysql import CHAR, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from lucking.db import Base


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"

    market_code: Mapped[str] = mapped_column(
        String(4).with_variant(CHAR(4, charset="ascii", collation="ascii_bin"), "mysql"),
        primary_key=True,
    )
    calendar_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    previous_open_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_market: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False).with_variant(DATETIME(fsp=6), "mysql"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False).with_variant(DATETIME(fsp=6), "mysql"),
        nullable=False,
    )
