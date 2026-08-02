"""MySQL 审计表 schema 三方一致、并发认领、租约边界与失败演练测试。"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import UniqueConstraint, create_engine, delete, func, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from lucking.clickhouse import ClickHouseClient, migrate
from lucking.config import Settings
from lucking.models.market_data import (
    DataKind,
    MarketDataSyncAttempt,
    MarketDataSyncIssue,
    MarketDataSyncRun,
)
from lucking.models.stock_list import StockCurrent, StockProviderMapping
from lucking.repositories.market_data import (
    SqlAlchemyMarketDataRepository,
    SyncCounts,
)
from lucking.repositories.market_data_clickhouse import MarketDataClickHouseRepository
from lucking.services.market_data import (
    BackfillMarketDataCommand,
    MarketDataService,
    SyncStatus,
)
from tests.contract.market_data_memory import (
    MemoryAdjFactorProvider,
    MemoryDailyBasicProvider,
    MemoryDailyQuoteProvider,
)

_STOCK_COUNT = 3

TABLES = (
    MarketDataSyncRun.__table__,
    MarketDataSyncAttempt.__table__,
    MarketDataSyncIssue.__table__,
)


def test_orm_and_migration_declare_governed_physical_and_business_keys() -> None:
    migration = Path("migrations/versions/004_create_market_data_audit_tables.py").read_text()
    for table in TABLES:
        assert [column.name for column in table.primary_key.columns] == ["id"]
        assert table.c.id.type.python_type is int
        assert all(column.comment for column in table.columns)
    for comment in (
        "行情数据同步运行",
        "行情数据同步执行尝试",
        "行情数据同步质量问题",
    ):
        assert comment in migration
    assert "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in migration
    assert "lease_expires_at" in migration
    assert "target_trade_date" in migration
    assert "data_kind" in migration
    unique_by_table = {
        table.name: {
            frozenset(constraint.columns.keys())
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        for table in TABLES
    }
    assert {"run_id"} in unique_by_table["market_data_sync_run"]
    assert {"run_key"} in unique_by_table["market_data_sync_run"]
    assert {"attempt_id"} in unique_by_table["market_data_sync_attempt"]
    assert {"run_id", "attempt_no"} in unique_by_table["market_data_sync_attempt"]
    assert {"flow_run_id"} in unique_by_table["market_data_sync_attempt"]
    assert {"issue_id"} in unique_by_table["market_data_sync_issue"]


@pytest.mark.mysql
def test_live_mysql_has_bigint_autoincrement_and_chinese_comments() -> None:
    url = _mysql_url()
    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
        expected = {table.name for table in TABLES}
        if not expected <= names:
            pytest.skip("测试库尚未 upgrade 到 revision 004")
        with engine.connect() as connection:
            for table in TABLES:
                ddl = connection.execute(text(f"SHOW CREATE TABLE `{table.name}`")).one()[1]
                assert "`id` bigint" in ddl.lower()
                assert "AUTO_INCREMENT" in ddl
                assert "ON UPDATE CURRENT_TIMESTAMP" in ddl
                assert table.comment in ddl
                live_columns = {
                    column["name"]: column for column in inspect(connection).get_columns(table.name)
                }
                assert set(live_columns) == set(table.c.keys())
                for column in table.columns:
                    assert live_columns[column.name]["comment"] == column.comment
                assert inspect(connection).get_pk_constraint(table.name)["constrained_columns"] == [
                    "id"
                ]
                unique_keys = inspect(connection).get_unique_constraints(table.name)
                for constraint in table.constraints:
                    if hasattr(constraint, "unique") and constraint.unique:
                        assert any(
                            set(constraint.columns) == set(unique["column_names"])
                            for unique in unique_keys
                        )
    finally:
        engine.dispose()


@pytest.mark.mysql
def test_concurrent_claims_and_expired_lease_retry_same_run() -> None:
    engine = create_engine(_mysql_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyMarketDataRepository(factory)
    marker = uuid4().hex
    run_ids: list[str] = []
    target = date(2026, 7, 24)
    try:
        claim = repository.claim_run_and_start_attempt(
            run_key=f"{marker}-lease",
            run_kind="BACKFILL",
            data_kind=DataKind.DAILY_QUOTE.value,
            target_trade_date=target,
            scope_fingerprint=marker,
            flow_run_id=f"{marker}-flow-1",
            provider_code="memory",
            page_limit=6000,
            backfill_batch_id=marker,
        )
        run_ids.append(claim.run_id)
        with factory() as session:
            attempt = session.scalar(
                select(MarketDataSyncAttempt).where(
                    MarketDataSyncAttempt.attempt_id == claim.attempt_id
                )
            )
            assert attempt is not None
            assert (attempt.lease_expires_at - attempt.started_at).total_seconds() == 2100
        current = repository.resolve_backfill_date(
            data_kind=DataKind.DAILY_QUOTE.value,
            backfill_batch_id=marker,
            target_trade_date=target,
        )
        assert current.action.value == "IN_PROGRESS"
        with factory.begin() as session:
            session.execute(
                update(MarketDataSyncAttempt)
                .where(MarketDataSyncAttempt.attempt_id == claim.attempt_id)
                .values(lease_expires_at=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"))
            )
        expired = repository.resolve_backfill_date(
            data_kind=DataKind.DAILY_QUOTE.value,
            backfill_batch_id=marker,
            target_trade_date=target,
        )
        assert expired.action.value == "RETRY"
        retry = repository.claim_run_and_start_attempt(
            run_key="",
            run_kind="",
            data_kind=DataKind.DAILY_QUOTE.value,
            target_trade_date=date.min,
            scope_fingerprint="",
            flow_run_id=f"{marker}-flow-2",
            provider_code="memory",
            page_limit=6000,
            retry_run_id=claim.run_id,
        )
        assert retry.run_id == claim.run_id
        assert retry.attempt_no == 2
        with factory() as session:
            old_status = session.scalar(
                select(MarketDataSyncAttempt.status).where(
                    MarketDataSyncAttempt.attempt_id == claim.attempt_id
                )
            )
            assert old_status == "ABANDONED"
    finally:
        _cleanup_runs(factory, run_ids)
        engine.dispose()


@pytest.mark.mysql
def test_ten_scheduled_backfill_races_publish_distinct_runs() -> None:
    engine = create_engine(_mysql_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyMarketDataRepository(factory)
    marker = uuid4().hex
    run_ids: list[str] = []
    target = date(2026, 7, 27)
    try:
        for index in range(10):
            scheduled = repository.claim_run_and_start_attempt(
                run_key=f"{marker}-scheduled-{index}",
                run_kind="SCHEDULED",
                data_kind=DataKind.ADJ_FACTOR.value,
                target_trade_date=target,
                scope_fingerprint=marker,
                flow_run_id=f"{marker}-scheduled-flow-{index}",
                provider_code="memory",
                page_limit=6000,
                schedule_slug="daily-quote-sync",
                scheduled_for=datetime(2026, 7, 27, 9, 0),
            )
            backfill = repository.claim_run_and_start_attempt(
                run_key=f"{marker}-backfill-{index}",
                run_kind="BACKFILL",
                data_kind=DataKind.DAILY_QUOTE.value,
                target_trade_date=target,
                scope_fingerprint=marker,
                flow_run_id=f"{marker}-backfill-flow-{index}",
                provider_code="memory",
                page_limit=6000,
                backfill_batch_id=f"{marker}-batch-{index}",
            )
            run_ids.extend((scheduled.run_id, backfill.run_id))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = tuple(
                    pool.submit(
                        repository.publish_success,
                        claim,
                        SyncCounts(received_count=1, valid_count=1),
                    )
                    for claim in (scheduled, backfill)
                )
                for future in futures:
                    future.result()
        with factory() as session:
            successful_runs = session.scalar(
                select(func.count())
                .select_from(MarketDataSyncRun)
                .where(
                    MarketDataSyncRun.run_id.in_(run_ids),
                    MarketDataSyncRun.status == "SUCCEEDED",
                )
            )
            same_key_dup = session.scalar(
                select(func.count())
                .select_from(MarketDataSyncRun)
                .where(MarketDataSyncRun.run_key == f"{marker}-scheduled-0")
            )
        assert successful_runs == 20
        assert same_key_dup == 1
    finally:
        _cleanup_runs(factory, run_ids)
        engine.dispose()


@pytest.mark.mysql
def test_empty_and_003_migration_paths_repeat_upgrade_and_development_downgrade() -> None:
    source_url = make_url(_mysql_url())
    provided_database = os.getenv("LUCKING_MIGRATION_TEST_DATABASE")
    database_name = provided_database or f"lucking_migration_{uuid4().hex[:12]}"
    if not database_name.replace("_", "").isalnum():
        raise ValueError("临时迁移数据库名称非法")
    admin = create_engine(source_url.set(database="mysql"), isolation_level="AUTOCOMMIT")
    if provided_database is None:
        try:
            with admin.begin() as connection:
                connection.execute(
                    text(
                        f"CREATE DATABASE `{database_name}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                    )
                )
        except Exception as exc:
            admin.dispose()
            pytest.skip(f"测试账号无临时数据库权限：{type(exc).__name__}")
    temporary_url = source_url.set(database=database_name)
    config = Config("alembic.ini")
    try:
        with patch.dict(
            os.environ,
            {"DATABASE_URL": temporary_url.render_as_string(hide_password=False)},
        ):
            command.upgrade(config, "head")
            temporary_engine = create_engine(temporary_url)
            assert {table.name for table in TABLES} <= set(
                inspect(temporary_engine).get_table_names()
            )
            temporary_engine.dispose()
            command.upgrade(config, "head")
            command.downgrade(config, "003")
            temporary_engine = create_engine(temporary_url)
            names_at_003 = set(inspect(temporary_engine).get_table_names())
            temporary_engine.dispose()
            assert "broker_recommendation_sync_run" in names_at_003
            assert not {table.name for table in TABLES} & names_at_003
            command.upgrade(config, "004")
            command.upgrade(config, "head")
    finally:
        if provided_database is None:
            with admin.begin() as connection:
                connection.execute(text(f"DROP DATABASE `{database_name}`"))
        admin.dispose()


def _cleanup_runs(factory: sessionmaker[Session], run_ids: list[str]) -> None:
    with factory.begin() as session:
        attempt_ids = select(MarketDataSyncAttempt.attempt_id).where(
            MarketDataSyncAttempt.run_id.in_(run_ids)
        )
        session.execute(
            delete(MarketDataSyncIssue).where(
                MarketDataSyncIssue.attempt_id.in_(attempt_ids)
            )
        )
        session.execute(
            update(MarketDataSyncRun)
            .where(MarketDataSyncRun.run_id.in_(run_ids))
            .values(successful_attempt_id=None)
        )
        session.execute(
            delete(MarketDataSyncAttempt).where(MarketDataSyncAttempt.run_id.in_(run_ids))
        )
        session.execute(delete(MarketDataSyncRun).where(MarketDataSyncRun.run_id.in_(run_ids)))


def _mysql_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    if os.getenv("LUCKING_USE_LOCAL_MYSQL_TESTS") == "1":
        return Settings().database_url
    pytest.skip("未配置 TEST_DATABASE_URL")


# ---------------------------------------------------------------------------
# 失败演练（T039）：限流/超时/空响应/冲突/ClickHouse 不可达不破坏已有数据
# ---------------------------------------------------------------------------


def _marker_date() -> date:
    """回补目标日：2024-01-01 起唯一日期（历史数据尚未回补，无冲突）。"""
    return date(2024, 1, 1) + timedelta(days=int(uuid4().hex[:5], 16) % 500)


class FlakyDailyQuoteProvider(MemoryDailyQuoteProvider):
    """首次调用失败一次（限流），之后恢复正常。"""

    def __init__(self, *, failure_category: str = "PROVIDER_RATE_LIMITED") -> None:
        super().__init__()
        self.failure_category = failure_category
        self.fail_next = 1

    def fetch_daily_quotes(self, request: object, *, deadline: float) -> object:
        from lucking.ports.market_data_common import (
            ProviderAuthenticationError,
            ProviderRateLimitedError,
        )

        if self.fail_next > 0:
            self.fail_next -= 1
            error_cls = (
                ProviderRateLimitedError
                if self.failure_category == "PROVIDER_RATE_LIMITED"
                else ProviderAuthenticationError
            )
            raise error_cls("memory", "演练注入失败")
        return super().fetch_daily_quotes(request, deadline=deadline)  # type: ignore[arg-type]


@pytest.fixture
def failure_env(
    sqlite_session_factory: sessionmaker[Session],
) -> tuple[ClickHouseClient, MarketDataService, sessionmaker[Session], date]:
    settings = Settings()
    client = _build_clickhouse_client(settings)
    try:
        client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse 不可达：{type(exc).__name__}")
    migrate(settings)
    now = datetime.now(UTC).replace(tzinfo=None)
    with sqlite_session_factory.begin() as session:
        for index in range(_STOCK_COUNT):
            stock_id = f"drill-{index:04d}"
            session.add(
                StockCurrent(
                    stock_id=stock_id,
                    market_code="CN-S",
                    venue_code="XSHG",
                    security_code=f"{index + 1:06d}",
                    display_name=f"演练股票{index}",
                    currency_code="CNY",
                    listing_status="ACTIVE",
                    listed_on=date(2020, 1, 1),
                    delisted_on=None,
                    last_seen_run_id=str(uuid4()),
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                StockProviderMapping(
                    provider_code="memory",
                    provider_security_id=f"{index + 1:06d}.SH",
                    stock_id=stock_id,
                    last_seen_run_id=str(uuid4()),
                    last_seen_at=now,
                    created_at=now,
                )
            )
    repository = SqlAlchemyMarketDataRepository(sqlite_session_factory)
    service = MarketDataService(
        {
            DataKind.DAILY_QUOTE: MemoryDailyQuoteProvider(),
            DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
            DataKind.DAILY_BASIC: MemoryDailyBasicProvider(),
        },
        repository,
        MarketDataClickHouseRepository(client),
        sqlite_session_factory,
    )
    return client, service, sqlite_session_factory, _marker_date()


def _backfill(service: MarketDataService, data_kind: DataKind, target: date) -> object:
    return service.sync(
        BackfillMarketDataCommand(
            data_kind=data_kind,
            target_trade_date=target,
            backfill_batch_id=f"drill-{uuid4().hex[:8]}",
            flow_run_id=str(uuid4()),
        )
    )


@pytest.mark.mysql
def test_rate_limited_sync_fails_without_touching_existing_data(
    failure_env: tuple[ClickHouseClient, MarketDataService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = failure_env
    table = f"{client.database}.daily_quote"
    try:
        failing = _build_drill_service(factory, client, FlakyDailyQuoteProvider())
        with pytest.raises(Exception) as excinfo:
            _backfill(failing, DataKind.DAILY_QUOTE, target)
        assert getattr(excinfo.value, "category", "") == "PROVIDER_RATE_LIMITED"
        # 失败后 ClickHouse 无任何写入（不可见半批），MySQL run 保持 FAILED
        assert client.execute(
            f"SELECT count() FROM {table} WHERE trade_date = '{target.isoformat()}'"
        )[0]["count()"] == 0
        runs = failing.list_runs(data_kind=DataKind.DAILY_QUOTE, status="FAILED")
        assert len(runs) == 1
        # 修复后重试：行集与成功执行一致
        result = _backfill(failing, DataKind.DAILY_QUOTE, target)
        assert result.status is SyncStatus.SUCCEEDED
        assert result.added_count == _STOCK_COUNT
    finally:
        client.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_clickhouse_unreachable_keeps_run_non_succeeded_and_retry_converges(
    failure_env: tuple[ClickHouseClient, MarketDataService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = failure_env
    table = f"{client.database}.adj_factor"
    try:
        from unittest.mock import patch as mock_patch

        with mock_patch(
            "lucking.repositories.market_data_clickhouse.ClickHouseClient.insert_rows",
            side_effect=RuntimeError("ClickHouse 不可达"),
        ):
            with pytest.raises(RuntimeError):
                _backfill(service, DataKind.ADJ_FACTOR, target)
        runs = service.list_runs(data_kind=DataKind.ADJ_FACTOR)
        assert runs and runs[0].status == "FAILED"
        assert client.execute(
            f"SELECT count() FROM {table} WHERE trade_date = '{target.isoformat()}'"
        )[0]["count()"] == 0
        # 恢复后同一批次键重试收敛
        result = service.sync(
            BackfillMarketDataCommand(
                data_kind=DataKind.ADJ_FACTOR,
                target_trade_date=target,
                backfill_batch_id="drill-converge",
                flow_run_id=str(uuid4()),
            )
        )
        assert result.status is SyncStatus.SUCCEEDED
        assert result.added_count == _STOCK_COUNT
    finally:
        client.execute_ddl(
            f"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' "
            "SETTINGS mutations_sync = 1"
        )


@pytest.mark.mysql
def test_nfr009_daily_failure_does_not_block_other_kinds(
    failure_env: tuple[ClickHouseClient, MarketDataService, sessionmaker[Session], date],
) -> None:
    client, service, factory, target = failure_env
    daily_table = f"{client.database}.daily_quote"
    basic_table = f"{client.database}.daily_basic"
    try:
        failing = _build_drill_service(factory, client, FlakyDailyQuoteProvider())
        from lucking.ports.market_data_common import ProviderRateLimitedError

        with pytest.raises(ProviderRateLimitedError):
            _backfill(failing, DataKind.DAILY_QUOTE, target)
        # 复权因子与基本面仍独立成功（互不阻塞、互不回滚）
        adj = _backfill(service, DataKind.ADJ_FACTOR, target)
        basic = _backfill(service, DataKind.DAILY_BASIC, target)
        assert adj.status is SyncStatus.SUCCEEDED
        assert basic.status is SyncStatus.SUCCEEDED
        assert client.execute(
            f"SELECT count() FROM {client.database}.adj_factor "
            f"WHERE trade_date = '{target.isoformat()}'"
        )[0]["count()"] == _STOCK_COUNT
        assert client.execute(
            f"SELECT count() FROM {basic_table} WHERE trade_date = '{target.isoformat()}'"
        )[0]["count()"] == _STOCK_COUNT
        assert client.execute(
            f"SELECT count() FROM {daily_table} WHERE trade_date = '{target.isoformat()}'"
        )[0]["count()"] == 0
    finally:
        for table in (daily_table, basic_table, f"{client.database}.adj_factor"):
            client.execute_ddl(
                f"ALTER TABLE {table} DELETE WHERE trade_date = '{target.isoformat()}' "
                "SETTINGS mutations_sync = 1"
            )


def _build_drill_service(
    factory: sessionmaker[Session],
    client: ClickHouseClient,
    daily_quote_provider: MemoryDailyQuoteProvider,
) -> MarketDataService:
    repository = SqlAlchemyMarketDataRepository(factory)
    return MarketDataService(
        {
            DataKind.DAILY_QUOTE: daily_quote_provider,
            DataKind.ADJ_FACTOR: MemoryAdjFactorProvider(),
            DataKind.DAILY_BASIC: MemoryDailyBasicProvider(),
        },
        repository,
        MarketDataClickHouseRepository(client),
        factory,
    )


def _build_clickhouse_client(settings: Settings) -> ClickHouseClient:
    password = (
        settings.clickhouse_password.get_secret_value()
        if settings.clickhouse_password is not None
        else None
    )
    return ClickHouseClient(
        settings.clickhouse_host,
        settings.clickhouse_port,
        settings.clickhouse_database,
        user=settings.clickhouse_user,
        password=password,
    )
