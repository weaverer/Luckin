# 当前供应商契约：Tushare broker_recommend

## 1. 唯一允许调用

首期 Tushare Adapter 只允许发出：

```text
api_name = broker_recommend
params   = {"month": "YYYYMM"}
fields   = month,broker,ts_code,name
```

禁止调用 `stock_basic`、行情、成交、财务、预测、公司、指数、基金或任何其他端点。
禁止请求或保存四字段以外的数据。

官方接口资料：<https://tushare.pro/document/2?doc_id=267>。

## 2. 请求规则

- `month` 由 `BrokerRecommendationRequest.target_month` 格式化为 `YYYYMM`。
- 不发送目标月份以外的业务参数。
- Token 通过现有 `TushareClient` 请求信封传递，禁止记录或返回。
- 当前公开契约没有 cursor、offset、limit、broker filter 或排序参数；
  首期不得向请求偷偷加入未验证参数。

## 3. 响应字段

响应字段集合必须精确等于：

```text
month,broker,ts_code,name
```

| Tushare 字段 | 规范语义 | 校验 |
|--------------|----------|------|
| `month` | `recommendation_month` | 6 位 `YYYYMM`，且等于请求月份 |
| `broker` | `broker_name` 原始文本 | 字符串且去空白后非空；不在 Adapter 做别名处理 |
| `ts_code` | 临时 Provider 股票标识 + venue/code | 字符串且后缀可映射 |
| `name` | `stock_name` | 字符串且去首尾空白后非空 |

后缀映射：

```text
.SH → XSHG
.SZ → XSHE
.BJ → XBSE
```

证券代码取后缀前的非空部分。未知后缀、空代码、月份错配、字段缺失或类型错误映射为
`ProviderPayloadError`，不得默认填充或猜测。

## 4. 完整性和返回上限

官方说明单次最多 1,000 行并可循环提取，但当前端点文档只公开 `month` 输入，
未定义可验证续取协议。首期规则：

| 返回行数 | 结果 |
|----------|------|
| 0 | `ProviderIncompleteError(EMPTY_AGGREGATE)` |
| 1–999 | 完成字段校验后可构造正常批次 |
| 1,000 | `ProviderIncompleteError(RESPONSE_CAPPED)` |
| >1,000 | `ProviderPayloadError`，响应违反当前契约上限 |

不得把恰好 1,000 行宣称为完整，不得用猜测的 `offset/limit` 循环，
也不得因失败清空已有推荐。

若供应商后续提供受支持续取方式，变更必须先：

1. 更新本契约与 research。
2. 定义稳定终止条件、重复页检测和跨页冲突规则。
3. 用真实或供应商沙箱契约测试证明全部页可得。
4. 保持 Provider-neutral Port 和 Service 不变。

## 5. Tushare Client 错误映射

| Client 类别/情况 | Provider 异常 | 自动重试 |
|-------------------|---------------|----------|
| 网络连接或读超时 | `ProviderUnavailableError` | 是 |
| HTTP 429/明确短期限流 | `ProviderRateLimitedError` | 是 |
| HTTP 5xx/明确临时不可用 | `ProviderUnavailableError` | 是 |
| Token/权限，包括确定性权限码 | `ProviderAuthenticationError` | 否 |
| 积分、日总量或额度不足 | `ProviderQuotaExceededError` | 否 |
| 参数/API 名错误 | `ProviderRequestError` | 否 |
| 响应信封、字段或行非法 | `ProviderPayloadError` | 否 |
| 成功空表 | `ProviderIncompleteError` | 否 |
| 恰好达到 1,000 行 | `ProviderIncompleteError` | 否 |

不得只依赖供应商原始中文消息判断权限；对已知确定性权限码应有显式映射。
原始消息不得进入业务日志。

## 6. 重试

- 初次调用失败后最多重试 3 次，总调用次数最多 4 次。
- 延迟为 30、120、300 秒；若可靠取得 `Retry-After`，可在不超过整体 deadline 的前提下遵循。
- 每次重试只针对当前月度请求。
- 任一等待或请求将超过 25 分钟整体 deadline 时，立即抛出
  `ProviderDeadlineExceededError`。
- Flow 和 Service 不再重试。

## 7. 请求审计契约

HTTPX `MockTransport` 测试必须捕获所有请求并证明：

- `api_name` 唯一值为 `broker_recommend`。
- `params` 的业务键唯一为 `month`，值与目标月一致。
- `fields` 集合与顺序严格为四字段。
- 无 Token 出现在快照、断言失败文本或日志 fixture。
- 暂时性错误最多产生 4 次总调用；永久错误只产生 1 次。
- 0、999、1,000 行分别得到预期结果。
- 未发生任何范围外端点调用。

## 8. 权限上线门禁

官方端点页与频次/积分总表的权限表述可能随账户等级变化。
部署前必须用目标账户做一次不记录 Token 的权限探测，验证：

- 能调用 `broker_recommend`。
- 允许的频率覆盖初次调用和最多 3 次瞬态重试。
- 实际月度数据未达到 1,000 行，或已有受支持的完整续取方案。

权限或触顶门禁失败时不得上线，不得通过降低校验或记录原始凭据绕过。
