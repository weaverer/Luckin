# 供应商适配契约：Tushare stock_basic

## 1. 硬性范围

`TushareStockListProvider` 只允许通过现有通用 `TushareClient` 调用：

```text
api_name = stock_basic
```

任何其他 `api_name` 都属于范围违反并使契约测试失败。本 Adapter 不调用行情、成交、
停复牌、公司、财务、指标、交易日历或其他端点。

## 2. 请求字段白名单

每次请求的 `fields` 必须按以下顺序精确等于：

```text
ts_code,symbol,name,exchange,curr_type,list_status,list_date,delist_date
```

不得请求：

```text
area,industry,fullname,enname,cnspell,market,is_hs,act_name,act_ent_type
```

也不得使用未获规格授权的默认全字段响应。

## 3. 分区矩阵

每次完整获取必须执行下列 12 个 segment：

| `exchange` | `list_status` |
|------------|---------------|
| `SSE` | `L` |
| `SSE` | `D` |
| `SSE` | `P` |
| `SSE` | `G` |
| `SZSE` | `L` |
| `SZSE` | `D` |
| `SZSE` | `P` |
| `SZSE` | `G` |
| `BSE` | `L` |
| `BSE` | `D` |
| `BSE` | `P` |
| `BSE` | `G` |

首期 `CN-S` 固定包含三个交易所，不能通过配置删减上述矩阵；缺少任一交易所或状态分区
均为不完整结果。

请求 `params` 只包含当前 `exchange` 与 `list_status`；不传 `ts_code`、`name`、
`market` 或 `is_hs`。

执行顺序必须固定，便于重试、日志和契约审计。只重试当前失败 segment，
已经成功并在内存验证的 segment 不重复调用。

## 4. 成功空 segment

通用 Client 增加：

```python
def call(
    api_name: str,
    *,
    params: Mapping[str, Any],
    fields: tuple[str, ...],
    allow_empty: bool = False,
) -> TushareTable: ...
```

- 默认 `False`，保持现有交易日历空结果失败。
- 本 Adapter 显式传 `allow_empty=True`，允许某个交易所/状态组合为零行。
- Client 仍必须验证成功信封和精确字段集合。
- 12 个 segment 合并后为空，由 Adapter 抛出 `ProviderIncompleteError`。

## 5. 字段映射

| Tushare 字段 | 规范字段 | 规则 |
|--------------|----------|------|
| `ts_code` | `provider_security_id` | 非空；后缀必须匹配交易所 |
| `symbol` | `security_code` | 非空；保留前导零 |
| `name` | `display_name` | 去除首尾空白后非空 |
| `exchange=SSE` | `venue_code` | `XSHG` |
| `exchange=SZSE` | `venue_code` | `XSHE` |
| `exchange=BSE` | `venue_code` | `XBSE` |
| `curr_type=CNY` | `currency_code` | `CNY` |
| `list_status=L` | `listing_status` | `ACTIVE` |
| `list_status=D` | `listing_status` | `DELISTED` |
| `list_status=P` | `listing_status` | `SUSPENDED` |
| `list_status=G` | `listing_status` | `PENDING` |
| `list_date` | `listed_on` | 空或 `YYYYMMDD` |
| `delist_date` | `delisted_on` | 空或 `YYYYMMDD` |

`ts_code` 后缀规则：

| `exchange` | 后缀 |
|------------|------|
| `SSE` | `.SH` |
| `SZSE` | `.SZ` |
| `BSE` | `.BJ` |

未知、空或不匹配的交易所、状态、币种及后缀不得默认修复。

## 6. 日期规则

- 非空日期必须严格为 8 位 `YYYYMMDD` 且是真实日历日期。
- `ACTIVE/SUSPENDED/DELISTED` 要求 `listed_on` 非空。
- `PENDING` 允许 `listed_on` 为空。
- `DELISTED` 要求 `delisted_on` 非空。
- 两个日期同时存在时 `delisted_on >= listed_on`。
- 若真实脱敏 fixture 与以上规则冲突，必须先更新契约和规格评估，不得静默填值。

## 7. 完整性与重复

每个 segment：

- 响应字段集合必须与第 2 节完全一致。
- 每一行的 `exchange/list_status` 必须与请求 segment 一致。
- 行数必须 `< 6000`；`== 6000` 抛出 `ProviderIncompleteError`。

聚合结果：

- 12 个 segment 必须全部完成。
- 总记录数必须大于 0。
- 同一 `ts_code` 或规范 venue + code 出现多条完全相同记录时允许 Service 去重。
- 同一键出现字段差异时由 Service 判定冲突并整批拒绝。

## 8. 重试与截止时间

- 网络、HTTP 429、明确短时频率限制和 5xx：当前 segment 最多额外重试 3 次，
  退避 30、120、300 秒。
- 每次等待前检查单调时钟 `deadline`；等待或下一次调用将越过截止时间时立即失败。
- 认证、权限、额度、请求、载荷、未知枚举、触顶和完整性错误不重试。
- Flow 不对完整 12 segment 操作再次重试。

## 9. 错误映射

| Tushare Client 类别 | Provider 异常 |
|---------------------|---------------|
| `NETWORK`、`UPSTREAM_UNAVAILABLE` | `ProviderUnavailableError` |
| `RATE_LIMITED` | `ProviderRateLimitedError` |
| `QUOTA_EXHAUSTED` | `ProviderQuotaExceededError` |
| `AUTHENTICATION` | `ProviderAuthenticationError` |
| `BAD_REQUEST`、`UPSTREAM_BUSINESS` | `ProviderRequestError` |
| `INVALID_PAYLOAD` | `ProviderPayloadError` |
| 非允许空调用的 `EMPTY_PAYLOAD` | `ProviderPayloadError` |

错误上下文可包含 segment 序号、规范错误类别和 HTTP 状态码，不得包含 Token、
完整请求、完整响应或供应商消息原文。

## 10. 契约测试

- `MockTransport` 捕获全部请求，断言 `api_name` 集合精确等于 `{"stock_basic"}`。
- 断言总调用覆盖 12 个唯一 segment，且参数/字段没有额外内容。
- 覆盖四状态、三交易所、后缀、币种和日期映射。
- 覆盖合法空 segment、聚合空、5,999 行、6,000 行、重复和冲突。
- 覆盖限流/5xx 重试、非重试错误、截止时间和 Token 脱敏。
- 验证 `allow_empty` 默认行为未改变现有交易日历测试。

## 11. 官方参考

- 股票基础信息：<https://tushare.pro/document/2?doc_id=25>
- Tushare HTTP 调用：<https://tushare.pro/document/1?doc_id=40>

这些链接用于字段与协议研究，不授权本功能调用其他数据端点。
