# 工作流契约：Prefect Flow 与调度（股票技术面因子）

> 编排契约：Flow 入口、参数、调度与运维语义。Flow 只做组装与触发，
> 领域逻辑在 Service 层（`stock-factor-service.md`）；Flow 不得直接调用
> 供应商或存储细节（宪章 II）。
> 流程名称使用简体中文且语义符合业务场景（spec FR-019，用户显式约定）；
> 内部 `schedule_slug` 保持 ASCII（幂等键与审计标识，research 决策 6）。

## 1. Flow `股票技术面因子交易日同步`（增量同步）

```python
@flow(name="股票技术面因子交易日同步", retries=0)
def stock_factor_sync(scheduled_at: str | None = None, schedule_slug: str | None = None) -> None: ...
```

- `scheduled_at`：原计划时点（UTC ISO 8601）。调度运行时从
  `prefect.runtime.flow_run.scheduled_start_time` 读取（必须），
  直接调用时**必须显式提供**，否则报错拒绝（NFR-007 目标交易日归属）。
- `schedule_slug`：计划标识（ASCII，如 `stock-factor-sync`），参与
  run_key 与审计，与流程名（中文）职责分离（research 决策 6）。
- Flow 内组装：`Settings` → `build_tushare_stock_factor_provider()`
  （Registry）+ Service + Repository（与 005 `_build_service`、
  006 `index_factor.py` 同模式）；`retries=0`，重试只发生在 Adapter 内
  （决策 4），防止重试层相乘。
- 非交易日：Flow 正常结束并记录 `SKIPPED_NOT_TRADING_DAY`（FR-001），
  不产生失败告警。
- 日志白名单：只记录 `run_key`、目标交易日、计划时点、状态、计数、
  错误类别与脱敏摘要；禁止 Token、签名、完整请求体（NFR-005）。

## 2. Deployment（增量）

| Deployment | Flow | Cron（Asia/Shanghai） | 参数 |
|------------|------|----------------------|------|
| `股票技术面因子交易日同步/股票技术面因子交易日同步` | 股票技术面因子交易日同步 | `0 19 * * 1-5` | `schedule_slug=stock-factor-sync` |

- Work pool `local-pool`；`concurrency_limit: 1` + `collision_strategy: ENQUEUE`
  （并发重入时排队，配合 MySQL run_key 幂等兜底，NFR-003）。
- 19:00 为 2026-08-06 上线实测校准结果：17:00 上游 `stk_factor_pro` 当日数据尚未发布
  （连续 2 日 EMPTY_AGGREGATE，23:00 实测已就绪）；数据更新时点文档未明确，仍属
  上线门禁实测校准范围（research 待验证项 4）。

## 3. Flow `股票技术面因子历史回补`（初始化回补）

```python
@flow(name="股票技术面因子历史回补", retries=0)
def stock_factor_backfill(
    start_date: str, end_date: str, backfill_batch_id: str,
) -> None: ...
```

- `start_date/end_date`：`YYYYMMDD`；区间校验：起点 ≥ `BACKFILL_START=2024-01-01`、
  不含未来日期、`start_date ≤ end_date`，违反即整体拒绝（FR-018）。
- 流程：区间整体校验 → 交易日历展开 → 逐日 `resolve`（START /
  SKIP_SUCCEEDED / RETRY / IN_PROGRESS，键 = `backfill_batch_id +
  STOCK_FACTOR + target_trade_date`）→ 逐日独立终态（FR-003）。
- 与增量同步共用同一 Service 与节流器（30 次/分钟全局生效，NFR-004）。
- 人工触发，无 schedule；示例：
  `uv run prefect deployment run "股票技术面因子历史回补/股票技术面因子历史回补" --param start_date=20240101 --param end_date=20260804 --param backfill_batch_id=init-2026-08-04`。

## 4. 运维语义

- **失败与恢复**：单日失败只影响该日，不阻塞后续日期（NFR-009）；
  失败日期可单独重跑（同 run_key 重开，租约过期自动 ABANDONED）。
- **幂等**：`run_key` MySQL 唯一约束是最终保障；ClickHouse 同键替换兜底
  （NFR-003/SC-003）。
- **复权回溯更新**：同一交易日重复同步且期间发生新除权事件时，
  `_qfq/_hfq`/`adj_factor` 值按来源最新值更新（`updated_count`），
  不触发冲突（FR-010/ED-009）；稳定字段变化仍整批失败。
- **窗口及时性**：增量同步在当日形成终态（NFR-001）；回补由人工窗口
  控制（约 630 交易日 × 节流 ≥ 2 秒 ≈ 20~30 分钟，research 待验证项 6）。
- **监控**：run/attempt 状态、计数与 issue 通过既有审计表可观测
  （SC-009）；不做颜色依赖的界面（NFR-010）。

## 5. 契约测试要点

- 直接调用 Flow（显式 `scheduled_at`）验证组装与参数校验；
  缺失 `scheduled_at` 必须拒绝。
- 用假 Provider 验证：非交易日 SKIPPED、回补区间校验拒绝非法区间、
  逐日幂等（替身调用计数断言）、单日失败不阻塞后续日期、
  复权字段回溯更新不触发冲突。
- 验证日志白名单：注入含 Token 的错误消息，断言日志不含敏感内容。
- 验证中文流程名与 ASCII `schedule_slug` 双轨：`prefect deployment ls`
  显示中文名，审计 run_key 使用 ASCII slug（无编码异常）。
