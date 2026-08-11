# 内部契约：每日任务汇总与通知

## 1. 计划任务目录

```python
@dataclass(frozen=True, slots=True)
class ScheduledTaskDefinition:
    task_key: str
    display_name: str
    schedule_slug: str
    source_domain: str
    cron: str
    timezone: str

class ScheduledTaskCatalog(Protocol):
    def due_before(
        self, business_date: date, cutoff: time, timezone: ZoneInfo
    ) -> tuple[ScheduledTaskDefinition, ...]: ...
```

- 目录包含 `prefect.yaml` 中所有带 schedule 的 Deployment，任务身份以
  `deployment + schedule_slug` 规范化形成 `task_key`。
- `due_before` 只返回该业务日期实际应触发且原定时点不晚于 20:00 的任务；
  历史回补和无 schedule 的人工 Deployment 不纳入。
- 契约测试解析 `prefect.yaml`，确保计划任务无遗漏、无重复、Cron 和时区一致。

## 2. 归一运行读取端口

```python
class NormalizedTaskStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    UNKNOWN = "UNKNOWN"
    NOT_RUN = "NOT_RUN"

@dataclass(frozen=True, slots=True)
class TaskObservation:
    task_key: str
    status: NormalizedTaskStatus
    source_run_id: str | None
    source_flow_run_id: str | None
    started_at: datetime | None
    completed_at: datetime | None
    record_count: int | None
    error_category: str | None
    error_summary: str | None
    observed_at: datetime

class TaskExecutionReader(Protocol):
    def observe(
        self, definition: ScheduledTaskDefinition, scheduled_for: datetime
    ) -> TaskObservation: ...
```

归一规则：

- 业务发布成功 → `SUCCEEDED`；源领域记录无法可靠映射为成功、失败或未运行时 → `UNKNOWN`。
- 运行已认领且未到终态 → `RUNNING`；终态失败 → `FAILED`。
- 源领域明确表示只发布可信前缀或多子任务部分成功 → `PARTIAL`。
- 目录应运行但权威运行表无对应计划身份 → `NOT_RUN`。
- 运行表与 Prefect 状态冲突时，以项目业务运行表为准，并记录结构化不一致事件。
- Adapter 只能读取原领域公开字段；错误摘要必须脱敏并限制 500 字符。

## 3. 汇总服务

```python
@dataclass(frozen=True, slots=True)
class GenerateDailySummaryCommand:
    scheduled_for: datetime
    flow_run_id: str

@dataclass(frozen=True, slots=True)
class DailyTaskSummaryResult:
    summary_id: str
    business_date: date
    status: str
    notification_status: str
    counts: Mapping[NormalizedTaskStatus, int]
    items: tuple[TaskObservation, ...]

class DailyTaskSummaryService(Protocol):
    def generate(self, command: GenerateDailySummaryCommand) -> DailyTaskSummaryResult: ...
    def send_notification(self, summary_id: str, trigger_kind: str) -> NotificationResult: ...
    def get_live_status(self, business_date: date, observed_at: datetime) -> DailyTaskSummaryResult: ...
    def get_snapshot(self, business_date: date) -> DailyTaskSummaryResult | None: ...
```

- `scheduled_for` 必须为时区感知时间且转换到 `Asia/Shanghai` 后为目标日期 20:00。
- `generate` 使用 `business_date` 唯一认领；相同日期成功快照直接返回，不重新观察。
- 明细、状态计数和规范 JSON 的 SHA-256 摘要在一个 MySQL 事务发布为 `READY`。
- 自动通知只允许 `READY + PENDING|FAILED` 且从未 `SENT` 的汇总；显式补发允许已 `SENT`，
  但必须创建 `MANUAL_RETRY` attempt，仍使用原快照。
- 飞书调用不持有 MySQL 事务，attempt 认领与终态各使用短事务。

## 4. 通知 Port

```python
@dataclass(frozen=True, slots=True)
class TaskSummaryNotification:
    summary_id: str
    business_date: date
    generated_at: datetime
    counts: Mapping[NormalizedTaskStatus, int]
    exceptions: tuple[TaskObservation, ...]

class DeliveryDisposition(StrEnum):
    DELIVERED = "DELIVERED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"

@dataclass(frozen=True, slots=True)
class NotificationResult:
    disposition: DeliveryDisposition
    provider_code: str
    response_status: int | None
    error_category: str | None
    error_summary: str | None

class NotificationSender(Protocol):
    def send(self, notification: TaskSummaryNotification) -> NotificationResult: ...
```

- 领域契约不包含 webhook、签名、飞书 `msg_type` 或专有错误码。
- Feishu Adapter 负责把规范 DTO 转为不超过 20 KB 的消息卡片，异常任务过多时截断明细并保留总数。
- webhook 与签名密钥从 `SecretStr` 配置读取；日志、表、异常和测试快照中一律脱敏。
- Memory Sender 必须支持成功、429、5xx、鉴权失败和畸形响应 golden cases。

## 5. Flow 契约

```python
@flow(name="daily-task-summary")
def daily_task_summary_flow(scheduled_for: datetime | None = None) -> DailyTaskSummaryResult: ...

@flow(name="retry-daily-task-notification")
def retry_daily_task_notification(summary_id: str) -> NotificationResult: ...
```

- `prefect.yaml` 新增 `每日任务汇总通知` Deployment：Cron `0 20 * * *`，时区 `Asia/Shanghai`，
  并发限制 1；Flow 使用 Prefect 原计划时点，不以实际启动时间派生业务日期。
- 可恢复的网络、429 和 5xx 使用 30/120/300 秒最多 3 次重试；永久错误不重试。
- 20:05 前仍未成功时，汇总保持可查询，通知状态置 `FAILED`，并记录可操作日志。

## 6. 契约测试

- 固定时钟覆盖工作日、周末、月度任务日、20:00 后任务排除和跨午夜实际启动。
- 每个既有同步领域的 Reader 用相同 golden cases 验证六种归一状态。
- 同一业务日期并发生成只产生一份快照；重复自动触发至多一次成功发送。
- 补发读取原 `snapshot_digest`，不得重算明细；失败尝试不泄露 webhook 或原始响应。
- Feishu Adapter 与 Memory Sender 通过相同 `NotificationSender` 契约，替换不修改 Service/Flow。
