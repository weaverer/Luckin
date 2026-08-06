# 快速验证指南：股票技术面因子同步（007-sync-stock-factors）

> 端到端验证与排障指南；实现细节见契约与 tasks.md。
> 相关：`contracts/tushare-stock-factor.md`、`contracts/stock-factor-provider.md`、
> `contracts/stock-factor-service.md`、`contracts/prefect-flow.md`、`data-model.md`。

## 1. 前置条件

- `.env` 新增配置块（沿用项目前缀模式）：

```dotenv
TUSHARE_TOKEN=<部署账户token>
STOCK_FACTOR_PROVIDER_CODE=tushare
STOCK_FACTOR_TIMEZONE=Asia/Shanghai
STOCK_FACTOR_LOG_DIR=logs/stock_factor
STOCK_FACTOR_LOG_FILENAME=stock_factor.jsonl
STOCK_FACTOR_FETCH_DEADLINE_SECONDS=1500
STOCK_FACTOR_RUN_LEASE_SECONDS=2100
STOCK_FACTOR_PAGE_LIMIT=10000
STOCK_FACTOR_RATE_LIMIT_PER_MINUTE=30
```

- 部署账户积分 ≥ 5000（每分钟 30 次档位）；Token 为 SecretStr 延迟读取，
  不得进入日志或版本控制（宪章 IV）。
- 依赖既有功能已就绪：003 股票列表已同步（`stock_current`/
  `stock_provider_mapping` 含 tushare 映射）、005 交易日历已同步。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
uv run alembic upgrade head        # 本功能无新迁移，验证既有迁移无异常
uv run python -m lucking.clickhouse migrate   # 创建 stock_factor 表（按月分区）
uv run prefect deployment apply prefect.yaml
```

预期：ClickHouse 迁移创建 `stock_factor` 宽表（含全部复权变体列）；
`prefect deployment ls` 可见中文部署名
`股票技术面因子交易日同步/股票技术面因子交易日同步` 与 `股票技术面因子历史回补/股票技术面因子历史回补`；
MySQL 无新增表（身份/审计全复用）。

## 3. 核心验证：增量同步

```bash
uv run prefect deployment run "股票技术面因子交易日同步/股票技术面因子交易日同步" --param scheduled_at=<最近一个交易日17:00的UTC ISO8601>
```

预期（`logs/stock_factor/stock_factor.jsonl` 与 MySQL 审计表）：

- 返回最近一个交易日全部 A 股的因子记录（received ≈ A 股总数，远小于
  10,000）；run 终态 `SUCCEEDED`，计数齐全；
- `stock_factor` 表按 `(trade_date, stock_id)` 可查
  （`SELECT ... FINAL`），复权变体列有值；
- 未知 ts_code 的记录被隔离（`invalid_count` + issue
  `UNKNOWN_STOCK_IDENTITY`），不阻断整批。

重复执行同一 `scheduled_at`：run_key 唯一，第二次不重复处理（幂等）。

## 4. 初始化回补与幂等

```bash
uv run prefect deployment run "股票技术面因子历史回补/股票技术面因子历史回补" \
  --param start_date=20240101 --param end_date=<最近交易日> --param backfill_batch_id=init-2026-08-04
```

预期：

- 从 2024-01-01 逐交易日回补，每个交易日独立终态，请求间隔 ≥ 2 秒
  （全程 ≤ 30 次/分钟）；全部完成约 20~30 分钟；
- 再次提交同一 `backfill_batch_id`：已成功日期 SKIP，不重复调用来源
  （检查 Provider 请求计数日志）；失败日期修复后重跑只处理失败日期；
- 回补与增量重叠的日期数据一致（同键替换，无重复）。

## 5. 非交易日

触发增量 Flow（如周末的 `scheduled_at`）：终态 `SKIPPED_NOT_TRADING_DAY`，
正常结束，不产生失败告警，不写因子数据。

## 6. 失败与恢复

- 限流/超时：Adapter 退避 30/120/300 秒重试 ≤ 3 次，仍失败则 run FAILED，
  issue 类别 `PROVIDER_RATE_LIMITED`/`PROVIDER_TIMEOUT`；已有数据不受影响。
- 触顶：若某日返回行数 == 10,000，run FAILED（`PROVIDER_RESPONSE_CAPPED`），
  禁止猜测参数续取；须报告维护人员按 research 待验证项 1 处理。
- 冲突：稳定字段同键冲突整批失败（`RECORD_CONFLICT`），不得任意覆盖；
  复权字段（`_qfq/_hfq`/`adj_factor`）值变化属正常修订，按最新值更新
  （`updated_count`），不产生告警。
- 中断恢复：租约过期（2100 秒）后 attempt 置 ABANDONED，可重新认领重跑；
  重试归属原计划交易日，不串日。

## 7. 五分钟排障

1. `uv run prefect flow-run ls` 找最近一次运行，确认触发与参数。
2. 查 MySQL：`market_data_sync_run`（run_key/状态，`data_kind=STOCK_FACTOR`）
   → `market_data_sync_attempt`（计数/租约）→ `market_data_sync_issue`
   （类别/脱敏摘要，如 `UNKNOWN_STOCK_IDENTITY`）。
3. 查 `logs/stock_factor/stock_factor.jsonl`：错误类别与窗口及时性。
4. 查 ClickHouse：`SELECT count() FROM stock_factor WHERE trade_date = '<目标日>' FINAL`
   与审计 received/valid/added 核对；复权列抽样
   `SELECT close_qfq, ma_qfq_5, adj_factor FROM stock_factor WHERE stock_id='<id>' AND trade_date='<目标日>' FINAL`。
5. 按状态判定：SUCCEEDED 完成；FAILED 按 issue 类别修复（限流等待 /
   冲突排查 / 触顶上报）；SKIPPED_NOT_TRADING_DAY 属正常。

## 8. 上线门禁（部署前实测）

1. 部署账户实测 `trade_date` 单次全量请求行数 << 10,000（触顶即判定不兼容）。
2. 实测返回字段全集与契约 §3 一致：各指标复权变体（`_bfq/_qfq/_hfq`）
   返回规律、`open/high/low/close` 原值并存形态；据此校准
   `STOCK_FACTOR_FIELDS` 白名单与可修订/稳定字段分级。
3. 实测 30 次/分钟限流行为，校准节流参数与错误映射
   （006 已实测同档位，预期复用结论）。
4. 实测 17:00 时当日数据已完整更新。
5. 实测 003 `stock_current`/`stock_provider_mapping` 对返回 ts_code 全集的
   覆盖度（含北交所），确认 `UNKNOWN_STOCK_IDENTITY` 占比符合预期。
6. 实测 2024-01-01 起回补总耗时在窗口内（约 20~30 分钟）。
7. 实测复权字段回溯更新：期间发生除权事件后重复同步某交易日，
   `_qfq/_hfq`/`adj_factor` 按最新值更新且不触发冲突。
