# 内部契约：指数技术因子同步服务

> 编排契约：Service 对外的命令、结果与内部查询。Service 只依赖
> `index-factor-provider.md`（Port）、交易日历、身份注册与存储抽象；
> 不依赖任何供应商细节（宪章 II）。

## 1. 目的

定义指数技术因子同步的领域编排：交易日判断、身份注册与解析、批次校验、
ClickHouse 发布、MySQL 审计终态。同步运行与消费两侧共享本契约的语义。

## 2. 命令

```python
@dataclass(frozen=True)
class ScheduledIndexFactorSyncCommand:
    scheduled_at: datetime            # 原计划时点（UTC）
    schedule_slug: str                # 计划标识（如 "index-factor-sync"）
    now: datetime | None = None       # 注入当前时间（测试用）

@dataclass(frozen=True)
class BackfillIndexFactorCommand:
    start_date: date                  # 起（含）；不得早于 2024-01-01
    end_date: date                    # 止（含）；不得晚于今天、不得早于 start_date
    backfill_batch_id: str            # 回补批次标识（run_key 输入）
    now: datetime | None = None
```

## 3. 结果

```python
@dataclass(frozen=True)
class IndexFactorSyncResult:
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
   交易日历（CN-S，复用项目交易日历领域）；非交易日 → `SKIPPED_NOT_TRADING_DAY`
   成功终态（FR-001）。
2. **认领**：`run_key` = `data_kind + SCHEDULED + schedule_slug + scheduled_for_utc + target_trade_date`
   或 `data_kind + BACKFILL + backfill_batch_id + target_trade_date`；MySQL
   原子认领 + 租约（2100 秒）防并发重入；`SUCCEEDED` 不可重开（FR-011）。
3. **身份解析与注册**：每条记录按 `provider_security_id` 后缀白名单
   （`.SH/.SZ/.CSI/.SI`）解析 `index_id`；合法则幂等 upsert
   `index_current`/`index_provider_mapping`（MySQL 唯一约束兜底）；
   非法后缀、空代码 → `invalid_count` + 脱敏 issue，跳过该条（ED-004）。
4. **批次校验**：交易日归属一致、基础行情必需字段完整、因子字段缺失以
   NULL 保存；完全相同重复行去重计 `duplicate_count`；同键字段冲突抛
   `RECORD_CONFLICT` 整批失败（FR-010/FR-011）。
5. **发布**：有效行以单 block 批量 INSERT `index_factor`
   （`ReplacingMergeTree(updated_at)` 同键替换）；INSERT 前读既有同键行
   计算 added/updated/unchanged（仅审计计数）。
6. **终态**：发布成功后在同一 MySQL 事务写 attempt 计数 + run `SUCCEEDED`；
   任何失败写 FAILED 终态；失败/空响应/不完整结果不得清空或覆盖已有数据
   （FR-013）。全市场空响应与“个别指数无数据”区分：后者正常成功，
   前者由完整性门禁判定失败（FR-014/ED-004）。
7. **重试**：Adapter 内瞬态重试 ≤ 3 次（退避 30/120/300 秒，受 deadline
   约束）；Service 不再重试；Flow `retries=0`。
8. **回补**：区间整体校验（起点 ≥ 2024-01-01、无未来日期、无反向区间）
   → 交易日历逐日展开 → 逐日按 (2) 认领（已成功跳过、进行中重试、
   失败可重试）→ 逐日独立终态（FR-003/FR-018）。

## 5. 内部查询（消费契约）

```python
def query_index_factors(
    index_id: str,              # 规范指数标识
    start_date: date, end_date: date,
    limit: int = 1000, offset: int = 0,
) -> list[IndexFactorRecord]: ...   # SELECT ... FINAL，按 (trade_date, index_id) 排序
```

- 消费方按 `index_id` + 日期区间取得因子记录（FR-016）；本功能不新增
  公共网络入口、UI 或授权机制。
- 记录 DTO 与 `ProviderIndexFactorRecord` 数据列同构（去掉
  `provider_security_id`，加 `index_id`/`index_code`）。

## 6. 契约测试要点

- 用假 Provider（固定 records + evidence）与内存/替身存储验证 1~8 全流程；
  非交易日 SKIPPED、身份非法跳过、空响应 vs 无数据区分、冲突整批失败、
  重复同步幂等（run_key 唯一）、失败不破坏已有数据。
- 回补：区间校验拒绝未来/反向/早于起点；已成功日期跳过且不重复调用
  Provider（通过替身调用计数断言）；中断后重试只处理失败日期。
- 替换 Provider 实现后 1~8 行为不变（ED-006）。
