# 应用契约：BrokerRecommendationService

## 1. 目的和边界

`BrokerRecommendationService` 编排供应商无关 Provider、股票身份读取和 Repository，
负责月度推导、业务校验、去重、冲突判断与发布决策。

Service 不导入 Tushare 模块、不执行 SQL、不调用 Prefect、不记录秘密，
也不创建或修改股票主数据。

## 2. 同步命令

```python
class BrokerRecommendationRunKind(StrEnum):
    SCHEDULED = "SCHEDULED"
    BACKFILL = "BACKFILL"


@dataclass(frozen=True, slots=True)
class ScheduledBrokerRecommendationSyncCommand:
    schedule_slug: str
    scheduled_at: datetime
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class BackfillBrokerRecommendationMonthCommand:
    target_month: date
    backfill_batch_id: str
    flow_run_id: str


@dataclass(frozen=True, slots=True)
class RetryBrokerRecommendationSyncCommand:
    run_id: str
    flow_run_id: str


BrokerRecommendationSyncCommand = (
    ScheduledBrokerRecommendationSyncCommand
    | BackfillBrokerRecommendationMonthCommand
    | RetryBrokerRecommendationSyncCommand
)
```

规则：

- 计划命令的 `scheduled_at` 必须包含时区并表示原计划时点；Service 将其转换为
  `Asia/Shanghai`，再取所在月份第一日作为 `target_month`。
- 历史补跑月命令必须显式提供月首 `target_month` 和非空 `backfill_batch_id`；
  目标月不得晚于当前北京时间所在月份。
- 重试命令必须引用已有失败或过期运行；目标月份、运行类型和身份字段全部从原 run 读取，
  调用方不得覆盖。
- 三类命令字段不得混用；实际启动时间不参与目标月或 `run_key`。

## 3. 同步结果

```python
@dataclass(frozen=True, slots=True)
class BrokerRecommendationSyncResult:
    run_id: str
    run_key: str
    attempt_id: str
    attempt_no: int
    status: SyncStatus
    run_kind: BrokerRecommendationRunKind
    target_month: date
    backfill_batch_id: str | None
    provider_code: str
    provider_request_count: int
    provider_retry_count: int
    provider_page_count: int
    provider_page_limit: int
    provider_last_page_count: int
    received_count: int
    valid_count: int
    added_count: int
    updated_count: int
    unchanged_count: int
    duplicate_count: int
    invalid_count: int
    conflict_count: int
```

结果只含规范状态和计数，不返回 Token、Provider 原始字段、错误消息或原始行。
`run_id`、`attempt_id` 和推荐项中的业务 ID 继续使用 UUID；数据库自增物理 `id`
不得进入 Service、Flow、日志关联键或内部消费契约。

## 4. 同步流程

```text
validate discriminated command
  → derive/load run identity, target_month and run_key
  → repository.claim_run_and_start_attempt
  → provider.fetch_month
  → validate evidence and target month
  → normalize broker whitespace
  → validate fields and resolve stable stock identity
  → deduplicate / detect conflicts
  → repository.publish_success
  → return canonical result
```

任一步失败：

1. 推荐发布事务必须为零修改或整体回滚。
2. Service 生成统一安全类别与完整计数。
3. Repository 独立保存失败 attempt/run 和 issue。
4. Service 重新抛出不含秘密的领域或 Provider 异常。

### 4.1 历史补跑月份解析

补跑 Flow 在执行某个月前调用：

```python
class BackfillMonthAction(StrEnum):
    START = "START"
    SKIP_SUCCEEDED = "SKIP_SUCCEEDED"
    RETRY = "RETRY"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class BackfillRunState:
    run_id: str
    status: SyncStatus
    active_attempt_lease_expires_at: datetime | None
    active_attempt_lease_expired: bool


@dataclass(frozen=True, slots=True)
class BackfillMonthResolution:
    action: BackfillMonthAction
    run_id: str | None
    target_month: date


def resolve_backfill_month(
    *,
    backfill_batch_id: str,
    target_month: date,
) -> BackfillMonthResolution: ...
```

`active_attempt_lease_expired` 必须由 Repository 使用数据库 UTC 时钟计算；
Service 不使用 Worker 本地时间自行判断。Retry 认领时 Repository 必须在同一事务再次原子确认过期。

解析规则：

- 无既有 run：`START`，随后发送 `BackfillBrokerRecommendationMonthCommand`。
- `SUCCEEDED`：`SKIP_SUCCEEDED`，不调用 Provider。
- `FAILED`：`RETRY` 并返回原 `run_id`，随后发送 `RetryBrokerRecommendationSyncCommand`。
- `RUNNING` 且租约有效：`IN_PROGRESS`，不得创建第二 attempt。
- `RUNNING` 且租约过期：先将旧 attempt 置为 `ABANDONED` 并记录问题，
  再返回 `RETRY` 和原 `run_id`。

同一批次失败月份不得再次发送 Backfill 命令来创建新 run。

## 5. 业务校验

- 目标月必须为月首，批次和每条记录月份必须完全一致。
- 券商名称只去首尾空白并折叠连续 Unicode 空白；规范后不得为空或超过 160 字符。
- 股票代码、简称、Provider 标识和 venue 必须有效。
- Provider 映射和规范 venue + code 必须解析到同一已有 `stock_id`。
- 单条记录无法解析到已有 `stock_id` 时，记录脱敏 `UNKNOWN_STOCK_IDENTITY` issue、
  增加 `invalid_count` 并跳过该条；不得影响同月其他有效记录发布。
- 完全相同业务键和字段的重复记录去重并增加 `duplicate_count`。
- 同一业务键出现不同 venue、代码或简称时为 `RECOMMENDATION_CONFLICT`，整批失败。
- 同一股票由不同券商推荐时是两条合法记录。
- 只新增、更新和确认本批出现的推荐；不读取基线来删除、失效或拒绝缺席行。
- 身份映射冲突、月份错配、核心字段无效或推荐冲突导致整批失败；若未知身份记录跳过后
  仍有有效记录则可以成功，若 `valid_count = 0` 则整月失败。

## 6. Repository Port

```python
class BrokerRecommendationRepository(Protocol):
    def claim_run_and_start_attempt(
        self,
        *,
        run_key: str,
        run_kind: BrokerRecommendationRunKind,
        schedule_slug: str | None,
        scheduled_for: datetime | None,
        backfill_batch_id: str | None,
        target_month: date,
        scope_fingerprint: str,
        flow_run_id: str,
        provider_code: str,
        started_at: datetime,
    ) -> AttemptClaim: ...

    def retry_run_and_start_attempt(
        self,
        *,
        run_id: str,
        flow_run_id: str,
        provider_code: str,
        started_at: datetime,
    ) -> AttemptClaim: ...

    def get_backfill_run(
        self,
        *,
        backfill_batch_id: str,
        target_month: date,
    ) -> BackfillRunState | None: ...

    def resolve_stock_identities(
        self,
        provider_code: str,
        candidates: tuple[IdentityCandidate, ...],
    ) -> tuple[ResolvedStockIdentity, ...]: ...

    def publish_success(
        self,
        claim: AttemptClaim,
        *,
        records: tuple[PublishRecommendation, ...],
        counts: SyncCounts,
        candidate_digest: str,
        completed_at: datetime,
    ) -> None: ...

    def record_failure(
        self,
        claim: AttemptClaim,
        *,
        counts: SyncCounts,
        category: str,
        summary: str,
        issues: tuple[SyncIssue, ...],
        completed_at: datetime,
    ) -> None: ...

    def get_result(self, run_id: str) -> BrokerRecommendationSyncResult | None: ...

    def list_month(
        self,
        query: BrokerRecommendationQuery,
    ) -> list[BrokerRecommendationItem]: ...
```

Repository 必须：

- 用数据库唯一键原子认领 `run_key`，并捕获首次并发插入竞态。
- 强制计划字段与历史补跑字段按 `run_kind` 互斥，并校验目标月份。
- `get_backfill_run` 按稳定的批次键和月份解析业务 run，不使用 Provider、
  配置、`scope_fingerprint` 或实际启动时间。
- attempt 认领时用数据库 UTC 设置固定 35 分钟 `lease_expires_at`；
  首版不续租。过期 Retry 必须锁定原 run/attempt、以数据库 UTC 再次确认租约到期，
  再原子写入 `ABANDONED`、issue 和新 attempt。
- 为每次执行追加不可变 attempt；相同 `flow_run_id` 重入不得重复。
- 锁定 run 后发布，确保 attempt 所有权和状态正确。
- 以单事务完成推荐 upsert、attempt 成功和 run 成功。
- 失败记录保留全部计数；issue 不保存原始 payload。
- 不删除候选集中缺席的推荐。

## 7. 内部查询

```python
@dataclass(frozen=True, slots=True)
class BrokerRecommendationQuery:
    target_month: date
    broker_name: str | None = None
    stock_id: str | None = None
    venue_code: VenueCode | None = None
    security_code: str | None = None
    limit: int = 1000
    offset: int = 0
```

规则：

- `target_month` 必填且必须为月首。
- `broker_name` 若存在，按相同空白规范化后精确匹配。
- `1 ≤ limit ≤ 1000`，`offset ≥ 0`。
- 稳定排序为券商、venue、代码、推荐 ID。
- 返回月份、券商、`stock_id`、venue、代码和简称；
  不返回 Provider 标识、运行错误或供应商字段。
- 调用入口必须在调用 Service 前完成认证、授权和访问控制。

## 8. 幂等、历史补跑与失败重试

- `run_key` 不使用实际启动时间或 Provider code。
- 计划运行：
  `SHA256("SCHEDULED" | schedule_slug | scheduled_at_utc | target_month)`。
- 历史补跑：
  `SHA256("BACKFILL" | backfill_batch_id | target_month)`。
- `scope_fingerprint`、Provider 和配置只作为审计信息，不参与业务运行身份。
- 同一成功运行身份重复调用直接返回原结果，不调用 Provider。
- 失败运行只有显式 `RetryBrokerRecommendationSyncCommand` 才能创建下一 attempt。
- 3 日失败不阻止 4 日独立周期运行。
- 4 日成功批次是 3 日子集时仍可成功，3 日缺席行保持不变。
- 同一 `backfill_batch_id + target_month` 重复或并发提交只形成一个 run；
  新 `backfill_batch_id` 允许刷新同一目标月份，推荐表仍按业务唯一键幂等。
- 计划与补跑可拥有不同 run 并发处理同月；Repository 必须以
  `recommendation_month + broker_name + stock_id` 唯一约束避免重复。
  股票代码属于稳定股票身份；股票简称等其他属性不定义跨 run 版本优先级，
  测试不比较并发写入后的属性版本。
- 过期 `RUNNING` attempt 必须先标记 `ABANDONED` 并保存问题，再允许显式重试。

## 9. 测试契约

单元与集成测试必须证明：

- 跨月延迟的计划运行始终使用原计划月份；历史补跑使用显式目标月份。
- 24 月补跑逐月形成独立 run，相同批次重放跳过成功月并恢复失败月；
  新批次可以刷新已成功月份。
- 按首尾月份均计入的口径，120 月范围被接受并逐月解析；
  121 月范围在解析任何月份前整体拒绝。
- 相同批次的失败/过期月份解析为原 `run_id` 的 Retry，未开始月份解析为 Backfill。
- Unicode 空白规范化和 MySQL 唯一语义一致。
- 同券商同股幂等、同股不同券商分离、冲突整批失败。
- 3 日 A/B/C，4 日缺 A、改 B、新 D 后，A/B/C/D 均符合追加更新规则。
- 失败批次推荐表摘要不变。
- 相同周期 30 次重复和 10 组并发只有一个权威 run。
- 10 组计划/补跑同月跨运行类型并发分别保留两个 run，
  且相同 `recommendation_month + broker_name + stock_id` 不产生重复推荐。
- 固定 35 分钟租约使用数据库 UTC 创建和比较；到期前保持 `IN_PROGRESS`，
  到期后原子 `ABANDONED` 并以原 `run_id` Retry。
- 失败重试保留多个 attempt，成功运行不可重开。
- Memory Provider 的 2,500 条完整批次成功。
