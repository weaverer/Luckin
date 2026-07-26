# 外部契约：通用 Tushare Client 与 `trade_cal` Adapter

## 1. 分层边界

### 1.1 通用 TushareClient

```python
class TushareClient:
    def query(
        self,
        api_name: str,
        params: Mapping[str, JSONValue],
        fields: Sequence[str],
    ) -> TushareTable: ...
```

```python
@dataclass(frozen=True)
class TushareTable:
    request_id: str | None
    rows: tuple[Mapping[str, JSONValue], ...]
```

通用 Client 负责：

- Token 注入、HTTPS 请求、超时和连接复用。
- 通用响应信封 `request_id/code/msg/data.fields/data.items` 解析。
- 按字段名将二维 `items` 转为行映射，允许字段顺序变化。
- Tushare 业务错误、HTTP 错误、限流和网络错误分类。
- 集中脱敏 Token、请求体和敏感错误信息。

通用 Client 不得：

- 写死 `trade_cal`、`SSE` 或日历字段。
- 生成 `MarketCode`、`ProviderCalendarDay` 或数据库模型。
- 执行业务完整性校验或数据库写入。

通用 Client 的公共返回值不得包含 Token；`data.fields/data.items` 必须在 Client 内
转换为按字段名访问的只读行。

### 1.2 TushareTradingCalendarProvider

Adapter 实现 `TradingCalendarProvider`，负责：

- 将项目 `CN-S` 映射到 Tushare `SSE`。
- 使用通用 Client 调用 `trade_cal`。
- 将 Tushare 行转换为 `ProviderCalendarDay`。
- 把 Tushare Client 错误映射为供应商无关异常。

## 2. 端点

```text
POST https://api.tushare.pro
Content-Type: application/json
```

API URL 允许通过 `TUSHARE_API_URL` 覆盖以支持契约测试，生产默认值固定为 HTTPS 官方端点。

## 3. 请求

```json
{
  "api_name": "trade_cal",
  "token": "<TUSHARE_TOKEN>",
  "params": {
    "exchange": "SSE",
    "start_date": "20260701",
    "end_date": "20261231"
  },
  "fields": "exchange,cal_date,is_open,pretrade_date"
}
```

规则：

- Token 仅存在于请求内存，不得写入日志、异常或测试快照。
- `exchange` 首期必须显式为 `SSE`，不得依赖接口默认值。
- 日期格式为 `YYYYMMDD`，区间两端均包含。
- 不传 `is_open`，确保同时获得开市日和休市日。

## 4. 成功响应

通用 Client 按 Tushare 响应信封解析；Adapter 再做日历字段映射：

```json
{
  "request_id": "opaque-id",
  "code": 0,
  "msg": null,
  "data": {
    "fields": [
      "exchange",
      "cal_date",
      "is_open",
      "pretrade_date"
    ],
    "items": [
      ["SSE", "20260701", 1, "20260630"],
      ["SSE", "20260704", 0, "20260703"]
    ]
  }
}
```

通用 Client 接受条件：

- HTTP 状态为 2xx。
- `code = 0`。
- `data.fields` 与请求字段匹配，顺序可变。
- `data.items` 非空，且每行列数与 `fields` 一致。

`trade_cal` Adapter 额外接受条件：

- 所需字段精确包含 `exchange/cal_date/is_open/pretrade_date`。
- `exchange` 为 `SSE`。
- 字段可转换为标准类型，且单批结果中日期无重复。

完整自然日覆盖、日期范围和跨记录一致性由 `TradingCalendarService` 对所有 Provider
统一校验，不在 Tushare Adapter 中形成供应商专属业务规则。

## 5. 失败分类

| 类别 | 条件 | 是否重试 |
|------|------|----------|
| `NETWORK` | 连接失败、DNS、超时 | 是 |
| `RATE_LIMITED` | HTTP 429 或明确的短时频率限制 | 是 |
| `QUOTA_EXHAUSTED` | 账户额度、积分或当日配额耗尽 | 否 |
| `UPSTREAM_UNAVAILABLE` | HTTP 5xx | 是 |
| `AUTHENTICATION` | Token 无效或权限不足 | 否 |
| `BAD_REQUEST` | 参数或接口名错误 | 否 |
| `UPSTREAM_BUSINESS` | 其他非零 `code` | 否 |
| `INVALID_PAYLOAD` | JSON、通用信封或行列结构不合法 | 否 |
| `EMPTY_PAYLOAD` | 非空请求区间返回零行 | 否 |

仅 `NETWORK/RATE_LIMITED/UPSTREAM_UNAVAILABLE` 最多重试 3 次，等待
30、120、300 秒；`QUOTA_EXHAUSTED` 立即失败。日志只记录类别、HTTP 状态、
Tushare `code` 和经过截断/脱敏的 `msg`，不得记录请求体。分类必须依据明确的状态码
或经过契约测试的错误语义；无法确认是短时频率限制时不得默认重试。

## 6. 契约测试

### 6.1 通用 Client

- 至少用 `trade_cal` 和一个虚构的第二 `api_name` 验证请求构造没有接口硬编码。
- 字段顺序变化时按字段名正确映射。
- 通用信封错误、HTTP 429/5xx/超时、短时频率限制和额度耗尽分类正确。
- Token 和完整请求体不出现在日志或异常中。

### 6.2 `trade_cal` Adapter

必须覆盖：

- 开市与休市行均保留。
- 非 `SSE` 行被拒绝。
- 空数据、重复日期、缺少字段、非法日期和非法 `is_open` 被拒绝。
- 输出只包含 `ProviderCalendarDay`，不暴露 Tushare 原始行。
- Tushare 错误正确转换为供应商无关异常。
- 连续未来尾部缺失由 Adapter 原样返回已取得记录，不填充休市日；完整性状态由
  Service 判定。

## 7. 官方参考

- [交易日历接口](https://tushare.pro/document/2?doc_id=26)
- [HTTP API 调用方式](https://tushare.pro/document/1?doc_id=40)
