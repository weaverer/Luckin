# 供应商无关契约：TradingCalendarProvider

## 1. 目的

本契约由 Lucking 项目拥有，是领域服务与第三方数据源之间唯一允许的依赖边界。
Tushare、未来其他 HTTP API 或测试替身都必须实现同一契约。

Provider 专有请求参数、响应字段、SDK 类型和错误不得越过 Adapter 进入 Flow、
Service、Repository 或数据库模型。

## 2. 标准模型

```python
@dataclass(frozen=True)
class ProviderCalendarDay:
    market_code: MarketCode
    calendar_date: date
    is_open: bool
    previous_open_date: date | None
    source: str
    source_market: str
```

规则：

- `market_code` 是项目标准代码，例如 `CN-S`。
- `source` 是 Provider 稳定标识，例如 `tushare`，不能使用类名或 URL。
- `source_market` 是 Provider 原生市场标识，例如 `SSE`。
- 模型不得保存供应商原始 JSON、请求 ID、Token 或未使用字段。

## 3. Port

```python
class TradingCalendarProvider(Protocol):
    @property
    def provider_code(self) -> str: ...

    def fetch_calendar(
        self,
        market_code: MarketCode,
        start_date: date,
        end_date: date,
    ) -> Sequence[ProviderCalendarDay]: ...
```

Provider 必须：

- 在调用外部服务前验证是否支持目标 `market_code`。
- 返回请求闭区间内所有可用的开市和休市日期。
- 将供应商字段转换为标准 Python 类型。
- 不写数据库、不计算月度/年末窗口、不记录同步模式。
- 不吞掉错误；必须映射为第 4 节的项目异常。

## 4. 供应商无关异常

| 异常 | 含义 | 可重试 |
|------|------|--------|
| `ProviderAuthenticationError` | 凭据无效或权限不足 | 否 |
| `ProviderRateLimitedError` | 短时调用频率限制，退避后可能恢复 | 是 |
| `ProviderQuotaExceededError` | 账户额度、积分或当日配额耗尽 | 否 |
| `ProviderUnavailableError` | 网络、超时或上游 5xx | 是 |
| `ProviderRequestError` | 项目传入参数无效 | 否 |
| `ProviderPayloadError` | 上游响应缺字段、类型错误或无法映射 | 否 |
| `ProviderConfigurationError` | Provider 未注册或配置缺失 | 否 |

异常只允许携带供应商代码、错误类别、可选状态码和脱敏摘要。

## 5. Registry

```python
ProviderFactory = Callable[[Settings], TradingCalendarProvider]

PROVIDERS: Mapping[str, ProviderFactory] = {
    "tushare": build_tushare_trading_calendar_provider,
}

def build_trading_calendar_provider(
    provider_code: str,
    settings: Settings,
) -> TradingCalendarProvider: ...
```

规则：

- `TRADING_CALENDAR_PROVIDER` 默认 `tushare`。
- 未注册的代码立即抛出 `ProviderConfigurationError`。
- Registry 只负责构造和选择，不包含供应商业务分支或回退策略。
- 首期不自动故障转移，不做跨供应商数据合并。

## 6. 一致性契约测试

每个 Adapter 和内存测试替身必须通过同一测试套件：

1. 支持的市场和日期范围返回标准模型。
2. 开市、休市和上一交易日语义一致。
3. 返回结果不含供应商原始类型。
4. 凭据、短时限流、额度耗尽、暂时故障、参数和载荷错误映射到正确异常。
5. Provider 标识和原生市场标识非空且稳定。
6. 不执行数据库写入或日期窗口推导。

完整性规则由领域服务统一执行：Provider 返回请求范围内当前可用的记录，不得自行填充
未公布日期或将其标记为休市。契约测试必须允许 Provider 返回连续的未来前缀，
并由 Service 测试验证其 `COMPLETE/FUTURE_PARTIAL` 判定。

领域服务测试必须至少使用一个不依赖 Tushare 的内存 Provider，证明替换数据源时
Flow、Service、Repository 无需修改。

## 7. 新增或替换数据源

新增 Provider 必须：

1. 新建独立 Adapter 包。
2. 完成市场代码到供应商原生标识的映射。
3. 实现本契约并通过一致性测试。
4. 在 Registry 注册稳定 `provider_code`。
5. 增加秘密配置和脱敏测试。
6. 在测试环境对完整日期窗口进行验证后，才切换
   `TRADING_CALENDAR_PROVIDER`。

若新来源无法表达开市、休市或上一交易日，必须先修订规格与本契约，不得在 Adapter
中静默伪造。
