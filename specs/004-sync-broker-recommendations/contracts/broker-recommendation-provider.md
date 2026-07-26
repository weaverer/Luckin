# 供应商无关契约：BrokerRecommendationProvider

## 1. 目的

本契约由 Lucking 项目拥有，是领域服务获取月度券商金股的唯一外部数据边界。
供应商端点、请求字段、SDK 类型、专有代码、积分、频率、分页方式和错误码不得进入
Flow、Service、Repository、ORM 模型或内部查询结果。

## 2. 输入

```python
@dataclass(frozen=True, slots=True)
class BrokerRecommendationRequest:
    target_month: date
```

规则：

- `target_month` 必须是目标自然月第一日。
- Service 从原计划时间转换到 `Asia/Shanghai` 后推导该值；
  Flow 或调用方不得另传一个可能冲突的自由月份。
- Provider 不得根据实际调用时间改变目标月。

## 3. 规范推荐记录

```python
class VenueCode(StrEnum):
    SHANGHAI = "XSHG"
    SHENZHEN = "XSHE"
    BEIJING = "XBSE"


@dataclass(frozen=True, slots=True)
class ProviderBrokerRecommendation:
    recommendation_month: date
    broker_name: str
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    stock_name: str
```

规则：

- DTO 只包含月度券商金股所需语义。
- `provider_security_id` 只供 Service 解析现有股票身份，不得成为推荐业务键或消费字段。
- `broker_name` 在 Adapter 只验证为非空字符串；精确空白规范化由 Service 完成。
- `recommendation_month` 必须等于请求月份。
- venue、代码和 Provider 标识必须相互一致。
- 不允许附带行情、财务、预测、推荐理由、券商排名或原始供应商字段。

## 4. 覆盖证据和批次

```python
@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    request_count: int
    completed_request_count: int
    retry_count: int
    received_count: int
    row_cap: int
    cap_reached: bool
    continuation_exhausted: bool


@dataclass(frozen=True, slots=True)
class ProviderBrokerRecommendationBatch:
    provider_code: str
    target_month: date
    records: tuple[ProviderBrokerRecommendation, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
```

成功批次要求：

- `completed_request_count = request_count`。
- `retry_count ≤ 3`。
- `records` 非空，且 `received_count > 0`。
- `cap_reached = False`。
- `continuation_exhausted = True`。
- `acquired_at` 为 aware UTC。

当前 Tushare Adapter 未获得可验证续取协议，因此单次返回 1,000 行时不得构造成功批次。
替代 Provider 可以通过自身受支持的分页方式返回 1,000 条或更多记录，
但必须提供完整覆盖证据。

## 5. Port

```python
@runtime_checkable
class BrokerRecommendationProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_month(
        self,
        request: BrokerRecommendationRequest,
        *,
        deadline: float,
    ) -> ProviderBrokerRecommendationBatch: ...
```

`deadline` 是单调时钟绝对截止值。Provider 不写数据库、不生成 `stock_id`、
不规范券商空白、不比较 3 日/4 日基线，也不决定同步终态。

## 6. 统一异常

| 异常 | 含义 | 可重试 |
|------|------|--------|
| `ProviderAuthenticationError` | Token 无效或权限不足 | 否 |
| `ProviderRateLimitedError` | 短时频率限制 | 是 |
| `ProviderQuotaExceededError` | 积分、日总量或配额耗尽 | 否 |
| `ProviderUnavailableError` | 网络、超时或上游 5xx | 是 |
| `ProviderRequestError` | 月份或请求不受支持 | 否 |
| `ProviderPayloadError` | 字段、类型、后缀或行结构无效 | 否 |
| `ProviderIncompleteError` | 空结果、触顶或续取不完整 | 否 |
| `ProviderDeadlineExceededError` | 整体获取超过截止时间 | 否 |
| `ProviderConfigurationError` | Provider 未注册或秘密缺失 | 否 |

异常只携带 Provider code、统一类别、可选状态码、请求序号和不超过 500 字符的安全摘要。
不得携带 Token、完整 URL、请求/响应、供应商原始消息或原始行。

## 7. Adapter 职责

Adapter 必须：

- 只访问自身声明的月度券商金股能力。
- 封装鉴权、最小字段、供应商月份格式、续取、限流、有界重试和错误映射。
- 验证响应字段精确、月份一致、代码后缀可映射和覆盖证据完整。
- 对瞬态错误最多额外重试 3 次，且不超过整体 deadline。
- 返回规范 DTO；不解析项目 `stock_id`，不写数据库。
- 不吞掉、伪造或默认填充未知核心字段。

## 8. Registry

```python
BrokerRecommendationProviderFactory = Callable[
    [Settings], BrokerRecommendationProvider
]

BROKER_RECOMMENDATION_PROVIDERS: Mapping[
    str, BrokerRecommendationProviderFactory
] = {
    "tushare": build_tushare_broker_recommendation_provider,
}

def build_broker_recommendation_provider(
    provider_code: str,
    settings: Settings,
) -> BrokerRecommendationProvider: ...
```

- `BROKER_RECOMMENDATION_PROVIDER` 首期默认 `tushare`。
- Registry 只负责构造和显式选择，不自动回退、混合来源或改变目标月。
- 只有选中 Tushare 时才读取其 Token。

## 9. 一致性契约测试

每个真实 Adapter 和 Memory Provider 必须通过同一测试：

1. 返回多个券商、同券商多股票、同股票多券商的合法规范记录。
2. 输出字段严格限制为第 3 节。
3. 目标月份和记录月份始终一致。
4. venue、代码和临时 Provider 标识映射语义一致。
5. 空结果、触顶、续取不完整和请求中断不能伪装为成功。
6. 完全相同重复和冲突由 Service 得到一致输入语义。
7. 所有错误映射为统一异常，秘密与原始 payload 不进入异常。
8. Memory Provider 的 1,000 条完整批次可成功；当前 Tushare 的 1,000 行触顶批次失败。
9. 替代 Provider 对固定 golden cases 产生相同规范摘要。

领域 Service 测试必须使用不导入 `integrations.tushare` 的 Memory Provider。

## 10. Provider 替换

1. 新 Adapter 实现本契约及全部一致性测试。
2. 以非发布影子运行对账目标月份、规范券商、venue、代码、股票简称、数量与摘要。
3. 验证其 Provider 股票标识能通过现有股票映射或规范键解析到相同 `stock_id`。
4. 仅修改配置切换后续计划周期，不重写既有推荐或 `stock_id`。

若新来源不能表达核心字段或证明完整覆盖，必须判定不兼容，不得填入伪造默认值。
