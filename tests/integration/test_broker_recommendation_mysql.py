import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from lucking.config import Settings
from lucking.models.broker_recommendation import (
    BrokerRecommendation,
    BrokerRecommendationSyncAttempt,
    BrokerRecommendationSyncIssue,
    BrokerRecommendationSyncRun,
)
from lucking.models.stock_list import StockCurrent
from lucking.ports.broker_recommendation_provider import VenueCode
from lucking.repositories.broker_recommendation import (
    RecommendationWrite,
    SqlAlchemyBrokerRecommendationRepository,
    SyncCounts,
)

TABLES = (
    BrokerRecommendation.__table__,
    BrokerRecommendationSyncRun.__table__,
    BrokerRecommendationSyncAttempt.__table__,
    BrokerRecommendationSyncIssue.__table__,
)


def test_orm_and_migration_declare_governed_physical_and_business_keys() -> None:
    migration = Path("migrations/versions/003_create_broker_recommendation_tables.py").read_text()
    for table in TABLES:
        assert [column.name for column in table.primary_key.columns] == ["id"]
        assert table.c.id.type.python_type is int
        assert all(column.comment for column in table.columns)
    for comment in (
        "券商月度金股推荐",
        "券商金股同步运行",
        "券商金股同步执行尝试",
        "券商金股同步质量问题",
    ):
        assert comment in migration
    assert "CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP" in migration
    assert "lease_expires_at" in migration


@pytest.mark.mysql
def test_live_mysql_has_bigint_autoincrement_and_chinese_comments() -> None:
    url = _mysql_url()
    engine = create_engine(url)
    try:
        names = set(inspect(engine).get_table_names())
        expected = {table.name for table in TABLES}
        if not expected <= names:
            pytest.skip("测试库尚未 upgrade 到 revision 003")
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
    finally:
        engine.dispose()


@pytest.mark.mysql
def test_ten_scheduled_backfill_races_keep_two_runs_and_one_business_row() -> None:
    engine = create_engine(_mysql_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyBrokerRecommendationRepository(factory)
    marker = uuid4().hex
    stock_id = str(uuid4())
    run_ids: list[str] = []
    target = date(2026, 7, 1)
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory.begin() as session:
        session.add(
            StockCurrent(
                stock_id=stock_id,
                market_code="CN-S",
                venue_code="XSHG",
                security_code=marker[:8],
                display_name="并发测试股票",
                currency_code="CNY",
                listing_status="ACTIVE",
                listed_on=target,
                delisted_on=None,
                last_seen_run_id=str(uuid4()),
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    try:
        for index in range(10):
            scheduled = repository.claim_run_and_start_attempt(
                run_key=f"{marker}-scheduled-{index}",
                run_kind="SCHEDULED",
                target_month=target,
                scope_fingerprint=marker,
                flow_run_id=f"{marker}-scheduled-flow-{index}",
                provider_code="memory",
                page_limit=1000,
                schedule_slug="monthly",
                scheduled_for=datetime(2026, 7, 3, 4, 0),
            )
            backfill = repository.claim_run_and_start_attempt(
                run_key=f"{marker}-backfill-{index}",
                run_kind="BACKFILL",
                target_month=target,
                scope_fingerprint=marker,
                flow_run_id=f"{marker}-backfill-flow-{index}",
                provider_code="memory",
                page_limit=1000,
                backfill_batch_id=f"{marker}-batch-{index}",
            )
            run_ids.extend((scheduled.run_id, backfill.run_id))
            record = RecommendationWrite(
                target,
                f"{marker}-broker-{index}",
                stock_id,
                VenueCode.SHANGHAI,
                marker[:8],
                "并发测试股票",
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = tuple(
                    pool.submit(
                        repository.publish_success,
                        claim,
                        (record,),
                        SyncCounts(received_count=1, valid_count=1),
                    )
                    for claim in (scheduled, backfill)
                )
                for future in futures:
                    future.result()
        with factory() as session:
            recommendation_count = session.scalar(
                select(func.count())
                .select_from(BrokerRecommendation)
                .where(BrokerRecommendation.stock_id == stock_id)
            )
            successful_runs = session.scalar(
                select(func.count())
                .select_from(BrokerRecommendationSyncRun)
                .where(
                    BrokerRecommendationSyncRun.run_id.in_(run_ids),
                    BrokerRecommendationSyncRun.status == "SUCCEEDED",
                )
            )
        assert recommendation_count == 10
        assert successful_runs == 20
    finally:
        with factory.begin() as session:
            session.execute(
                delete(BrokerRecommendation).where(BrokerRecommendation.stock_id == stock_id)
            )
            session.execute(
                update(BrokerRecommendationSyncRun)
                .where(BrokerRecommendationSyncRun.run_id.in_(run_ids))
                .values(successful_attempt_id=None)
            )
            session.execute(
                delete(BrokerRecommendationSyncAttempt).where(
                    BrokerRecommendationSyncAttempt.run_id.in_(run_ids)
                )
            )
            session.execute(
                delete(BrokerRecommendationSyncRun).where(
                    BrokerRecommendationSyncRun.run_id.in_(run_ids)
                )
            )
            session.execute(delete(StockCurrent).where(StockCurrent.stock_id == stock_id))
        engine.dispose()


@pytest.mark.mysql
def test_database_utc_lease_is_2100_seconds_and_expired_attempt_retries_same_run() -> None:
    engine = create_engine(_mysql_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyBrokerRecommendationRepository(factory)
    marker = uuid4().hex
    run_ids: list[str] = []
    try:
        claim = repository.claim_run_and_start_attempt(
            run_key=f"{marker}-lease",
            run_kind="BACKFILL",
            target_month=date(2026, 6, 1),
            scope_fingerprint=marker,
            flow_run_id=f"{marker}-flow-1",
            provider_code="memory",
            page_limit=1000,
            backfill_batch_id=marker,
        )
        run_ids.append(claim.run_id)
        with factory() as session:
            attempt = session.scalar(
                select(BrokerRecommendationSyncAttempt).where(
                    BrokerRecommendationSyncAttempt.attempt_id == claim.attempt_id
                )
            )
            assert attempt is not None
            assert (attempt.lease_expires_at - attempt.started_at).total_seconds() == 2100
        current = repository.resolve_backfill_month(
            backfill_batch_id=marker, target_month=date(2026, 6, 1)
        )
        assert current.action.value == "IN_PROGRESS"
        with factory.begin() as session:
            session.execute(
                update(BrokerRecommendationSyncAttempt)
                .where(BrokerRecommendationSyncAttempt.attempt_id == claim.attempt_id)
                .values(lease_expires_at=text("UTC_TIMESTAMP(6) - INTERVAL 1 SECOND"))
            )
        expired = repository.resolve_backfill_month(
            backfill_batch_id=marker, target_month=date(2026, 6, 1)
        )
        assert expired.action.value == "RETRY"
        retry = repository.claim_run_and_start_attempt(
            run_key="",
            run_kind="",
            target_month=date.min,
            scope_fingerprint="",
            flow_run_id=f"{marker}-flow-2",
            provider_code="memory",
            page_limit=1000,
            retry_run_id=claim.run_id,
        )
        assert retry.run_id == claim.run_id
        assert retry.attempt_no == 2
        with factory() as session:
            old_status = session.scalar(
                select(BrokerRecommendationSyncAttempt.status).where(
                    BrokerRecommendationSyncAttempt.attempt_id == claim.attempt_id
                )
            )
            assert old_status == "ABANDONED"
    finally:
        with factory.begin() as session:
            attempt_ids = select(BrokerRecommendationSyncAttempt.attempt_id).where(
                BrokerRecommendationSyncAttempt.run_id.in_(run_ids)
            )
            session.execute(
                delete(BrokerRecommendationSyncIssue).where(
                    BrokerRecommendationSyncIssue.attempt_id.in_(attempt_ids)
                )
            )
            session.execute(
                delete(BrokerRecommendationSyncAttempt).where(
                    BrokerRecommendationSyncAttempt.run_id.in_(run_ids)
                )
            )
            session.execute(
                delete(BrokerRecommendationSyncRun).where(
                    BrokerRecommendationSyncRun.run_id.in_(run_ids)
                )
            )
        engine.dispose()


@pytest.mark.mysql
def test_empty_and_002_migration_paths_repeat_upgrade_and_development_downgrade() -> None:
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
            command.downgrade(config, "002")
            temporary_engine = create_engine(temporary_url)
            names_at_002 = set(inspect(temporary_engine).get_table_names())
            temporary_engine.dispose()
            assert "stock_current" in names_at_002
            assert not {table.name for table in TABLES} & names_at_002
            command.upgrade(config, "003")
            command.upgrade(config, "head")
    finally:
        if provided_database is None:
            with admin.begin() as connection:
                connection.execute(text(f"DROP DATABASE `{database_name}`"))
        admin.dispose()


def _mysql_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    if os.getenv("LUCKING_USE_LOCAL_MYSQL_TESTS") == "1":
        return Settings().database_url
    pytest.skip("未配置 TEST_DATABASE_URL")
