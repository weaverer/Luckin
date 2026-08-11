# 内部契约：股东数据同步服务

> 编排契约：Service 对外的命令、结果与内部查询。Service 只依赖
> `shareholder-data-provider.md`（Port）、交易日历、003 身份解析与存储
> 抽象；不依赖任何供应商细节（宪章 II）。
> **三个接口各有一个 Flow 与一条同步链路**（`prefect-flow.md` §1/§3）：
> Service 按接口暴露独立方法，每个方法的运行（认领、水位、提取、校验、
> 发布、终态）彼此隔离——任一接口失败不影响其他两个（用户显式要求）。

## 1. 目的

定义股东数据同步的领域编排：交易日判断、按接口水位计算与公告日窗口
展开、按接口提取（分页在 Provider 内部）、身份解析（003 复用）、批次
校验、ClickHouse 发布、MySQL 审计终态。同步运行与消费两侧共享本契约
的语义。

## 2. 命令与入口

```python
# 增量（每接口一个命令；schedule_slug 区分接口）
@dataclass(frozen=True)
class ScheduledTop10HoldersSyncCommand:
    scheduled_at: datetime            # 原计划时点（UTC）
    schedule_slug: str                # 计划标识（"top10-holders-sync"，ASCII）
    now: datetime | None = None

@dataclass(frozen=True)
class ScheduledTop10FloatHoldersSyncCommand:
    scheduled_at: datetime
    schedule_slug: str                # "top10-floatholders-sync"
    now: datetime | None = None

@dataclass(frozen=True)
class ScheduledHolderCountSyncCommand:
    scheduled_at: datetime
    schedule_slug: str                # "holder-count-sync"
    now: datetime | None = None

# 回补（每接口一个命令；backfill_batch_id 区分批次）
@dataclass(frozen=True)
class BackfillTop10HoldersCommand:
    start_date: date                  # 起（含）；不得早于 2024-01-01
    end_date: date                    # 止（含）；不得晚于今天、不得早于 start_date
    backfill_batch_id: str

@dataclass(frozen=True)
class BackfillTop10FloatHoldersCommand:
    start_date: date; end_date: date; backfill_batch_id: str

@dataclass(frozen=True)
class BackfillHolderCountCommand:
    start_date: date; end_date: date; backfill_batch_id: str
```

```python
class ShareholderDataService:
    def sync_top10_holders(self, cmd: ScheduledTop10HoldersSyncCommand) -> ShareholderDataSyncResult: ...
    def sync_top10_float_holders(self, cmd: ScheduledTop10FloatHoldersSyncCommand) -> ShareholderDataSyncResult: ...
    def sync_holder_count(self, cmd: ScheduledHolderCountSyncCommand) -> ShareholderDataSyncResult: ...
    def backfill_top10_holders(self, cmd: BackfillTop10HoldersCommand) -> ShareholderDataSyncResult: ...
    def backfill_top10_float_holders(self, cmd: BackfillTop10FloatHoldersCommand) -> ShareholderDataSyncResult: ...
    def backfill_holder_count(self, cmd: BackfillHolderCountCommand) -> ShareholderDataSyncResult: ...
```

- 六个入口内部共用同一编排骨架（认领 → 水位/窗口 → 提取 → 校验 →
  发布 → 终态），仅接口数据类、`data_kind`、水位口径与提取参数不同；
  **失败互不影响**：A 接口方法抛错只写 A 的 FAILED 终态，B/C 方法
  与终态不受牵连（prefect-flow.md §4）。

## 3. 结果

```python
@dataclass(frozen=True)
class ShareholderDataSyncResult:
    data_kind: str                    # TOP10_HOLDERS / TOP10_FLOAT_HOLDERS / HOLDER_COUNT
    run_key: str                      # 本次运行的幂等键（审计）
    target_trade_date: date           # 目标交易日
    status: str                       # SUCCEEDED / FAILED / SKIPPED_NOT_TRADING_DAY
    schedule_slug: str | None
    backfill_batch_id: str | None
    request_count: int; received_count: int
    valid_count: int; added_count: int; updated_count: int
    unchanged_count: int; duplicate_count: int
    invalid_count: int; conflict_count: int
    provider_retry_count: int
    scope_fingerprint: str | None
    error_category: str | None
    started_at: datetime; finished_at: datetime
```

## 4. 编号行为

1. **交易日判断**：目标交易日 = 计划时点（回补为逐日展开的日期）所属的
   交易日历（CN-S，复用项目交易日历领域）；非交易日 →
   `SKIPPED_NOT_TRADING_DAY` 成功终态（FR-001）。
2. **认领**：`run_key` = `<DATA_KIND> + SCHEDULED + schedule_slug +
   scheduled_for_utc + target_trade_date` 或 `<DATA_KIND> + BACKFILL +
   backfill_batch_id + target_trade_date`，其中 `DATA_KIND` ∈
   {`TOP10_HOLDERS`, `TOP10_FLOAT_HOLDERS`, `HOLDER_COUNT`}（审计
   `data_kind` 按接口取值，与 005 每接口一 `data_kind` 的模式一致）；
   MySQL 原子认领 + 租约（2100 秒）防并发重入；`SUCCEEDED` 不可重开
   （FR-011）。
3. **水位与窗口（增量，按接口）**：水位 = 本接口数据的 `max(ann_date)
   FINAL`——`TOP10_HOLDERS`：`shareholder_holding WHERE holder_kind=
   'TOP10'`；`TOP10_FLOAT_HOLDERS`：`shareholder_holding WHERE
   holder_kind='TOP10_FLOAT'`；`HOLDER_COUNT`：`shareholder_count`。
   **必须按接口（kind）分别取水位**：两 top10 接口写入同一张表，
   若用表级水位，先运行的接口会把后运行接口的当日数据一并跳过
   （后者的 `max(ann_date)` 已被前者推进）；表空则水位 = `2024-01-01`。
   窗口 =（水位, 目标日前一自然日]，逐日展开，最多回看 30 天
   （`shareholder_data_window_lookback_days`：start = max(水位+1,
   目标日-30)，表空/水位陈旧时限制单次提取规模，深历史由回补覆盖）；
   无窗口（水位 ≥ 昨日）时直接成功终态，不调用来源。回补忽略水位，
   窗口 = `[start_date, end_date]`。
4. **提取（按接口）**：`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS` 用
   `ann_date=YYYYMMDD`、`HOLDER_COUNT` 用 `start_date=end_date=YYYYMMDD`
   全市场查询（不传 ts_code），逐公告日调用；Provider 内部
   `has_more/offset` 分页至完整（ED-003）。回补提取范围按接口语义：
   `TOP10_*` 仅季度末（报告期）日期触发提取、`HOLDER_COUNT` 逐日
   （research 决策 1）。某个接口某日 0 行属正常披露节奏（FR-014），
   不视为失败。
5. **身份解析**：每条记录按 `provider_security_id` 查 003
   `provider_mappings`（tushare）解析 `stock_id`；未映射 → `invalid_count`
   + 脱敏 issue（类别 `UNKNOWN_STOCK_IDENTITY`），跳过该条（ED-005）。
   本功能不做身份注册（003 主数据为权威，research 决策 3）。
6. **批次校验**：`end_date`/`ann_date` 合法、字段集合与白名单严格相等、
   持仓记录 `holder_name` 非空；完全相同的重复行去重计 `duplicate_count`
   （FR-010）。
7. **修订 vs 冲突**（spec FR-010/ED-010）：INSERT 前读取同键既有行
   （`SELECT ... FINAL`），按 `ann_date` 锚点判定——
   - 值完全相同 → `unchanged_count`；
   - 值不同且**新公告**（入站 `ann_date` > 既有 `ann_date`）→
     正常修订，按最新公告更新，计 `updated_count`，不视为冲突；
   - 值不同且**非新公告**（入站 `ann_date` ≤ 既有 `ann_date`）→
     `RECORD_CONFLICT` 整批失败，不得任意覆盖（FR-010/FR-012）；
   - **批内同日重复披露**（同一业务键同日两次公告、数值不一致，实测
     2026-08-06 温一峰 3709894.0 vs 3709912.0）→ 保留首见记录，后见
     记录隔离为质量 issue（类别 `DUPLICATE_ANN_DISCLOSURE`），不整批
     失败（ED-004 修订，FR-013 不覆盖原则保持）。
8. **发布**：有效行以单 block 批量 INSERT 对应表
   （`ReplacingMergeTree(updated_at)` 同键替换，`updated_at` 批内相同、
   跨重试递增；`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS` 分别写
   `holder_kind` 不同行）；added/updated/unchanged 计数仅审计用途。
9. **终态**：发布成功后在同一 MySQL 事务写 attempt 计数 + run
   `SUCCEEDED`；任何失败写 FAILED 终态；失败/空响应/不完整结果不得清空
   或覆盖已有数据（FR-013）。
10. **重试**：Adapter 内瞬态重试 ≤ 3 次（退避 30/120/300 秒，受 deadline
    约束）；Service 不再重试；Flow `retries=0`。
11. **回补**：区间整体校验（起点 ≥ 2024-01-01、无未来日期、无反向区间）
    → 逐日展开 → 逐日按 (2) 认领（已成功跳过、进行中重试、失败可重试）
    → 逐日独立终态（FR-003/FR-018）；与增量重叠的日期幂等衔接；
    **三个接口的回补相互独立**（同增量隔离语义）。

## 5. 内部查询（消费契约）

```python
def query_shareholder_holdings(
    stock_id: str, holder_kind: str,
    start_date: date, end_date: date,
    limit: int = 1000, offset: int = 0,
) -> list[ShareholderHoldingRecord]: ...   # SELECT ... FINAL，按 (end_date, stock_id, holder_kind, holder_name) 排序

def query_shareholder_count(
    stock_id: str,
    start_date: date, end_date: date,
    limit: int = 1000, offset: int = 0,
) -> list[ShareholderCountRecord]: ...      # SELECT ... FINAL，按 (end_date, stock_id) 排序
```

- 消费方按 `stock_id` + 披露期区间取得记录（FR-016）；本功能不新增
  公共网络入口、UI 或授权机制。
- 记录 DTO 与 Provider 记录数据列同构（去掉 `provider_security_id`，
  加 `stock_id`/`stock_code`）。

## 6. 契约测试要点

- 用假 Provider（固定 records + evidence）与内存/替身存储验证 1~11
  全流程（每接口）：非交易日 SKIPPED、空水位窗口直接成功、身份未映射
  隔离跳过、公告日 0 行正常成功、新公告修订（updated）vs 非新公告冲突
  （conflict 整批失败）、重复同步幂等（run_key 唯一，含接口维度）、
  失败不破坏已有数据。
- **按接口水位测试**：`shareholder_holding` 两 kind 各自推进水位——
  先同步 `TOP10` 后同步 `TOP10_FLOAT`，后者仍覆盖同日公告（替身调用
  计数断言），不因表级水位跳日。
- **隔离测试**：A 接口 Provider 抛错 → 只写 A 的 FAILED 终态；
  B/C 接口正常成功；单独重跑 A 只处理 A 数据。
- 回补：区间校验拒绝未来/反向/早于起点；已成功日期跳过且不重复调用
  Provider（通过替身调用计数断言）；中断后重试只处理失败日期。
- 替换 Provider 实现后 1~11 行为不变（ED-007）。
