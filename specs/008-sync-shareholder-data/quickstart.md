# 快速验证指南：股东数据交易日同步（008-sync-shareholder-data）

> 端到端验证与排障指南；实现细节见契约与 tasks.md。
> 相关：`contracts/tushare-shareholder-data.md`、
> `contracts/shareholder-data-provider.md`、
> `contracts/shareholder-data-service.md`、`contracts/prefect-flow.md`、
> `data-model.md`、`research.md`。

## 1. 前置条件

- `.env` 新增配置块（沿用项目前缀模式）：

```dotenv
TUSHARE_TOKEN=<部署账户token>
SHAREHOLDER_DATA_PROVIDER_CODE=tushare
SHAREHOLDER_DATA_TIMEZONE=Asia/Shanghai
SHAREHOLDER_DATA_LOG_DIR=logs/shareholder_data
SHAREHOLDER_DATA_LOG_FILENAME=shareholder_data.jsonl
SHAREHOLDER_DATA_FETCH_DEADLINE_SECONDS=1500
SHAREHOLDER_DATA_RUN_LEASE_SECONDS=2100
SHAREHOLDER_DATA_PAGE_LIMIT=6000
SHAREHOLDER_DATA_RATE_LIMIT_PER_MINUTE=400
SHAREHOLDER_DATA_RATE_LIMITER=redis   # 账户级共享预算（三接口合计），redis 或 process
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_PASSWORD=<compose 的 redis 密码>
```

- 部署账户积分满足三个接口的档位要求（`top10_*` 需 2000+ 积分，
  `stk_holdernumber` 需 600+；用户显式指定按 400 次/分钟保守执行，
  **账户级共享预算：三个接口的请求合计 ≤ 400 次/分钟**，由 Redis
  分布式节流器跨进程保证，Redis 不可达时降级进程级限流）；
  Token 为 SecretStr 延迟读取，不得进入日志或版本控制（宪章 IV）。
- 依赖既有功能已就绪：003 股票列表已同步（`stock_current`/
  `stock_provider_mapping` 含 tushare 映射）、005 交易日历已同步。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
uv run alembic upgrade head        # 本功能无新迁移，验证既有迁移无异常
uv run python -m lucking.clickhouse migrate   # 创建 shareholder_holding / shareholder_count 表
uv run prefect deployment apply prefect.yaml
```

预期：ClickHouse 迁移创建两张业务表（含中文表/列注释）；
`prefect deployment ls` 可见 6 个中文部署名：增量
`前十大股东交易日同步`/`前十大流通股东交易日同步`/`股东人数交易日同步`，
回补 `前十大股东历史回补`/`前十大流通股东历史回补`/`股东人数历史回补`；
MySQL 无新增表（身份/审计全复用）。

## 3. 核心验证：增量同步（每接口独立 Flow）

```bash
# 三个接口各自触发（Cron 已错峰 17:00 / 17:05 / 17:10）
uv run prefect deployment run "前十大股东交易日同步/前十大股东交易日同步" --param scheduled_at=<最近一个交易日17:00的UTC ISO8601>
uv run prefect deployment run "前十大流通股东交易日同步/前十大流通股东交易日同步" --param scheduled_at=<最近一个交易日17:00的UTC ISO8601>
uv run prefect deployment run "股东人数交易日同步/股东人数交易日同步" --param scheduled_at=<最近一个交易日17:00的UTC ISO8601>
```

预期（`logs/shareholder_data/shareholder_data.jsonl` 与 MySQL 审计表，
三个接口各一条独立 run/终态）：

- 每接口窗口 =（本接口 `max(ann_date)` 水位, 昨日]，按公告日提取；
  无新公告日时直接 `SUCCEEDED` 且不调用来源；run 终态 `SUCCEEDED`，
  计数齐全；
- `shareholder_holding` 按 `(end_date, stock_id, holder_kind, holder_name)`
  可查（`SELECT ... FINAL`；`holder_kind` 区分两个 top10 接口），
  `shareholder_count` 按 `(end_date, stock_id)` 可查；披露高峰日返回
  多页时 `page_count > 1` 且 `continuation_exhausted=True`；
- 未知 ts_code 的记录被隔离（`invalid_count` + issue
  `UNKNOWN_STOCK_IDENTITY`），不阻断整批；
- **按接口水位**：先触发任一 top10 接口、再触发另一个，后者的窗口
  仍覆盖当日公告（`data_kind` 各自独立，不跳日）；
- **故障隔离**：模拟某接口来源失败（如临时断网），其余两个接口的
  run 仍 `SUCCEEDED`；修复后单独重跑失败接口即可。

重复执行同一 `scheduled_at`：run_key 唯一（含 `data_kind` 维度），
第二次不重复处理（幂等）；无新公告的交易日不重复调用来源
（对比请求计数日志）。

## 4. 初始化回补与幂等（每接口独立 Flow）

```bash
# 三个接口各自回补；运维约定：串行执行（不要并行，避免账户限流叠加）
uv run prefect deployment run "前十大股东历史回补/前十大股东历史回补" \
  --param start_date=20240101 --param end_date=<最近交易日> --param backfill_batch_id=init-top10-2026-08-05
uv run prefect deployment run "前十大流通股东历史回补/前十大流通股东历史回补" \
  --param start_date=20240101 --param end_date=<最近交易日> --param backfill_batch_id=init-top10float-2026-08-05
uv run prefect deployment run "股东人数历史回补/股东人数历史回补" \
  --param start_date=20240101 --param end_date=<最近交易日> --param backfill_batch_id=init-count-2026-08-05
```

预期：

- `前十大股东历史回补`/`前十大流通股东历史回补` 按报告期季度末、
  `股东人数历史回补` 按公告日逐日提取，请求间隔 ≥ 150 毫秒
  （全程 ≤ 400 次/分钟）；`top10_*` 单接口约 90 次请求（秒级完成）、
  `股东人数` 约 630 次（约 2 分钟）；
- 任一接口回补失败不影响其他两个接口的回补进度与终态；
- 再次提交同一 `backfill_batch_id`：已成功日期 SKIP，不重复调用来源
  （检查 Provider 请求计数日志）；失败日期修复后重跑只处理失败日期；
- 回补与增量重叠的日期数据一致（同键替换，无重复）。

## 5. 非交易日

触发任一增量 Flow（如周末的 `scheduled_at`）：该 Flow 终态
`SKIPPED_NOT_TRADING_DAY`，正常结束，不产生失败告警，不写数据；
三个 Flow 各自判定，互不影响。

## 6. 失败与恢复

- **故障隔离**：某接口来源失败（限流/超时/分页异常）只使该接口的 run
  FAILED，其余两个接口照常成功；单独重跑失败接口
  （`deployment run` 或等下一交易日自然窗口覆盖）。
- 限流/超时：Adapter 退避 30/120/300 秒重试 ≤ 3 次，仍失败则 run FAILED，
  issue 类别 `PROVIDER_RATE_LIMITED`/`PROVIDER_TIMEOUT`；已有数据不受影响。
- 分页异常：`has_more=True` 但位置不前进、页摘要重复或超过最大页数 →
  run FAILED（`PROVIDER_RESPONSE_CAPPED`），须报告维护人员
  （按 research 待验证项处理）；不得猜测参数绕过门禁。
- 冲突：非新公告的同键值变化整批失败（`RECORD_CONFLICT`），不得任意
  覆盖；**新公告**（更正公告）的值变化属正常修订，按最新公告更新
  （`updated_count`），不产生告警。
- 中断恢复：租约过期（2100 秒）后 attempt 置 ABANDONED，可重新认领重跑；
  重试归属原计划交易日，不串日；失败日的公告由下一运行的自然窗口覆盖。

## 7. 五分钟排障

1. `uv run prefect flow-run ls` 找最近一次运行，确认触发与参数（6 个
   Deployment 之一）。
2. 查 MySQL：`market_data_sync_run`（run_key/状态，`data_kind` ∈
   `TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/`HOLDER_COUNT` 定位接口）
   → `market_data_sync_attempt`（计数/租约）→ `market_data_sync_issue`
   （类别/脱敏摘要，如 `UNKNOWN_STOCK_IDENTITY`）。
3. 查 `logs/shareholder_data/shareholder_data.jsonl`：错误类别与窗口及时性。
4. 查 ClickHouse：`SELECT count() FROM shareholder_holding WHERE end_date = '<披露期>' FINAL`
   与审计 received/valid/added 核对；抽样
   `SELECT holder_name, hold_amount, hold_ratio FROM shareholder_holding WHERE stock_id='<id>' AND end_date='<披露期>' AND holder_kind='TOP10' FINAL`；
   `SELECT holder_num FROM shareholder_count WHERE stock_id='<id>' AND end_date='<截止日>' FINAL`。
5. 按状态判定：SUCCEEDED 完成；FAILED 按 issue 类别修复（限流等待 /
   冲突排查 / 分页异常上报）；SKIPPED_NOT_TRADING_DAY 属正常。

## 8. 上线门禁（部署前实测）

1. ✅ 已实测（2026-08-05）：三个接口无需 ts_code 全市场查询、
   `limit` 参数生效、按公告日过滤可用、昨日（20260804）无新公告
   属正常披露节奏（research 待验证项 1）。
2. ✅ 已实测（2026-08-05）：字段全集 9+9+4 与文档逐名一致、数值形态
   确认（research 待验证项 2）。
3. ✅ 已实测（2026-08-05）：单次上限 6,000 行 + `has_more/offset` 分页
   有效；`stk_holdernumber` 文档 3,000 上限过时（research 待验证项 3）。
4. 实测 400 次/分钟限流档位的拒绝形态与错误码，校准节流参数与
   `PROVIDER_RATE_LIMITED` 重试退避。
5. 实测 003 `stock_provider_mapping` 对三个接口返回 ts_code 全集的
   覆盖度（参考 007 实测结论：预期覆盖 0 缺失）。
6. 实测各接口 2024-01-01 起回补的请求量与耗时（`top10_*` ~90 次
   ≈ 秒级、`股东人数` ~630 次 ≈ 2 分钟；3 Flow 拆分后按接口独立回补）。
7. 实测更正公告重复披露形态：同一业务身份多个 ann_date 的记录按最新
   公告值收敛且不触发冲突。
8. 实测三接口错峰调度（17:00/17:05/17:10）串行执行无叠加：
   并发触发时审计 run 各自独立、请求计数合计不超过账户限流。
