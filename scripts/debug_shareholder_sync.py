"""开发辅助：真实 ClickHouse 调试增量同步链路（含清理，不留数据）。"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

sys.path.insert(0, ".")
sys.path.insert(0, "tests")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from lucking.clickhouse import ClickHouseClient, migrate  # noqa: E402
from lucking.config import Settings  # noqa: E402
from lucking.db import Base  # noqa: E402
from lucking.repositories.market_data import (  # noqa: E402
    SqlAlchemyMarketDataRepository,
)
from lucking.repositories.shareholder_data_clickhouse import (  # noqa: E402
    ShareholderDataClickHouseRepository,
)
from lucking.services.shareholder_data import (  # noqa: E402
    ScheduledShareholderDataSyncCommand,
    ShareholderDataService,
)
from tests.contract.shareholder_data_memory import MemoryShareholderDataProvider  # noqa: E402
from tests.unit.test_shareholder_data_service import seeded_factory  # noqa: E402

engine = create_engine("sqlite+pysqlite:///:memory:")
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine, expire_on_commit=False)
sf = seeded_factory(factory)

settings = Settings()
migrate(settings)
ch = ClickHouseClient(
    settings.clickhouse_host,
    settings.clickhouse_port,
    settings.clickhouse_database,
    user=settings.clickhouse_user,
    password=(
        settings.clickhouse_password.get_secret_value()
        if settings.clickhouse_password is not None
        else None
    ),
)
ch.execute("ALTER TABLE lucking.shareholder_holding DELETE WHERE stock_code LIKE '%dbg%'")
rows = []
for pid, _v, code in (
    ("600000.SH", "XSHG", "600000"),
    ("000001.SZ", "XSHE", "000001"),
    ("300750.SZ", "XSHE", "300750"),
    ("830799.BJ", "XBSE", "830799"),
):
    rows.append(
        {
            "end_date": date(2026, 7, 30),
            "stock_id": f"stock-{code}",
            "holder_kind": "TOP10",
            "holder_name": "测试股东",
            "ann_date": date(2026, 7, 29),
            "stock_code": f"{pid}-dbg",
            "hold_amount": Decimal("1000000.00"),
            "hold_ratio": Decimal("1.5000"),
            "hold_float_ratio": Decimal("1.5000"),
            "hold_change": Decimal("0.0000"),
            "holder_type": "一般企业",
            "updated_at": datetime.now(UTC).replace(tzinfo=None),
        }
    )
ch.insert_rows(
    "shareholder_holding",
    (
        "end_date",
        "stock_id",
        "holder_kind",
        "holder_name",
        "ann_date",
        "stock_code",
        "hold_amount",
        "hold_ratio",
        "hold_float_ratio",
        "hold_change",
        "holder_type",
        "updated_at",
    ),
    rows,
)
print("watermark:", ch.execute(
    "SELECT max(ann_date) AS w FROM lucking.shareholder_holding FINAL "
    "WHERE holder_kind = 'TOP10'"
))
provider = MemoryShareholderDataProvider(
    codes=("600000.SH", "000001.SZ", "300750.SZ", "830799.BJ")
)
repo = SqlAlchemyMarketDataRepository(sf, lease_seconds=2100)
service = ShareholderDataService(
    provider,
    repo,
    ShareholderDataClickHouseRepository(ch),
    sf,
    timezone="Asia/Shanghai",
    fetch_deadline_seconds=1500,
    page_limit=6000,
)
cmd = ScheduledShareholderDataSyncCommand(
    "top10-holders-sync", datetime(2026, 7, 31, 9, 0, tzinfo=UTC), str(uuid4())
)
r = service.sync_top10_holders(cmd)
print(
    "result:", r.status, "received", r.received_count, "valid", r.valid_count,
    "added", r.added_count, "invalid", r.invalid_count, "duplicate", r.duplicate_count,
)
print("provider days:", provider.requested_dates["TOP10"])
ch.execute("ALTER TABLE lucking.shareholder_holding DELETE WHERE stock_code LIKE '%dbg%'")
