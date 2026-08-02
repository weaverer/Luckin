# 供应商无关契约：DailyQuoteProvider

## 1. 目的

本契约由 Lucking 项目拥有，是领域服务获取 A 股未复权日线行情的唯一外部数据边界。
供应商端点、请求字段、SDK 类型、专有代码、积分、频率、分页方式和错误码不得进入
Flow、Service、Repository、ORM 模型或内部查询结果。

## 2. 输入

```python
@dataclass(frozen=True, slots=True)
class DailyQuoteRequest:
    target_trade_date: date
```

规则：

- `target_trade_date` 必须是计划时点所属的交易日（由 Service 从原计划时间
  转换到 `Asia/Shanghai` 后结合交易日历确定），或回补显式指定的已校验交易日。
- Provider 不得根据实际调用时间改变目标交易日。

## 3. 规范行情记录

```python
class VenueCode(StrEnum):
    SHANGHAI = "XSHG"
    SHENZHEN = "XSHE"
    BEIJING = "XBSE"


@dataclass(frozen=True, slots=True)
class ProviderDailyQuote:
    trade_date: date
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal
    change: Decimal
    pct_chg: Decimal
    vol: Decimal
    amount: Decimal
```

规则：

- DTO 只包含未复权日线行情所需语义；成交量为手、成交额为千元。
- `provider_security_id` 只供 Service 解析现有股票身份，不得成为业务键或消费字段。
- `trade_date` 必须等于请求交易日。
- venue、代码和 Provider 标识必须相互一致。
- 停牌股票当日无记录：Provider 返回的候选集合不包含该股票，不产生空记录。
- 不允许附带除规范字段以外的供应商字段（含盘后成交量/成交额，
  除非经计划评审纳入规范模型）。

## 4. 覆盖证据和批次

```python
@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    request_count: int
    completed_request_count: int
    retry_count: int
    page_count: int
    page_limit: int
    last_page_count: int
    received_count: int
    pagination_enabled: bool
    continuation_exhausted: bool
    repeated_page_detected: bool


@dataclass(frozen=True, slots=True)
class ProviderDailyQuoteBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderDailyQuote, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
```

成功批次要求：

- `completed_request_count = request_count`。
- 未检测到重复页，续取位置已前进（多批时）。
- `records` 中所有记录 `trade_date` 与目标交易日一致。
- 行数未达到 `page_limit`（或已验证的续取达到终止条件），
  否则必须标记为不完整并失败。

## 5. 错误

Adapter 必须把来源错误映射为统一异常类别，至少包括：

```text
PROVIDER_RATE_LIMITED   PROVIDER_UNAVAILABLE   PROVIDER_DEADLINE
AUTHENTICATION          QUOTA_EXCEEDED         RESPONSE_CAPPED
```

瞬态故障（网络/超时、HTTP 429、明确短期限流、5xx）由 Adapter 在初次调用后
重试最多 3 次；认证、权限、额度、参数、载荷和空结果错误不自动重试。

## 6. 契约测试要点

- 输入交易日全量记录转换后字段、单位和交易日一致。
- 空响应映射为 `EMPTY_AGGREGATE`；达到上限且无法证明完整映射为 `RESPONSE_CAPPED`。
- 停牌股票不产生记录；交易日错配记录产生 `TRADE_DATE_MISMATCH` 候选。
- 供应商字段泄漏（额外字段进入 DTO）必须在契约测试中失败。
