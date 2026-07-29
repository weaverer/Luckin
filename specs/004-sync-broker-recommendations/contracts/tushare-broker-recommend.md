# 当前供应商契约：Tushare broker_recommend

## 1. 唯一允许调用

首期 Tushare Adapter 只允许发出：

```text
api_name = broker_recommend
params   = {"month": "YYYYMM"}
# 仅在续取契约验证并启用后：
params   = {"month": "YYYYMM", "limit": 1000, "offset": 0}
fields   = month,broker,ts_code,name
```

禁止调用 `stock_basic`、行情、成交、财务、预测、公司、指数、基金或任何其他端点。
禁止请求或保存四字段以外的数据。

官方接口资料：<https://tushare.pro/document/2?doc_id=267>。

## 2. 请求规则

- `month` 由 `BrokerRecommendationRequest.target_month` 格式化为 `YYYYMM`。
- 不发送目标月份以外的业务筛选参数。
- Token 通过现有 `TushareClient` 请求信封传递，禁止记录或返回。
- 当前公开契约没有 cursor、offset、limit、broker filter 或排序参数；
  `limit/offset` 只有完成第 8 节真实续取门禁后才能启用。
- 分页关闭时只发送 `month`；分页启用时每次发送固定 `limit=1000`
  和从 0 开始、每次增加 1000 的 `offset`，不得混用 cursor 或券商拆分。

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

## 4. 完整性、分页和返回上限

官方说明单次最多 1,000 行并可循环提取，但当前端点文档只公开 `month` 输入，
未定义可验证续取协议。因此 Adapter 实现分页能力，但生产启用受第 8 节门禁控制。

分页启用后的算法：

```text
offset = 0
pages = []
previous_full_page_digest = None

loop:
  page = request(month, limit=1000, offset=offset)
  validate exact fields and every row
  reject len(page) > limit
  reject a repeated full-page digest
  append page

  if len(page) < limit:
      reject empty aggregate when this is the first page
      continuation_exhausted = true
      break

  offset += limit
  reject offset not advanced or page_count > max_pages
```

规则：

- 0 行首屏：`ProviderIncompleteError(EMPTY_AGGREGATE)`。
- 1–999 行首屏：校验后成功。
- 1,000 行页面：不得结束，必须请求下一 offset。
- 总量恰好为 1,000 的整数倍时，额外取得 0 行终止页后可以成功。
- 任意页面超过 1,000 行：`ProviderPayloadError`。
- 重复整页、offset 未前进或超过最大页数：`ProviderIncompleteError`。
- 任一页面请求或校验失败：整个月份失败，不返回部分成功批次。
- 跨页完全相同行由 Service 去重计数；同一业务键跨页冲突导致整批失败。
- `received_count` 是全部数据页面的原始行数之和，不包含空终止页。

分页关闭或真实续取门禁未通过时：

- 0 行仍失败；
- 1–999 行可成功；
- 恰好 1,000 行为 `ProviderIncompleteError(RESPONSE_CAPPED)`；
- 不得发送未经验证的 `limit/offset`，也不得因失败清空已有推荐。

`RetrievalEvidence` 必须记录请求数、重试数、页面数、页面上限、末页行数、
累计原始行数和 `continuation_exhausted`；不得只用总行数猜测完整性。

如果供应商未来提供正式 cursor 或其他续取方式，可以替换本 Adapter 内的
`limit/offset` 策略，但必须保留满页继续、短页/明确 exhausted 结束、
重复页保护和 Provider-neutral Port。

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
| 分页关闭时恰好达到 1,000 行 | `ProviderIncompleteError` | 否 |
| 重复页、未前进、超过最大页数 | `ProviderIncompleteError` | 否 |

不得只依赖供应商原始中文消息判断权限；对已知确定性权限码应有显式映射。
原始消息不得进入业务日志。

## 6. 重试

- 整个月份获取共享最多 3 次额外重试预算；单页场景总调用次数最多 4 次。
- 延迟为 30、120、300 秒；若可靠取得 `Retry-After`，可在不超过整体 deadline 的前提下遵循。
- 每次重试只针对当前月份的当前 offset；成功后继续下一页。
  已消耗的重试预算不会在进入下一页时重置。
- 任一等待或请求将超过 25 分钟整体 deadline 时，立即抛出
  `ProviderDeadlineExceededError`。
- Flow 和 Service 不再重试。

## 7. 请求审计契约

HTTPX `MockTransport` 测试必须捕获所有请求并证明：

- `api_name` 唯一值为 `broker_recommend`。
- 分页关闭时 `params` 的业务键唯一为 `month`。
- 分页启用时 `params` 精确为 `month/limit/offset`，其中月份不变，
  `limit=1000`，offset 为 `0,1000,2000...`。
- `fields` 集合与顺序严格为四字段。
- 无 Token 出现在快照、断言失败文本或日志 fixture。
- 单页暂时性错误最多产生 4 次总调用；多页场景的额外重试总数仍不超过 3；
  永久错误所在页面只调用 1 次。
- 0、999、分页关闭的 1,000 行分别得到预期结果。
- `1,000/1,000/500` 得到 2,500 条成功批次；`1,000/0` 得到恰好
  1,000 条成功批次；重复首页、offset 未前进、超过最大页数和中途失败均失败。
- 未发生任何范围外端点调用。

## 8. 权限上线门禁

官方端点页与频次/积分总表的权限表述可能随账户等级变化。
部署前必须用目标账户做一次不记录 Token 的权限探测，验证：

- 能调用 `broker_recommend`。
- 允许的频率覆盖初次调用和最多 3 次瞬态重试。
- `limit/offset` 参数被端点接受且 `limit` 确实限制页面数量。
- offset 前进后不会重复首页；`1,000` 满页会出现不同后续页或可靠空终止页。
- 在受控月份对分页聚合结果进行重复探测时，数量和规范摘要稳定。

验证证据必须记录账户环境、目标月份、页面数、每页数量和脱敏摘要，不记录 Token 或原始响应。
门禁通过后才可设置 `BROKER_RECOMMENDATION_TUSHARE_PAGINATION_ENABLED=true`。
权限或分页门禁失败时，不得为可能触顶的生产月份启用该 Adapter；必须保持满页失败
或切换能证明完整性的 Provider，不得通过降低校验或记录原始凭据绕过。
