# 内部服务契约：MarketDataService

## 1. 目的

本契约由 Lucking 项目拥有，是计划 Flow、回补 Flow 与内部调用方使用行情
同步能力的唯一业务边界。Service 只依赖四个供应商无关 Provider、交易日历
与股票身份解析，不接触任何供应商端点、字段或错误码。

## 2. 计划同步命令

```python
class DataKind(StrEnum):
    DAILY_QUOTE = "DAILY_QUOTE"
    ADJ_FACTOR = "ADJ_FACTOR"
    DAILY_BASIC = "DAILY_BASIC"
    WEEKLY_KLINE = "WEEKLY_KLINE"
    MONTHLY_KLINE = "MONTHLY_KLINE"


@dataclass(frozen=True, slots=True)
class ScheduledMarketDataSyncCommand:
    data_kind: DataKind
    schedule_slug: str
    scheduled_for: datetime      # 必须包含时区（原计划时点）
    flow_run_id: str
```

行为：

1. 从 `scheduled_for` 转换到 `Asia/Shanghai` 并确定目标交易日；
   查询交易日历（CN-S）：非交易日直接返回“跳过”结果，不调用 Provider。
2. 以 `data_kind + SCHEDULED + schedule_slug + scheduled_for_utc + target_trade_date`
   计算 `run_key` 并幂等解析运行：`SUCCEEDED` 直接返回；`FAILED` 转换为
   Retry（引用原 `run_id` 新增 attempt）；`RUNNING` 且租约有效则报告进行中；
   `RUNNING` 且租约过期则原子转 `ABANDONED` 后重试。
3. 调用对应 Provider 提取目标交易日（或周期）候选，验证覆盖证据完整。
4. 全批内存校验后发布：一次 ClickHouse 批量 INSERT（单 block）写入对应
   业务表，成功后在同一 MySQL 事务写 attempt/run 成功终态（见 data-model.md §12）。
5. 返回同步结果（状态、目标交易日、各类计数、覆盖证据、错误摘要）。

## 3. 回补命令

```python
@dataclass(frozen=True, slots=True)
class BackfillMarketDataCommand:
    data_kind: DataKind
    start_date: date
    end_date: date
    backfill_batch_id: str      # 非空幂等键
    flow_run_id: str
```

行为：

- 区间整体校验：`start_date <= end_date`、不早于 2024-01-01、不含未来交易日；
  无效范围在任何运行创建前整体拒绝。
- 按交易日历展开区间内每个交易日，逐日以
  `data_kind + BACKFILL + backfill_batch_id + target_trade_date` 幂等解析：
  不存在时创建 BACKFILL 运行；`SUCCEEDED` 跳过；`FAILED` 或租约过期
  `RUNNING` 转换为引用原 `run_id` 的 Retry。
- 每日独立终态，单日失败不影响其他日期的结果。

## 4. 同步结果

```python
@dataclass(frozen=True, slots=True)
class MarketDataSyncResult:
    data_kind: DataKind
    run_kind: RunKind
    run_id: str
    attempt_id: str
    target_trade_date: date
    status: SyncStatus        # SKIPPED / SUCCEEDED / FAILED / IN_PROGRESS
    received_count: int
    valid_count: int
    added_count: int
    updated_count: int
    unchanged_count: int
    duplicate_count: int
    invalid_count: int
    conflict_count: int
    error_category: str | None
    error_summary: str | None
```

## 5. 内部查询

```python
def query(
    data_kind: DataKind,              # 决定查询的 ClickHouse 业务表
    trade_date: date | None,
    stock_id: str | None = None,
    venue_code: str | None = None,
    security_code: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> QueryResult: ...
```

规则：

- 只返回规范业务字段；不返回 Provider 标识、运行问题或供应商字段。
- 分页限制 `1 ≤ limit ≤ 1000`、`offset ≥ 0`；稳定排序见 data-model.md §14。
- 仅供项目内部已授权调用方使用；认证、授权和访问控制由调用入口负责。

## 6. 契约测试要点

- 计划命令非交易日返回 `SKIPPED` 且不调用 Provider。
- 相同 `run_key` 重复触发只产生一个权威运行；`SUCCEEDED` 不可重开。
- 回补区间整体校验边界（2024-01-01 之前、未来、反向、空区间）。
- 五类 `data_kind` 互不串扰：相同交易日不同数据类形成不同 run；
  周线与月线写入各自业务表。
- 发布语义：ClickHouse INSERT 失败时 MySQL 运行保持非 `SUCCEEDED` 且可重试；
  重试后 ClickHouse 同键替换收敛，最终行集与成功执行一致。
