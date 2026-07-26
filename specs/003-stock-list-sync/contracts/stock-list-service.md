# 领域契约：StockListService

## 1. 职责

`StockListService` 负责供应商无关的计划周期认领、全批验证、股票身份解析、原子发布、
失败保护和当前列表查询。它只依赖：

- `StockListProvider`
- `StockListRepository`
- 可注入的 UTC 时钟与单调时钟

它不得导入 Tushare Adapter、`stock_basic` 字段或专有状态代码。

## 2. 同步命令

```python
@dataclass(frozen=True, slots=True)
class StockListSyncCommand:
    schedule_slug: str
    scheduled_at: datetime
    scope_code: ScopeCode
    flow_run_id: str
    is_manual_retry: bool = False

class StockListService:
    def sync(self, command: StockListSyncCommand) -> StockListSyncResult: ...
```

规则：

- `scheduled_at` 必须为时区感知时间。
- 计划业务日期取 `scheduled_at` 在 `Asia/Shanghai` 的日期。
- 计划运行的 `schedule_slug` 为 `daily-stock-list`。
- 人工补跑必须传原计划时点并设置 `is_manual_retry=True`。
- `scope_code` 首期仅允许 `CN-S`。
- `CN-S` 的 venue 集合固定为 `XSHG/XSHE/XBSE`，Service 不接受 venue 子集。

## 3. run_key 与周期认领

```text
run_key = SHA256(
  schedule_slug + "|" +
  scheduled_for_utc_iso + "|" +
  scope_fingerprint
)
```

Repository 必须通过唯一键和行锁完成认领：

- 新周期：创建 `PENDING` 后转为 `RUNNING`，`attempt_count=1`。
- 同一 `flow_run_id` 重复提交：返回同一尝试，不增加计数。
- 已 `SUCCEEDED`：直接返回已有结果，不调用 Provider。
- 已 `FAILED` 且显式补跑：转 `RUNNING`，增加 `attempt_count`。
- 已 `RUNNING` 且租约未过期：拒绝第二执行者。
- 已 `RUNNING` 且租约过期：记录 `ABANDONED` 问题后允许显式补跑。

## 4. Provider 获取

Service 计算从实际开始起 25 分钟的单调时钟截止值，并调用：

```python
provider.fetch_stock_list(request, deadline=deadline)
```

Service 不执行任何 Provider 重试，不知道 segment 或专有字段；只验证通用覆盖证明：

- segment 数、完成数和触顶数满足契约；
- 聚合列表非空；
- Provider、scope 和获取时间合法。

## 5. 全批业务校验

每条候选记录必须满足：

- Provider ID、venue、代码、名称、币种和状态非空且为规范值。
- `market=CN-S`，venue 必须属于固定集合 `XSHG/XSHE/XBSE`。
- 日期关系满足 [数据模型](../data-model.md)。
- `(provider_code, provider_security_id)` 与 `(market, venue, security_code)`
  在候选集内均不产生冲突。

重复处理：

- 同一两个身份键、所有字段均相同：保留一条，增加 `duplicate_count`。
- 任一身份键相同但字段不同：记录 `IDENTITY_CONFLICT`，整批失败。
- Provider ID 与规范键分别命中不同现有 `stock_id`：整批失败。

历史基线：

- 首次成功运行没有历史基线，只使用 Provider 覆盖证明建立列表。
- 后续运行要求上一成功运行中该 Provider 的所有映射仍在候选集中。
- 任一映射缺席产生 `BASELINE_MISSING` 并整批失败。
- 不允许因缺席删除、退市或暂停股票。

## 6. 身份解析

对通过验证的每条记录：

1. 优先读取 `(provider_code, provider_security_id)` 映射。
2. 无映射时按 `(market_code, venue_code, security_code)` 查找唯一股票。
3. 唯一命中时附加 Provider 映射。
4. 无命中时生成新 `stock_id`。
5. 映射与规范键指向不同股票时，整批失败。

名称、币种、状态或日期变化更新同一 `stock_id`。没有明确映射的代码变化不得按名称合并。

## 7. 发布与失败

候选全批验证通过后，Repository 在一个事务中：

1. 锁定 `run_key` 并确认仍为当前尝试。
2. 批量 upsert `stock_current`。
3. 批量 upsert `stock_provider_mapping`。
4. 不处理任何缺席旧记录。
5. 更新同步计数、候选摘要、`SUCCEEDED/completed_at/published_at`。
6. 提交。

若事务失败，全部股票与映射变更回滚。Service 随后通过独立失败记录方法保存
`FAILED`、错误类别、计数和质量问题；失败记录本身无法保存时必须记录结构化错误并让
Flow 失败，不得声称已有列表被更新。

## 8. 同步结果

```python
@dataclass(frozen=True, slots=True)
class StockListSyncResult:
    run_id: UUID
    run_key: str
    status: SyncStatus
    attempt_count: int
    business_date: date
    provider_code: str
    received_count: int
    valid_count: int
    duplicate_count: int
    invalid_count: int
    conflict_count: int
    added_count: int
    updated_count: int
    unchanged_count: int
```

结果不得包含 Token、原始请求、原始行或本功能范围外字段。

## 9. 当前列表查询

```python
def list_current(
    *,
    market_code: MarketCode = MarketCode.CN_STOCK,
    venue_code: VenueCode | None = None,
    listing_status: ListingStatus | None = None,
    security_code: str | None = None,
    name_query: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> Sequence[StockListItem]: ...
```

- `limit` 范围为 1–1000，`offset >= 0`。
- 未知枚举或非法筛选在数据库查询前拒绝。
- 默认稳定排序为 `venue_code, security_code, stock_id`。
- 只返回项目 `stock_id` 和 `stock_current` 允许字段。
- 本方法只允许由项目内部已完成授权的调用方使用；既有或未来应用入口必须在调用前完成
  身份认证、授权和数据访问控制，本功能不新增公共网络接口或新的授权机制。

## 10. 测试要求

- 使用 Memory Provider 验证 Service 不依赖 Tushare。
- 固定时钟验证北京时间业务日期、UTC 存储和 25 分钟截止值。
- 覆盖首次基线、后续身份消失、完全重复、冲突、字段变化和新 Provider 映射。
- 在真实 MySQL 验证 run_key 唯一、行锁、批量 upsert、失败回滚和无删除。
- 验证 `SUCCEEDED` 重复触发不调用 Provider，失败补跑复用 run_key。
- 验证所有查询结果不含供应商或范围外字段。
- 在 10,000 条当前记录的数据集中先执行一次预热，再连续执行 100 次覆盖无筛选、代码、
  venue、名称和状态的代表性查询，验证至少 95 次在 1 秒内返回。
