# 供应商无关契约：DailyBasicProvider

## 1. 目的

本契约由 Lucking 项目拥有，是领域服务获取 A 股每日基本面指标的唯一外部数据边界。
供应商端点、请求字段、SDK 类型、专有代码、积分、频率和错误码不得进入
Flow、Service、Repository、ORM 模型或内部查询结果。

## 2. 输入

```python
@dataclass(frozen=True, slots=True)
class DailyBasicRequest:
    target_trade_date: date
```

规则：

- `target_trade_date` 必须是计划时点所属的交易日（由 Service 从原计划时间
  转换到 `Asia/Shanghai` 后结合交易日历确定），或回补显式指定的已校验交易日。
- Provider 不得根据实际调用时间改变目标交易日。

## 3. 规范指标记录

```python
@dataclass(frozen=True, slots=True)
class ProviderDailyBasic:
    trade_date: date
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    pe: Decimal | None
    pe_ttm: Decimal | None
    pb: Decimal | None
    ps: Decimal | None
    ps_ttm: Decimal | None
    dv_ratio: Decimal | None
    dv_ttm: Decimal | None
    total_share: Decimal | None
    float_share: Decimal | None
    free_share: Decimal | None
    total_mv: Decimal | None
    circ_mv: Decimal | None
    turnover_rate: Decimal | None
    turnover_rate_f: Decimal | None
    volume_ratio: Decimal | None
    limit_status: int | None
```

规则：

- DTO 只包含基本面指标所需语义；来源 19 个字段中的收盘价 `close`
  与日线行情语义重复，遵循单表事实原则不进入本契约。
- `provider_security_id` 只供 Service 解析现有股票身份，不得成为业务键或消费字段。
- `trade_date` 必须等于请求交易日。
- 指标字段为 `None` 表示来源未返回该值（亏损公司 PE/PB 等），
  属于正常业务结果，Service 以 NULL 保存；与“必需身份字段缺失”区分。
- 不允许附带任何范围外字段。

## 4. 覆盖证据和批次

```python
@dataclass(frozen=True, slots=True)
class ProviderDailyBasicBatch:
    provider_code: str
    target_trade_date: date
    records: tuple[ProviderDailyBasic, ...]
    evidence: RetrievalEvidence  # 结构与 DailyQuoteProvider 一致
    acquired_at: datetime
```

成功批次要求：

- `completed_request_count = request_count`；未检测到重复页，续取位置已前进。
- 所有记录 `trade_date` 与目标交易日一致。
- 行数未达到 `page_limit`（或已验证的续取达到终止条件），否则必须标记为不完整并失败。

## 5. 错误

Adapter 必须把来源错误映射为统一异常类别（与 DailyQuoteProvider 相同集合）；
瞬态故障由 Adapter 在初次调用后重试最多 3 次，确定性错误不重试。

## 6. 契约测试要点

- 输入交易日全量指标记录转换后字段和交易日一致；亏损公司空字段保持为 `None`。
- 空响应映射为 `EMPTY_AGGREGATE`；达到上限且无法证明完整映射为 `RESPONSE_CAPPED`。
- 必需身份字段（交易日、股票身份、代码）缺失的记录作为无效候选被隔离。
- 供应商字段泄漏（额外字段进入 DTO）必须在契约测试中失败。
