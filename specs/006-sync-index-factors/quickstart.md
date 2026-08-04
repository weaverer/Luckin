# 快速验证指南：指数技术因子同步（006-sync-index-factors）

> 端到端验证与排障指南；实现细节见契约与 tasks.md。
> 相关：`contracts/tushare-index-factor.md`、`contracts/index-factor-provider.md`、
> `contracts/index-factor-service.md`、`contracts/prefect-flow.md`、`data-model.md`。

## 1. 前置条件

- `.env` 新增配置块（沿用项目前缀模式）：

```dotenv
TUSHARE_TOKEN=<部署账户token>
INDEX_FACTOR_PROVIDER_CODE=tushare
INDEX_FACTOR_TIMEZONE=Asia/Shanghai
INDEX_FACTOR_LOG_DIR=logs/index_factor
INDEX_FACTOR_LOG_FILENAME=index_factor.jsonl
INDEX_FACTOR_FETCH_DEADLINE_SECONDS=1500
INDEX_FACTOR_RUN_LEASE_SECONDS=2100
INDEX_FACTOR_PAGE_LIMIT=8000
INDEX_FACTOR_RATE_LIMIT_PER_MINUTE=30
```

- 部署账户积分 ≥ 5000（每分钟 30 次档位）；Token 为 SecretStr 延迟读取，
  不得进入日志或版本控制（宪章 IV）。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
uv run alembic upgrade head
uv run python -m lucking.clickhouse migrate
uv run prefect deployment apply prefect.yaml
```

预期：MySQL 迁移包含 `index_current`/`index_provider_mapping` 两表；
ClickHouse 迁移创建 `index_factor` 表（按月分区）；`prefect deployment ls`
可见 `index-factor-sync/daily-17` 与 `index-factor-backfill/backfill`。

## 3. 核心验证：增量同步

```bash
uv run prefect deployment run "index-factor-sync/daily-17" --param scheduled_at=<最近一个交易日17:00的UTC ISO8601>
```

预期（`logs/index_factor/index_factor.jsonl` 与 MySQL 审计表）：

- 返回最近一个交易日全部指数的因子记录（received = 指数总数，远小于 8,000）；
- run 终态 `SUCCEEDED`，计数齐全；`index_factor` 表按
  `(trade_date, index_id)` 可查（`SELECT ... FINAL`）；
- 首次运行后 `index_current`/`index_provider_mapping` 自动注册全部指数。

重复执行同一 `scheduled_at`：run_key 唯一，第二次不重复处理（幂等）。

## 4. 初始化回补与幂等

```bash
uv run prefect deployment run "index-factor-backfill/backfill" \
  --param start_date=20240101 --param end_date=<最近交易日> --param backfill_batch_id=init-2026-08-02
```

预期：

- 从 2024-01-01 逐交易日回补，每个交易日独立终态，请求间隔 ≥ 2 秒
  （全程 ≤ 30 次/分钟）；全部完成约 20~30 分钟；
- 再次提交同一 `backfill_batch_id`：已成功日期 SKIP，不重复调用来源
  （检查 Provider 请求计数日志）；失败日期修复后重跑只处理失败日期；
- 回补与增量重叠的日期数据一致（同键替换，无重复）。

## 5. 非交易日

触发 `index-factor-sync`（如周末的 `scheduled_at`）：终态
`SKIPPED_NOT_TRADING_DAY`，正常结束，不产生失败告警，不写因子数据。

## 6. 失败与恢复

- 限流/超时：Adapter 退避 30/120/300 秒重试 ≤ 3 次，仍失败则 run FAILED，
  issue 类别 `PROVIDER_RATE_LIMITED`/`PROVIDER_TIMEOUT`；已有数据不受影响。
- 触顶：若某日返回行数 == 8,000，run FAILED（`PROVIDER_RESPONSE_CAPPED`），
  禁止猜测参数续取；须报告维护人员按 research 待验证项 1 处理。
- 冲突：同键字段冲突整批失败（`RECORD_CONFLICT`），不得任意覆盖。
- 中断恢复：租约过期（2100 秒）后 attempt 置 ABANDONED，可重新认领重跑；
  重试归属原计划交易日，不串日。

## 7. 五分钟排障

1. `uv run prefect flow-run ls` 找最近一次运行，确认触发与参数。
2. 查 MySQL：`market_data_sync_run`（run_key/状态）→ `market_data_sync_attempt`
   （计数/租约）→ `market_data_sync_issue`（类别/脱敏摘要）。
3. 查 `logs/index_factor/index_factor.jsonl`：错误类别与窗口及时性。
4. 查 ClickHouse：`SELECT count() FROM index_factor WHERE trade_date = '<目标日>' FINAL`
   与审计 received/valid/added 核对。
5. 按状态判定：SUCCEEDED 完成；FAILED 按 issue 类别修复（限流等待 /
   冲突排查 / 触顶上报）；SKIPPED_NOT_TRADING_DAY 属正常。

## 8. 上线门禁（部署前实测）

1. 部署账户实测 `trade_date` 单次全量请求行数 << 8,000（触顶即判定不兼容）。
2. 实测返回字段全集与契约 §3 一致（78 因子 + 10 基础行情，`_bfq` 规律成立）。
3. 实测 30 次/分钟限流行为，校准节流参数与错误映射。
4. 实测 17:00 时当日数据已完整更新。
5. 实测指数 ts_code 后缀全集（.SH/.SZ/.CSI/.SI 之外是否有新后缀）。
6. 实测 2024-01-01 起回补总耗时在窗口内（约 20~30 分钟）。
