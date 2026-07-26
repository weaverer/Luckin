# 应用契约：BrokerRecommendationService

## 1. 目的和边界

`BrokerRecommendationService` 编排供应商无关 Provider、股票身份读取和 Repository，
负责月度推导、业务校验、去重、冲突判断与发布决策。

Service 不导入 Tushare 模块、不执行 SQL、不调用 Prefect、不记录秘密，
也不创建或修改股票主数据。

## 2. 同步命令

```python
@dataclass(frozen=True, slots=True)
class BrokerRecommendationSyncCommand:
    schedule_slug: str
    scheduled_at: datetime
    flow_run_id: str
    is_manual_retry: bool = False
```

规则：

- `scheduled_at` 必须包含时区并表示原计划时点。
- Service 将其转换为 `Asia/Shanghai`，再取所在月份第一日作为 `target_month`。
- 自动运行由 Prefect runtime 提供 `scheduled_at`；人工补跑必须显式传原值。
- 不接受独立 `target_month` 参数，避免与计划周期矛盾。

## 3. 同步结果

```python
@dataclass(frozen=True, slots=True)
class BrokerRecommendationSyncResult:
    run_id: str
    run_key: str
    attempt_id: str
    attempt_no: int
    status: SyncStatus
    target_month: date
    provider_code: str
    provider_request_count: int
    provider_retry_count: int
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

## 4. 同步流程

```text
validate command
  → derive target_month and run_key
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

## 5. 业务校验

- 目标月必须为月首，批次和每条记录月份必须完全一致。
- 券商名称只去首尾空白并折叠连续 Unicode 空白；规范后不得为空或超过 160 字符。
- 股票代码、简称、Provider 标识和 venue 必须有效。
- Provider 映射和规范 venue + code 必须解析到同一已有 `stock_id`。
- 完全相同业务键和字段的重复记录去重并增加 `duplicate_count`。
- 同一业务键出现不同 venue、代码或简称时为 `RECOMMENDATION_CONFLICT`，整批失败。
- 同一股票由不同券商推荐时是两条合法记录。
- 只新增、更新和确认本批出现的推荐；不读取基线来删除、失效或拒绝缺席行。
- 任一未解决无效或冲突记录导致本次失败。

## 6. Repository Port

```python
class BrokerRecommendationRepository(Protocol):
    def claim_run_and_start_attempt(
        self,
        *,
        run_key: str,
        schedule_slug: str,
        scheduled_for: datetime,
        target_month: date,
        scope_fingerprint: str,
        flow_run_id: str,
        provider_code: str,
        is_manual_retry: bool,
        started_at: datetime,
    ) -> AttemptClaim: ...

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

## 8. 幂等与补跑

- `run_key` 不使用实际启动时间或 Provider code。
- 同一成功周期重复调用直接返回原结果，不调用 Provider。
- 失败周期只有 `is_manual_retry=True` 才能创建下一 attempt。
- 3 日失败不阻止 4 日独立周期运行。
- 4 日成功批次是 3 日子集时仍可成功，3 日缺席行保持不变。
- 过期 `RUNNING` attempt 必须先标记 `ABANDONED` 并保存问题，再允许显式补跑。

## 9. 测试契约

单元与集成测试必须证明：

- 跨月延迟和补跑始终使用原计划月份。
- Unicode 空白规范化和 MySQL 唯一语义一致。
- 同券商同股幂等、同股不同券商分离、冲突整批失败。
- 3 日 A/B/C，4 日缺 A、改 B、新 D 后，A/B/C/D 均符合追加更新规则。
- 失败批次推荐表摘要不变。
- 相同周期 30 次重复和 10 组并发只有一个权威 run。
- 失败补跑保留多个 attempt，成功周期不可重开。
- Memory Provider 的 1,000 条完整批次成功。
