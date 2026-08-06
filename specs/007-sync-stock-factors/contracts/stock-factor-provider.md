# 内部契约：股票技术面因子 Provider Port

> 供应商无关的提取契约。领域与业务代码只依赖本契约，不依赖任何第三方 SDK、
> 端点、传输模型或供应商专有字段（宪章 II）。实现参见
> `tushare-stock-factor.md`；消费者为 `stock-factor-service.md`。

## 1. 目的

定义“按交易日获取全部 A 股股票技术面因子”这一业务能力的抽象，使 Tushare
实现、测试替身与未来替代供应商可互换（spec ED-006/ED-007）。供应商字段
（`provider_security_id`）仅用于身份解析，不得成为业务键。

## 2. 请求

```python
@dataclass(frozen=True)
class StockFactorRequest:
    target_trade_date: date          # 目标交易日（业务身份组成部分）
```

## 3. 规范记录

```python
@dataclass(frozen=True)
class ProviderStockFactorRecord:
    provider_security_id: str        # 供应商股票标识（ts_code，仅身份解析用）
    trade_date: date                 # 交易日
    # 行情（Decimal，可空；close 为行情锚点，spec FR-014 语义）
    # 字段名与全部复权变体（_bfq/_qfq/_hfq）见 tushare-stock-factor.md §3，
    # 白名单常量 STOCK_FACTOR_FIELDS 为唯一事实来源（部署账户实测校准）
    open: Decimal | None; open_qfq: Decimal | None; open_hfq: Decimal | None
    high: Decimal | None; high_qfq: Decimal | None; high_hfq: Decimal | None
    low: Decimal | None; low_qfq: Decimal | None; low_hfq: Decimal | None
    close: Decimal | None; close_qfq: Decimal | None; close_hfq: Decimal | None
    pre_close: Decimal | None; change: Decimal | None; pct_chg: Decimal | None
    vol: Decimal | None; amount: Decimal | None
    turnover_rate: Decimal | None; turnover_rate_f: Decimal | None
    volume_ratio: Decimal | None; adj_factor: Decimal | None   # 可修订字段
    # 估值（Decimal，可空）
    pe: Decimal | None; pe_ttm: Decimal | None; pb: Decimal | None
    ps: Decimal | None; ps_ttm: Decimal | None
    dv_ratio: Decimal | None; dv_ttm: Decimal | None
    total_share: Decimal | None; float_share: Decimal | None
    free_share: Decimal | None; total_mv: Decimal | None; circ_mv: Decimal | None
    # 技术指标（Decimal | None，每个指标含 _bfq/_qfq/_hfq 三变体，
    # 命名规则 <指标>_<变体>_<周期>，如 ma_bfq_5 / ma_qfq_5 / ma_hfq_5；
    # 完整字段清单以 STOCK_FACTOR_FIELDS 白名单为准，不逐列枚举于本契约）
    ...
    # 天数（int | None）
    updays: int | None; downdays: int | None
    lowdays: int | None; topdays: int | None
```

编号规则：

1. 所有数据字段可空；空表示“来源未返回该值”，不等于业务无效。
2. 仅 `close` 必填（行情锚点）；`close` 缺失时判定该股票当日无行情，
   按单条记录隔离（`ProviderInvalidCandidate`，类别 `INVALID_FIELD`，
   进入 batch.isolated），不阻断同交易日其他有效数据（spec FR-014/ED-004）；
   行内其余结构性错误（字段集合不精确、交易日错配）仍整批失败。
3. **字段分级**（spec FR-010/ED-009）：可修订字段 = 字段名含 `_qfq`/`_hfq`
   后缀者 + `adj_factor`；其余为稳定字段。该分级由
   `STOCK_FACTOR_FIELDS` 白名单元数据（`(field_name, revision_allowed)`）
   统一声明，Service 按此判定更新/冲突（`stock-factor-service.md` §4-3）。
4. DTO 字段全集即白名单：供应商返回的文档外字段不得进入 DTO
   （ED-005），出现即整批失败。

## 4. 覆盖证据

```python
@dataclass(frozen=True)
class RetrievalEvidence:
    request_count: int                 # 真实 HTTP 请求次数（含重试与分页）
    completed_request_count: int       # 成功完成数
    retry_count: int                   # 重试次数（≤ 3）
    page_count: int                    # 分页批次数
    page_limit: int                    # 单次上限（10,000）
    last_page_count: int               # 最后一批行数
    pagination_enabled: bool           # 是否启用续取（本接口预期 False）
    continuation_exhausted: bool       # 续取是否穷尽
    repeated_page_detected: bool       # 是否检出重复批次
```

```python
@dataclass(frozen=True)
class ProviderStockFactorBatch:
    records: tuple[ProviderStockFactorRecord, ...]
    isolated: tuple[ProviderInvalidCandidate, ...]
    evidence: RetrievalEvidence
```

## 5. Port 与错误

```python
@runtime_checkable
class StockFactorProvider(Protocol):
    provider_code: str
    def fetch_stock_factors(
        self, request: StockFactorRequest, *, deadline: float
    ) -> ProviderStockFactorBatch: ...
```

- `deadline`（单调时钟绝对时刻）由调用方传入；超过即抛
  `ProviderDeadlineExceededError`，实现必须保证不越过 deadline。
- 错误统一使用 `src/lucking/ports/market_data_common.py` 的
  `ProviderError` 家族（`category` 属性）：`PROVIDER_RATE_LIMITED`、
  `PROVIDER_TIMEOUT`、`PROVIDER_NETWORK`、`PROVIDER_RESPONSE_CAPPED`、
  `PROVIDER_BAD_REQUEST`、`PROVIDER_AUTHENTICATION`、`PROVIDER_QUOTA_EXCEEDED`、
  `PROVIDER_BUSINESS_ERROR`、`ProviderDeadlineExceededError`。
- 实现内部必须遵守 30 次/分钟限流（任意 60 秒窗口 ≤ 30 次真实请求，
  最小间隔 2 秒，共享 `RateLimiter`）；节流不产生业务错误，仅延后请求。

## 6. 契约测试要点

- 使用固定 `retrieval` 替身（record 集 + evidence）验证消费者行为，
  不依赖真实供应商。
- 验证实现：请求参数只含 `trade_date`；白名单严格相等（含字段分级元数据
  完整）；触顶判定（10,000 行 → `PROVIDER_RESPONSE_CAPPED`）；节流间隔
  ≥ 2 秒；重试退避与 deadline 约束；错误类别映射完整（§5 全集）。
- 验证替换性：替换为第二实现（测试替身或假供应商）时，
  Flow/Service/Repository 行为不变（ED-006）。
