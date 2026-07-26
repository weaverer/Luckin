# 供应商无关契约：StockListProvider

## 1. 目的

本契约由 Lucking 项目拥有，是领域服务获取股票列表的唯一外部数据边界。
供应商端点、请求字段、SDK 类型、专有状态、分页方式和错误码不得进入 Flow、Service、
Repository 或核心数据模型。

## 2. 输入

```python
class ScopeCode(StrEnum):
    CN_STOCK = "CN-S"

class VenueCode(StrEnum):
    SHANGHAI = "XSHG"
    SHENZHEN = "XSHE"
    BEIJING = "XBSE"

@dataclass(frozen=True, slots=True)
class StockListRequest:
    scope_code: ScopeCode
```

首期只允许 `CN-S`。该 scope 的完整范围固定为 `XSHG/XSHE/XBSE` 三个 venue，
请求方不得传入或配置 venue 子集；Provider 必须证明三者均被覆盖。

## 3. 规范股票记录

```python
class ListingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"
    SUSPENDED = "SUSPENDED"
    PENDING = "PENDING"

@dataclass(frozen=True, slots=True)
class ProviderStockRecord:
    provider_security_id: str
    venue_code: VenueCode
    security_code: str
    display_name: str
    currency_code: str
    listing_status: ListingStatus
    listed_on: date | None
    delisted_on: date | None
```

规则：

- DTO 只包含规格允许的股票列表语义。
- `provider_security_id` 只供映射，不得成为下游业务主键。
- 不允许附带行业、地域、公司、行情、成交、财务、估值或其他字段。
- 日期为无时区日历日期；获取瞬间由批次结果单独记录。

## 4. 覆盖证明与结果

```python
@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    segment_count: int
    completed_segment_count: int
    capped_segment_count: int
    received_count: int

@dataclass(frozen=True, slots=True)
class ProviderStockList:
    provider_code: str
    scope_code: ScopeCode
    records: tuple[ProviderStockRecord, ...]
    evidence: RetrievalEvidence
    acquired_at: datetime
```

- `acquired_at` 必须为 UTC aware。
- Provider 只有在全部所需 segment 成功后才能返回正常结果。
- 首期完整结果必须覆盖固定的 `XSHG/XSHE/XBSE`，不得以配置缩小范围。
- `completed_segment_count` 必须等于 `segment_count`。
- `capped_segment_count` 必须为 0；命中来源上限的 segment 不可宣称完整。
- `records` 聚合后不得为空。
- segment 的供应商参数和值不得出现在公共 DTO；只暴露数量证明。

## 5. Port

```python
@runtime_checkable
class StockListProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_stock_list(
        self,
        request: StockListRequest,
        *,
        deadline: float,
    ) -> ProviderStockList: ...
```

`deadline` 是单调时钟绝对截止值，用于确保 Provider 获取不会占满 30 分钟业务预算。
Provider 不写数据库、不生成项目 `stock_id`、不比较上一成功列表，也不决定同步终态。

## 6. 统一异常

| 异常 | 含义 | 可重试 |
|------|------|--------|
| `ProviderAuthenticationError` | Token 无效或权限不足 | 否 |
| `ProviderRateLimitedError` | 短时频率限制 | 是 |
| `ProviderQuotaExceededError` | 额度、积分或日配额耗尽 | 否 |
| `ProviderUnavailableError` | 网络、超时或上游 5xx | 是 |
| `ProviderRequestError` | 范围或请求不受支持 | 否 |
| `ProviderPayloadError` | 字段、类型、枚举或行结构无效 | 否 |
| `ProviderIncompleteError` | segment 缺失、触顶或聚合为空 | 否 |
| `ProviderDeadlineExceededError` | 获取超过整体截止时间 | 否 |
| `ProviderConfigurationError` | Provider 未注册或秘密缺失 | 否 |

异常只携带 Provider 代码、统一类别、可选状态码、segment 序号和不超过 500 字符的
脱敏摘要。

## 7. Adapter 职责

Adapter 必须：

- 只访问自身声明的股票列表能力。
- 封装鉴权、请求分区、供应商字段、限流、有界重试、错误转换和日期解析。
- 显式请求最小字段集合，并验证返回字段精确一致。
- 验证每条记录属于当前 segment。
- 返回规范 DTO 和通用覆盖证明。
- 不吞掉、伪造或默认填充未知核心字段。

## 8. Registry

```python
StockListProviderFactory = Callable[[Settings], StockListProvider]

STOCK_LIST_PROVIDERS: Mapping[str, StockListProviderFactory] = {
    "tushare": build_tushare_stock_list_provider,
}

def build_stock_list_provider(
    provider_code: str,
    settings: Settings,
) -> StockListProvider: ...
```

- `STOCK_LIST_PROVIDER` 首期默认 `tushare`。
- Registry 只构造和选择，不做自动回退、来源混合或供应商状态判断。
- 只有选中 Tushare 时才读取并解密其 Token。
- 实现顺序先提供通用 Registry 选择机制，再实现 Adapter，最后注册
  `build_tushare_stock_list_provider`，禁止基础 Registry 引用尚不存在的工厂。

## 9. 一致性契约测试

每个真实 Adapter 和内存替身必须通过同一测试：

1. 返回三个 venue 和四种规范状态的合法记录。
2. 输出严格限制为第 3 节字段。
3. 覆盖证明能区分完成、segment 缺失、触顶和聚合为空。
4. Provider ID、规范 venue + code 的唯一性语义一致。
5. 未知币种、状态、venue、非法日期和身份冲突不会被默认处理。
6. 所有错误映射为统一异常，秘密和原始响应不进入异常。
7. 替代 Provider 对固定 golden cases 产生相同规范列表语义。

领域 Service 测试必须使用不导入 `integrations.tushare` 的内存 Provider。

## 10. Provider 替换

1. 新 Adapter 实现本契约和全部一致性测试。
2. 以非发布影子运行对账规范 venue、代码、币种、状态和日期。
3. 根据唯一 venue + code 建立新 Provider 映射，人工处理身份冲突。
4. 仅修改配置切换后续计划周期。

若新来源无法表达核心字段或完整覆盖证明，必须判定不兼容，不得填入伪造默认值。
