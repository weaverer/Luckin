# 工作流契约：交易日历同步

## 1. Flow

**Flow 名称**：`trading-calendar-sync`

**Deployment 名称**：`default`

**入口点**：`src/lucking/flows/trading_calendar.py:sync_trading_calendar`

## 2. 参数

| 参数 | 类型 | 必需 | 默认值 | 规则 |
|------|------|------|--------|------|
| `mode` | `monthly/year_end/manual` | 是 | 无 | 决定日期窗口 |
| `market_code` | 字符串 | 否 | `CN-S` | 首期只允许 `CN-S` |
| `start_date` | ISO 日期 | manual 必需 | 空 | 仅 manual 使用 |
| `end_date` | ISO 日期 | manual 必需 | 空 | 仅 manual 使用 |
| `as_of_date` | ISO 日期 | 否 | Flow 启动日 | 测试窗口计算；计划运行通常不传 |

校验：

- `monthly`、`year_end` 不得传 `start_date/end_date`。
- `manual` 必须同时传入 `start_date/end_date`。
- `start_date <= end_date`，人工范围不超过十年。
- 未启用的市场代码在调用 Provider 前拒绝。

## 3. Provider 选择

- Flow 从 `TRADING_CALENDAR_PROVIDER` 读取 Provider 稳定标识，默认 `tushare`。
- Flow 只向 Service 传递标准参数，不导入或实例化供应商 Adapter。
- 组合根通过 Registry 构造 `TradingCalendarProvider` 并注入 Service。
- Provider 未注册或配置缺失时，Flow 在外部调用前失败。

## 4. 计划

| slug | Cron | 时区 | 参数 |
|------|------|------|------|
| `monthly-current-year` | `0 2 1 * *` | `Asia/Shanghai` | `mode=monthly`, `market_code=CN-S` |
| `year-end-next-year` | `30 2 20 12 *` | `Asia/Shanghai` | `mode=year_end`, `market_code=CN-S` |

两个计划属于同一个 Deployment。Deployment 并发限制为 1，冲突策略使用 ENQUEUE，
避免重叠范围同时写入；数据库联合主键与事务仍作为最终一致性保障。

### 4.1 及时性口径

仅 `monthly/year_end` 计划运行计算：

- `schedule_delay_ms = started_at - scheduled_at`
- `run_duration_ms = completed_at - started_at`
- `schedule_to_completion_ms = completed_at - scheduled_at`
- `timeliness_met = schedule_to_completion_ms <= 600000`

`scheduled_at` 使用调度系统为本次 Flow Run 给出的预定时间；`completed_at` 是成功或失败
终态时间，因此排队和重试都计入。`manual` 运行的这些字段为 `null`，不参与 SC-002。
每个 Schedule 从 JSONL 日志取最近 20 次已完成计划运行统计达标率；不足 20 次时只报告
暂定比例和样本数。

## 5. 返回值

成功时返回：

```json
{
  "source": "tushare",
  "sync_mode": "manual",
  "market_code": "CN-S",
  "start_date": "2026-07-01",
  "end_date": "2026-12-31",
  "coverage_end": "2026-12-31",
  "completeness_status": "COMPLETE",
  "missing_future_count": 0,
  "received_count": 184,
  "written_count": 184,
  "status": "SUCCEEDED"
}
```

`completeness_status` 只能为：

- `COMPLETE`：请求闭区间全部覆盖。
- `FUTURE_PARTIAL`：只缺少 `as_of_date` 之后的连续未来尾部；Flow 仍为
  `SUCCEEDED`，但必须返回 `coverage_end/missing_future_count` 并记录降级状态。

失败时 Flow 进入 Failed 状态并抛出已分类、已脱敏的异常，不返回部分成功结果。

## 6. 日志事件

每行是一个 JSON 对象，公共字段如下：

```json
{
  "timestamp": "2026-07-25T02:00:00Z",
  "level": "INFO",
  "event": "sync_started",
  "flow_run_id": "uuid",
  "schedule_slug": null,
  "source": "tushare",
  "sync_mode": "manual",
  "market_code": "CN-S",
  "start_date": "2026-07-01",
  "end_date": "2026-12-31",
  "scheduled_at": null,
  "started_at": "2026-07-25T02:00:00Z"
}
```

事件集合：

- `sync_started`
- `fetch_attempt_started`
- `fetch_attempt_failed`
- `payload_validated`
- `database_write_started`
- `sync_succeeded`
- `sync_failed`

成功日志增加 `coverage_end`、`completeness_status`、`missing_future_count`、
`received_count` 和 `written_count`。计划运行的所有终态日志增加 `schedule_slug`、
`completed_at`、
`schedule_delay_ms`、`run_duration_ms`、`schedule_to_completion_ms` 和
`timeliness_met`；失败日志增加 `error_category`、`attempt` 和脱敏摘要。
禁止字段包括 Token、Authorization、完整请求体、数据库 URL 和密码。

错误重试：

- `ProviderRateLimitedError`、`ProviderUnavailableError`：最多 3 次，退避
  30、120、300 秒。
- `ProviderQuotaExceededError`、凭据、配置、请求、载荷和数据库错误：不重试。

## 7. 人工补数

```bash
uv run prefect deployment run \
  'trading-calendar-sync/default' \
  --param mode=manual \
  --param market_code=CN-S \
  --param start_date=2026-01-01 \
  --param end_date=2026-12-31
```

本地开发可调用模块入口，但必须经过同一 Flow 参数模型，不得绕过领域服务直接写表。
