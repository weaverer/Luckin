# 工作流契约：Prefect Flow 与调度（股东数据）

> 编排契约：Flow 入口、参数、调度与运维语义。Flow 只做组装与触发，
> 领域逻辑在 Service 层（`shareholder-data-service.md`）；Flow 不得直接
> 调用供应商或存储细节（宪章 II）。
> **三个接口拆分为三套独立 Flow（增量 + 回补各三个）**：任一接口的同步
> 失败只影响自身 run，不影响其他两个接口（用户显式要求，spec FR-011
> "每次计划同步"按接口独立成终态；与 005 每接口独立 Deployment 模式一致）。
> 流程名称使用简体中文且语义符合业务场景（spec FR-019）；
> 内部 `schedule_slug` 保持 ASCII（幂等键与审计标识，research 决策 6）。

## 1. Flow 族 `* 交易日同步`（增量同步，3 个）

```python
@flow(name="前十大股东交易日同步", retries=0)
def top10_holders_sync(scheduled_at: str | None = None, schedule_slug: str | None = None) -> None: ...

@flow(name="前十大流通股东交易日同步", retries=0)
def top10_float_holders_sync(scheduled_at: str | None = None, schedule_slug: str | None = None) -> None: ...

@flow(name="股东人数交易日同步", retries=0)
def holder_count_sync(scheduled_at: str | None = None, schedule_slug: str | None = None) -> None: ...
```

- `scheduled_at`：原计划时点（UTC ISO 8601）。调度运行时从
  `prefect.runtime.flow_run.scheduled_start_time` 读取（必须），
  直接调用时**必须显式提供**，否则报错拒绝（目标交易日归属）。
- `schedule_slug`：计划标识（ASCII，见 §2），参与 run_key 与审计，
  与流程名（中文）职责分离（research 决策 6）。
- 每个 Flow 只处理**一个接口**：组装 `Settings` →
  `build_tushare_shareholder_data_provider()`（Registry，三 Flow 共享
  同一 Adapter 实例类）+ Service + Repository（与 005 `_build_service`、
  007 `stock_factor.py` 同模式）；调用 Service 中对应的
  `sync_*` 方法（`shareholder-data-service.md` §2）；`retries=0`，
  重试只发生在 Adapter 内，防止重试层相乘。
- 增量窗口 =（本接口水位, 昨日]（水位 = 本接口数据的 `max(ann_date)`，
  `shareholder-data-service.md` §4-3）；水位 ≥ 昨日时直接成功终态。
- 计划增量窗口最多回看 30 天（`shareholder_data_window_lookback_days`，2026-08-06 实测：
  空表 600+ 天积压窗口超出 25 分钟提取截止时间触发 PROVIDER_DEADLINE）；
  更深历史由显式回补 Flow 覆盖，窗口随水位逐日自然收敛。
- 非交易日：Flow 正常结束并记录 `SKIPPED_NOT_TRADING_DAY`（FR-001），
  不产生失败告警。
- **故障隔离**：三个 Flow 相互独立——A 接口来源失败/限流持续不恢复时，
  另两个接口的 Flow 仍按各自调度执行并形成成功终态；维护人员可单独
  重跑失败的接口，不影响其他接口（FR-011 每接口独立 run_key）。
- 日志白名单：只记录 `run_key`、目标交易日、计划时点、状态、计数、
  错误类别与脱敏摘要；禁止 Token、签名、完整请求体（NFR-005）。

## 2. Deployment（增量，3 个）

| Deployment | Flow | Cron（Asia/Shanghai） | 参数 |
|------------|------|----------------------|------|
| `前十大股东交易日同步/前十大股东交易日同步` | 前十大股东交易日同步 | `0 17 * * 1-5` | `schedule_slug=top10-holders-sync` |
| `前十大流通股东交易日同步/前十大流通股东交易日同步` | 前十大流通股东交易日同步 | `5 17 * * 1-5` | `schedule_slug=top10-floatholders-sync` |
| `股东人数交易日同步/股东人数交易日同步` | 股东人数交易日同步 | `10 17 * * 1-5` | `schedule_slug=holder-count-sync` |

- 各 Deployment 独立 `concurrency_limit: 1` + `collision_strategy: ENQUEUE`
  （同接口并发重入排队，配合 MySQL run_key 幂等兜底，NFR-003）。
- **错峰调度（17:00 / 17:05 / 17:10）**：账户级限流已由 Redis 分布式
  节流器跨进程强保证（任意并发场景合计 ≤ 400 次/分钟，research 决策 4
  修订），错峰仅作**运维友好**保留——日常增量基本串行执行，时间轴可预期。
- 启动时点沿用项目惯例 17:00（006/007 一致；用户未显式指定，
  spec 假设记录）；股东数据按披露节奏发布，晚于当日披露高峰。
- 增量请求量级：通常 1~3 个公告日 × 1~10 页，每接口约数 10 次请求，
  远小于单接口限流预算（research 决策 1）。

## 3. Flow 族 `* 历史回补`（初始化回补，3 个）

```python
@flow(name="前十大股东历史回补", retries=0)
def top10_holders_backfill(start_date: str, end_date: str, backfill_batch_id: str) -> None: ...

@flow(name="前十大流通股东历史回补", retries=0)
def top10_float_holders_backfill(start_date: str, end_date: str, backfill_batch_id: str) -> None: ...

@flow(name="股东人数历史回补", retries=0)
def holder_count_backfill(start_date: str, end_date: str, backfill_batch_id: str) -> None: ...
```

- `start_date/end_date`：`YYYYMMDD`；区间校验：起点 ≥ `BACKFILL_START=2024-01-01`、
  不含未来日期、`start_date ≤ end_date`，违反即整体拒绝（FR-018）。
- 流程：区间整体校验 → 逐日展开 → 逐日 `resolve`（START /
  SKIP_SUCCEEDED / RETRY / IN_PROGRESS，键 = `backfill_batch_id +
  <本接口 DataKind> + target_trade_date`）→ 逐日独立终态（FR-003）。
- 提取范围按接口语义（research 决策 1）：`前十大股东历史回补`/`前十大
  流通股东历史回补` 按报告期季度末调用（逐日迭代内仅季度末日期触发
  提取）；`股东人数历史回补` 按公告日逐日调用。
- 三个回补 Flow 相互独立（同增量隔离语义），且与增量 Flow 共用同一
  Service 与分布式节流器；**账户级限流跨进程共享**（Redis 分布式，
  回补与增量同跑、三个回补并行均合计 ≤ 400 次/分钟，无需串行约定作为
  正确性前提）；运维仍建议错开触发以缩短总耗时。
- 请求量级：`top10_*` ~10 期 × ~9 页 ≈ 90 次/接口 + `股东人数` ~630 日
  ≈ 630 次，单接口 @400/min 在分钟级完成（research 待验证项 6）。
- 人工触发，无 schedule；示例：
  `uv run prefect deployment run "前十大股东历史回补/前十大股东历史回补" --param start_date=20240101 --param end_date=20260804 --param backfill_batch_id=init-top10-2026-08-05`。

## 4. 运维语义

- **失败与恢复**：单接口单日失败只影响该接口该日，不阻塞该接口后续
  日期（NFR-009）也不影响其他接口（用户显式要求）；失败接口可单独
  重跑（同 run_key 重开，租约过期自动 ABANDONED）。
- **幂等**：`run_key` MySQL 唯一约束是最终保障（含接口维度）；
  ClickHouse 同键替换兜底（NFR-003/SC-003）；增量水位自愈——失败的
  交易日由下一运行的自然窗口覆盖（research 决策 6）。
- **更正公告修订**：同一业务身份（股票 + 披露期 + 股东名称）出现新公告
  时按最新公告值更新（`updated_count`），不触发冲突（FR-010/ED-010）；
  非新公告的值变化整批失败。
- **并发与限流**：账户级 400 次/分钟预算由 Redis 分布式节流器跨进程
  共享（含回补与增量同跑）；Redis 不可达时降级进程级限流 + Adapter
  `PROVIDER_RATE_LIMITED` 重试兜底（tushare 契约 §4/§5）。
- **窗口及时性**：增量同步在当日形成终态（NFR-001）；回补由人工窗口
  控制（单接口分钟级，research 待验证项 6）。
- **监控**：run/attempt 状态、计数与 issue 通过既有审计表可观测
  （SC-009），`data_kind` 区分接口（`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/
  `HOLDER_COUNT`）；不做颜色依赖的界面（NFR-010）。

## 5. 契约测试要点

- 直接调用各 Flow（显式 `scheduled_at`）验证组装与参数校验；
  缺失 `scheduled_at` 必须拒绝。
- 用假 Provider 验证每接口：非交易日 SKIPPED、空水位窗口直接成功
  （不调用 Provider）、回补区间校验拒绝非法区间、逐日幂等（替身调用
  计数断言）、单日失败不阻塞后续日期、更正公告修订不触发冲突。
- **隔离验证**：A 接口 Provider 抛错（替身注入）时，B/C 接口 Flow
  独立成功；单独重跑失败接口只处理该接口数据。
- 验证日志白名单：注入含 Token 的错误消息，断言日志不含敏感内容。
- 验证中文流程名与 ASCII `schedule_slug` 双轨：`prefect deployment ls`
  显示 6 个中文部署名，审计 run_key 使用 ASCII slug（无编码异常）。
