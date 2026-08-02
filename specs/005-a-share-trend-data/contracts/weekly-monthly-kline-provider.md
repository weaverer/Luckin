# 供应商无关契约：WeeklyMonthlyKlineProvider

## 1. 目的

本契约由 Lucking 项目拥有，是领域服务获取 A 股周/月K线的唯一外部数据边界。
供应商端点、请求字段、SDK 类型、专有代码、积分、频率和错误码不得进入
Flow、Service、Repository、ORM 模型或内部查询结果。

## 2. 输入

```python
class KlineFreq(StrEnum):
    WEEK = "WEEK"
    MONTH = "MONTH"


@dataclass(frozen=True, slots=True)
class KlineRequest:
    freq: KlineFreq
    target_trade_date: date
```

规则：

- `freq` 为必填周期类型，规范值为 `WEEK` 或 `MONTH`。
- `target_trade_date` 是计划时点所属的交易日；Provider 返回的候选记录
  以来源返回的该周期最后交易日标识周期归属，可以等于或早于请求交易日
  （同一周期内多日请求返回相同周期最后交易日）。
- Provider 不得根据实际调用时间改变目标交易日或周期归属。

## 3. 规范K线记录

```python
@dataclass(frozen=True, slots=True)
class ProviderWeeklyMonthlyKline:
    freq: KlineFreq
    trade_date: date          # 周期最后交易日（每周五或月末最后一个交易日）
    end_date: date | None     # 来源计算截至日期；与 trade_date 一致时为空
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vol: Decimal
    amount: Decimal
    change: Decimal
    pct_chg: Decimal
```

规则：

- DTO 携带未复权开/高/低/收价，以及成交量（手）、
  成交额（千元）、涨跌额、涨跌幅与计算截至日期。
- `provider_security_id` 只供 Service 解析现有股票身份，不得成为业务键或消费字段。
- 同一周期多日同步返回相同 `trade_date`，唯一键保证幂等更新。
- 未复权价格缺失的记录必须作为无效候选隔离；来源未提供时不得伪造默认值。
- `end_date` 仅在部署账户验证返回且与 `trade_date` 不同时填入。
- 不允许附带任何范围外字段。

## 4. 覆盖证据和批次

```python
@dataclass(frozen=True, slots=True)
class ProviderKlineBatch:
    provider_code: str
    freq: KlineFreq
    target_trade_date: date
    records: tuple[ProviderWeeklyMonthlyKline, ...]
    evidence: RetrievalEvidence  # 结构与 DailyQuoteProvider 一致
    acquired_at: datetime
```

成功批次要求：

- `completed_request_count = request_count`；未检测到重复页，续取位置已前进。
- 所有记录 `freq` 与请求一致；`trade_date` 不晚于请求交易日。
- 行数未达到 `page_limit`（或已验证的续取达到终止条件），否则必须标记为不完整并失败。

## 5. 错误

Adapter 必须把来源错误映射为统一异常类别（与 DailyQuoteProvider 相同集合）；
瞬态故障由 Adapter 在初次调用后重试最多 3 次，确定性错误不重试。
周期与来源返回不一致映射为 `PERIOD_MISMATCH`。

## 6. 契约测试要点

- 输入周期全量K线记录转换后字段、freq 和周期归属一致；未复权价格完整。
- 空响应映射为 `EMPTY_AGGREGATE`；达到上限且无法证明完整映射为 `RESPONSE_CAPPED`。
- 同周期多日请求返回相同 `trade_date` 的记录可正常去重更新。
- 未复权价格缺失的记录作为 `INVALID_FIELD` 无效候选被隔离。
- 供应商字段泄漏（额外字段进入 DTO）必须在契约测试中失败。
