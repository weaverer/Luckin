# 工作流契约：Prefect Flow

## 1. 目的

本契约定义行情同步工作流的调度、输入与终态语义，供部署、运维与验证使用。
Flow 只负责编排；领域逻辑在 Service，供应商访问在 Adapter，Flow 不复制二者。

## 2. 计划同步 Flow

**Flow 名称**：`market-data-sync`

**参数**：

```python
def market_data_sync(
    data_kind: DataKind,
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, Any]: ...
```

**Deployments**：

| Deployment | data_kind | Cron | 时区 |
|------------|-----------|------|------|
| `adj-factor-sync` | ADJ_FACTOR | `0 9 * * 1-5` | Asia/Shanghai |
| `daily-quote-sync` | DAILY_QUOTE | `0 17 * * 1-5` | Asia/Shanghai |
| `daily-basic-sync` | DAILY_BASIC | `45 17 * * 1-5` | Asia/Shanghai |
| `weekly-kline-sync` | WEEKLY_KLINE | `30 18 * * 1-5` | Asia/Shanghai |
| `monthly-kline-sync` | MONTHLY_KLINE | `30 18 * * 1-5` | Asia/Shanghai |

周线与月线为两个 Deployment、两个 `data_kind`，各自独立运行、独立恢复，
写入各自独立的 ClickHouse 业务表；同一 Cron 时点不互相阻塞。

规则：

- `scheduled_at` 未显式提供时从 Prefect runtime `scheduled_start_time` 读取；
  直接调用必须显式提供且包含时区；`schedule_slug` 不得为空。
- `retries=0`；瞬态重试只由 Adapter 在初次调用后执行最多 3 次，防止重试层数相乘。
- 并发限制 1、冲突策略 `ENQUEUE`；MySQL 唯一约束是幂等最终保障。
- 非交易日返回成功与 `SKIPPED` 状态，不产生业务运行。

## 3. 回补 Flow

**Flow 名称**：`market-data-backfill`

```python
def market_data_backfill(
    data_kind: DataKind,
    start_date: date,
    end_date: date,
    backfill_batch_id: str,
) -> dict[str, Any]: ...
```

规则：

- 校验区间整体（不早于 2024-01-01、不含未来、起止有效）后按交易日历逐日展开；
  每个交易日独立形成终态，单日失败不影响其他日期。
- 相同 `backfill_batch_id + data_kind + target_trade_date` 幂等恢复；
  新批次键允许主动刷新同一历史交易日。
- 返回逐日汇总（成功、跳过、失败、进行中计数）。

## 4. 日志与可观测性

- 每次运行写入独立 JSONL 日志，字段白名单：`data_kind`、run/attempt、
  目标交易日、运行类型、批次键、提取计数、retry、窗口及时性与终态。
- 日志不得包含 Token、连接串、完整请求/响应、供应商原始消息和原始行。
- Flow 运行结果与 MySQL 运行状态、ClickHouse 写入状态均可用于
  五分钟排障（见 quickstart.md）。

## 5. 契约测试要点

- 五个 Deployment 的 Cron、时区与 `data_kind` 映射正确（周线与月线各自独立）。
- 计划 Flow 直接调用（无 `scheduled_at`）必须失败。
- 回补 Flow 区间边界与逐日幂等行为符合本契约。
