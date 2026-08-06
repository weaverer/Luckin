# 快速验证：A股行情数据交易日同步

本指南用于实现完成后的端到端验证，不替代 `tasks.md` 或自动化测试。

## 1. 前置条件

- WSL2 Ubuntu、Docker Desktop 和 `uv` 可用。
- 已按 `README.md` 创建本机 `.env`。
- 交易日历同步已完成，`trading_calendar` 含 CN-S 日历；
  `stock_current` 和 `stock_provider_mapping` 能解析行情 fixture 中的全部股票。
- Tushare 部署账户有权调用 `daily` / `adj_factor` / `daily_basic` /
  `stk_week_month_adj` 四个接口（`adj_factor`、`daily_basic`、`stk_week_month_adj`
  需最低 2,000 积分），并具有有效 Token。
- 已阅读 [数据模型](data-model.md)、[Provider 契约](contracts/daily-quote-provider.md)、
  [Service 契约](contracts/market-data-service.md) 和
  [Tushare 契约](contracts/tushare-market-data.md)。

本机 `.env` 增加：

```dotenv
DAILY_QUOTE_PROVIDER=tushare
ADJ_FACTOR_PROVIDER=tushare
DAILY_BASIC_PROVIDER=tushare
KLINE_PROVIDER=tushare
MARKET_DATA_TIMEZONE=Asia/Shanghai
MARKET_DATA_LOG_DIR=logs
MARKET_DATA_LOG_FILENAME=market-data-sync.jsonl
MARKET_DATA_FETCH_DEADLINE_SECONDS=1500
MARKET_DATA_RUN_LEASE_SECONDS=2100
MARKET_DATA_PAGE_LIMIT=6000
MARKET_DATA_MAX_PAGES=10
TUSHARE_TOKEN=replace-with-local-secret
TUSHARE_API_URL=https://api.tushare.pro
CLICKHOUSE_HOST=127.0.0.1
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=lucking
```

真实 Token 不得写入 `.env.example`、测试 fixture、命令输出、日志或数据库。
ClickHouse 由现有 Docker Compose 承载，本功能直接使用，不新增基础设施。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
docker compose ps
```

预期 MySQL、ClickHouse 和 Prefect Server 健康，所有宿主端口仅绑定 `127.0.0.1`。
应用 Redis 虽由 Compose 启动，但行情同步应用代码不使用它。

加载本机环境并执行迁移（MySQL 审计表与 ClickHouse 业务表）：

```bash
set -a
source .env
uv run alembic upgrade head
uv run python -m lucking.clickhouse migrate
```

## 3. 核心验证：四个接口单日同步

以最近一个已完成的交易日为目标，逐个 Deployment 验证。

### 3.1 复权因子（ADJ_FACTOR）

```bash
uv run prefect deployment run "market-data-sync/复权因子同步" --param data_kind=ADJ_FACTOR
```

预期：运行成功；`adj_factor` 表出现该交易日全市场因子记录；
重复执行同一计划运行不产生第二条记录。

### 3.2 日线（DAILY_QUOTE）

```bash
uv run prefect deployment run "market-data-sync/日线行情同步" --param data_kind=DAILY_QUOTE
```

预期：运行成功；`daily_quote` 表出现该交易日全市场未复权行情；
停牌股票无记录；重复执行不产生重复且业务字段不变。

### 3.3 基本面指标（DAILY_BASIC）

```bash
uv run prefect deployment run "market-data-sync/每日基本面同步" --param data_kind=DAILY_BASIC
```

预期：运行成功；`daily_basic` 表出现该交易日全市场指标；
亏损公司 PE/PB 等字段为 NULL；`limit_status` 正确保存。

### 3.4 周/月K线（WEEKLY_KLINE / MONTHLY_KLINE）

周线与月线为两个独立 Deployment、两个独立数据模型：

```bash
uv run prefect deployment run "market-data-sync/周K线同步" --param data_kind=WEEKLY_KLINE
uv run prefect deployment run "market-data-sync/月K线同步" --param data_kind=MONTHLY_KLINE
```

预期：两个运行均成功；`weekly_kline` 表出现截至该交易日的最新周线、
`monthly_kline` 表出现最新月线；两表互不串扰；同一周期重复同步
只保留一行（同键替换）；未复权价格与量额非空。

## 4. 核心验证：回补与幂等

```bash
uv run prefect deployment run "market-data-backfill/行情数据历史回补" \
  --param data_kind=DAILY_QUOTE \
  --param start_date=2024-01-02 --param end_date=2024-01-10 \
  --param backfill_batch_id=demo-20240801
```

预期：

- 区间内每个交易日逐日形成独立终态；`daily_quote` 覆盖全部交易日。
- 重复提交相同批次键：已成功日期跳过、不重复调用来源、不产生重复记录。
- 修改批次键重新提交：允许主动刷新同一区间且不产生重复记录
  （ClickHouse 同键替换，同一交易日同一股票只保留最新行）。
- 提交 `start_date=2023-12-29` 或未来日期：整个区间在任何运行创建前被拒绝。

## 5. 非交易日验证

```bash
# 选择一个已知的法定节假日（例如国庆节期间的工作日）
uv run prefect deployment run "market-data-sync/日线行情同步" --param data_kind=DAILY_QUOTE
```

预期：运行成功返回且状态为跳过（不调用来源接口，不产生业务运行）。

## 6. 失败与恢复验证

- 将 `.env` 中的 Token 改为无效值后触发日线同步：
  预期运行失败，`market_data_sync_run` 状态为失败，`market_data_sync_attempt`
  保留计数与安全错误摘要，已有行情数据不被清空或破坏。
- 将 ClickHouse 不可达后触发同步（验证发布语义）：
  预期运行失败且 MySQL 运行保持非成功状态；查询 `daily_quote` 只能看到
  完整批次或上一状态，不存在半批结果。
- 恢复 Token / ClickHouse 后重新触发同一计划：
  预期复用原运行新增一次尝试并成功；ClickHouse 同键替换保证最终行集
  与成功执行一致，不产生重复记录。
- 触发两次相同计划使前一次仍在运行：预期第二次在租约有效期间
  不创建第二 attempt，报告进行中。

## 7. 五分钟排障

1. `prefect flow-run inspect <id>`：查看 Flow 运行状态与日志。
2. 查询 `market_data_sync_run`：按 `data_kind`、目标交易日与状态确认权威结果。
3. 查询 `market_data_sync_attempt`：确认尝试次数、提取计数、重试次数与错误类别。
4. 查询 `market_data_sync_issue`：确认脱敏质量问题样本（哈希与安全摘要）。
5. 查询 ClickHouse `daily_quote` 等业务表：确认目标交易日行数
   （全市场约 5,400 行）与 `updated_at` 版本。
6. 检查 `logs/market-data-sync.jsonl`：确认字段白名单日志与窗口及时性。

一次排障应能回答：目标交易日是什么、哪类数据、是否成功、处理了多少记录、
是否需要重试。

## 8. 窗口及时性

- 复权因子（9:30 启动）：开盘后获取前一交易日因子，单请求快速收敛。
- 日线（17:00 启动）与基本面（17:45 启动）：当日形成终态。
- 周/月线（18:30 启动，日线同步完成之后）：当日形成终态。

## 9. 上线门禁

- 用部署账户或供应商沙箱验证四个接口的权限、积分门槛、频率限制，
  以及 `daily`/`adj_factor`/`daily_basic` 按 `trade_date` 全市场返回与
  `stk_week_month_adj` 按周期返回的行为；验证失败时不得启用对应数据类。
- 完成 2024-01-01 起的五类数据初始化回补（日线、复权因子、基本面、周线、月线），
  并按 §3 验证当日增量链路；验证 ClickHouse 分区、同键替换与单 block 原子性。
- 全部单元、契约、MySQL/ClickHouse 集成、Flow 测试与 `uv run ruff check .`、
  `uv run mypy src`、`uv run pytest`、`uv run pytest -m mysql` 通过。
