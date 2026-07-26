# 工作流契约：每日股票列表同步

## 1. Deployment

**Flow**：`stock-list-sync`

**Deployment**：`stock-list-sync/default`

**入口**：

```text
src/lucking/flows/stock_list.py:sync_stock_list
```

**调度**：

```yaml
concurrency_limit:
  limit: 1
  collision_strategy: ENQUEUE
schedules:
  - cron: "0 9 * * *"
    timezone: Asia/Shanghai
    slug: daily-stock-list
    active: true
    parameters:
      scope_code: CN-S
      schedule_slug: daily-stock-list
```

每个自然日执行，包括周末和休市日。并发限制保护本机资源，MySQL 唯一 `run_key`
提供最终业务幂等。`CN-S` 固定覆盖 `XSHG/XSHE/XBSE`，Deployment 和人工运行均不接受
venue 子集参数。

## 2. Flow 签名

```python
@flow(name="stock-list-sync")
def sync_stock_list(
    scope_code: str = "CN-S",
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
    is_manual_retry: bool = False,
) -> dict[str, JSONValue]: ...
```

- 计划运行的 `scheduled_at` 缺省时使用
  `prefect.runtime.flow_run.scheduled_start_time`。
- 人工首次运行必须显式传 `scheduled_at` 和 `schedule_slug=manual-stock-list`。
- 人工补跑必须传原计划的 `scheduled_at/schedule_slug` 并设置 `is_manual_retry=true`。
- 参数不得包含 `stock_basic`、Tushare 状态、Token 或其他专有字段。

## 3. 组合根

Flow 只负责：

1. 加载 Settings。
2. 从 Registry 构造 `StockListProvider`。
3. 构造 MySQL Repository 和 `StockListService`。
4. 将 Prefect 计划信息转换为 `StockListSyncCommand`。
5. 记录结构化开始/终态事件并返回 JSON 摘要。

Flow 不直接调用 Tushare、不解析字段、不执行身份或完整性校验、不写 SQL。

## 4. 重试边界

Adapter 负责失败 segment 的有界外部重试；Flow 不配置或实现第二层完整操作重试。
Flow 对 Provider、校验和持久化异常执行：

1. 请求 Service 记录 `FAILED`。
2. 写脱敏终态日志。
3. 重新抛出异常，使 Prefect Flow Run 失败。

运维通过显式补跑同一 `run_key` 恢复。

## 5. 结构化日志

日志文件：`logs/stock-list-sync.jsonl`，10 MiB 轮转并保留 5 个归档。

事件：

```text
stock_list_sync_started
stock_list_segment_attempt_started
stock_list_segment_attempt_failed
stock_list_validation_completed
stock_list_sync_succeeded
stock_list_sync_failed
```

公共白名单字段：

```text
flow_run_id, run_id, run_key, attempt_count,
schedule_slug, provider_code, scope_code,
scheduled_at, started_at, completed_at, published_at,
business_date, segment_count, completed_segment_count, capped_segment_count,
received_count, valid_count, duplicate_count, invalid_count, conflict_count,
added_count, updated_count, unchanged_count,
schedule_delay_ms, run_duration_ms, schedule_to_completion_ms,
timeliness_met, error_category, error_summary
```

禁止 Token、完整请求/响应、原始供应商行、数据库连接串和非白名单字段。

## 6. 及时性

将现有日志计时函数参数化：

```text
calculate_schedule_timing(
  scheduled_at,
  started_at,
  completed_at,
  target_ms
)
```

- 交易日历继续使用 600,000 ms。
- 股票列表使用 1,800,000 ms。
- 计划运行纳入 `daily-stock-list` 最近 30 次统计；人工运行不参与 SC-001。

指标：

```text
schedule_delay_ms = started_at - scheduled_at
run_duration_ms = completed_at - started_at
schedule_to_completion_ms = completed_at - scheduled_at
timeliness_met = schedule_to_completion_ms <= 1_800_000
```

## 7. Flow 返回

成功示例：

```json
{
  "run_id": "uuid",
  "run_key": "sha256",
  "status": "SUCCEEDED",
  "attempt_count": 1,
  "business_date": "2026-07-27",
  "provider_code": "tushare",
  "received_count": 5400,
  "valid_count": 5400,
  "duplicate_count": 0,
  "invalid_count": 0,
  "conflict_count": 0,
  "added_count": 10,
  "updated_count": 5,
  "unchanged_count": 5385
}
```

失败时无成功返回；MySQL 同步结果和 JSONL 日志包含安全终态，Prefect 运行进入失败状态。

## 8. 调度与 Flow 测试

- 解析 `prefect.yaml` 验证 Cron、时区、slug、入口、参数和并发限制。
- 固定计划时间与延迟启动时间，证明业务日期来自计划时点。
- 周末/休市日仍触发，不调用交易日历接口。
- 相同计划时点重复触发只对应一个 run_key。
- `SUCCEEDED` 重复触发不调用 Provider。
- Provider 暂时错误只在 Adapter 重试，Flow 不重复 12 segment。
- 失败、补跑和日志字段/脱敏符合本契约。
- 模拟最近 30 次 `daily-stock-list` 计划运行验证及时率统计，人工运行必须排除。
- 连续执行 30 次重复触发或补跑组合，验证只有一个权威结果且不产生重复股票。
