# 内部契约：TradingCalendarService

## 1. 同步

```python
def sync_range(
    sync_mode: SyncMode,
    market_code: MarketCode,
    start_date: date,
    end_date: date,
    as_of_date: date,
) -> SyncResult
```

前置条件：

- `sync_mode` 必须为 `monthly`、`year_end` 或 `manual`。
- `market_code` 首期只能为 `CN-S`。
- `start_date <= end_date`。
- `as_of_date` 是运行时按市场时区确定的业务日期，用于区分必须完整的已发生区间与
  允许尚未公布的未来尾部；测试必须显式注入该值。
- 范围不超过十年。

处理顺序：

1. 使用组合根注入的 `TradingCalendarProvider`。
2. 通过 Provider Port 获取标准日历记录；市场到供应商原生代码的映射由 Adapter 负责。
3. 校验全部来源记录。
4. 在单个事务中 upsert。
5. 提交后返回统计。

返回：

```python
class SyncResult:
    source: str
    sync_mode: SyncMode
    market_code: MarketCode
    start_date: date
    end_date: date
    coverage_end: date
    completeness_status: Literal["COMPLETE", "FUTURE_PARTIAL"]
    missing_future_count: int
    received_count: int
    written_count: int
```

异常：

- `InvalidSyncRequest`：市场或日期参数错误。
- `ProviderAuthenticationError`：所选供应商凭据/权限失败。
- `ProviderRateLimitedError`：所选供应商发生短时频率限制，可由 Prefect 重试。
- `ProviderQuotaExceededError`：账户额度、积分或当日配额耗尽，不重试。
- `ProviderUnavailableError`：网络、超时或上游不可用，可由 Prefect 重试。
- `ProviderConfigurationError`：Provider 未注册、未启用或缺少所需配置。
- `InvalidCalendarPayload`：来源数据不完整或不一致。
- `CalendarPersistenceError`：数据库事务失败，必须回滚。

### 1.1 统一完整性算法

Service 必须使用与具体 Provider 无关的同一算法：

1. 批次非空，日期位于请求闭区间内，按日期唯一且标准字段合法。
2. `required_end = min(end_date, as_of_date)`；当 `start_date <= required_end` 时，
   `start_date..required_end` 必须覆盖每个自然日。
3. 若返回未来记录，它们必须从必需区间下一日连续到最大返回日；若请求范围完全在未来，
   第一条必须为 `start_date`。
4. 最大返回日为 `coverage_end`；其中任何内部缺口均抛出 `InvalidCalendarPayload`。
5. `coverage_end = end_date` 返回 `COMPLETE`；否则仅当缺失部分全部晚于
   `as_of_date` 时返回 `FUTURE_PARTIAL`，并令
   `missing_future_count = end_date - coverage_end` 的自然日数。
6. `FUTURE_PARTIAL` 只写入已验证连续前缀，不合成尾部记录、不删除旧记录；
   下游查询无记录日期得到 `UNKNOWN`。

## 2. 单日状态查询

```python
def get_status(
    market_code: MarketCode,
    calendar_date: date,
) -> CalendarStatusResult
```

返回：

```python
class CalendarStatusResult:
    market_code: MarketCode
    calendar_date: date
    status: Literal["OPEN", "CLOSED", "UNKNOWN"]
    previous_open_date: date | None
    sync_mode: SyncMode | None
    updated_at: datetime | None
```

规则：

- 有记录且 `is_open=true` → `OPEN`。
- 有记录且 `is_open=false` → `CLOSED`。
- 无记录 → `UNKNOWN`，`sync_mode=None`、`updated_at=None`。
- 市场代码格式错误或未启用 → `InvalidMarketCode`，不得返回 `UNKNOWN`。

## 3. 范围查询

```python
def list_range(
    market_code: MarketCode,
    start_date: date,
    end_date: date,
) -> Sequence[TradingCalendarRecord]
```

用于验证与内部任务，按日期升序返回已有记录。不得为缺失日期合成 `CLOSED` 记录。

## 4. 仓储边界

Service 只能通过 `TradingCalendarRepository` 访问 MySQL：

```python
class TradingCalendarRepository(Protocol):
    def upsert_many(
        self,
        records: Sequence[TradingCalendarRecord],
        sync_mode: SyncMode,
        synced_at: datetime,
    ) -> int: ...

    def get(
        self,
        market_code: MarketCode,
        calendar_date: date,
    ) -> TradingCalendarRecord | None: ...

    def list_range(
        self,
        market_code: MarketCode,
        start_date: date,
        end_date: date,
    ) -> Sequence[TradingCalendarRecord]: ...
```

`upsert_many` 必须由调用方事务包裹或自身提供全批次原子事务；不得部分提交。
每个 insert/update 都必须保存本次 `sync_mode`，并覆盖记录原有模式。

## 5. 依赖方向

- `TradingCalendarService` 依赖 `TradingCalendarProvider` 和
  `TradingCalendarRepository` Protocol。
- Provider 和 Repository 均由组合根在构造 Service 时注入；Service 不读取
  `TRADING_CALENDAR_PROVIDER`，也不访问 Registry。
- Service、Flow、Repository 不得导入 `integrations.tushare`。
- Provider Adapter 返回项目标准 DTO 和供应商无关异常。
- Provider 的选择只发生在应用组合根/Registry，不得散布条件分支。
