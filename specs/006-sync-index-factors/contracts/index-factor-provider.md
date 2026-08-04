# 内部契约：指数技术因子 Provider Port

> 供应商无关的提取契约。领域与业务代码只依赖本契约，不依赖任何第三方 SDK、
> 端点、传输模型或供应商专有字段（宪章 II）。实现参见
> `tushare-index-factor.md`；消费者为 `index-factor-service.md`。

## 1. 目的

定义“按交易日获取全部指数技术因子”这一业务能力的抽象，使 Tushare 实现、
测试替身与未来替代供应商可互换（spec ED-006/ED-007）。供应商字段
（`provider_security_id`）仅用于身份解析，不得成为业务键。

## 2. 请求

```python
@dataclass(frozen=True)
class IndexFactorRequest:
    target_trade_date: date          # 目标交易日（业务身份组成部分）
```

## 3. 规范记录

```python
@dataclass(frozen=True)
class ProviderIndexFactorRecord:
    provider_security_id: str        # 供应商指数标识（ts_code，仅身份解析用）
    trade_date: date                 # 交易日
    # 基础行情（Decimal，非空）
    open: Decimal; high: Decimal; low: Decimal; close: Decimal
    pre_close: Decimal; change: Decimal; pct_chg: Decimal
    vol: Decimal; amount: Decimal
    # 技术因子（Optional[Decimal]，来源缺失以 None 保存；不进入本契约的
    # 供应商字段一律不得出现在 DTO 上）
    ma_5: Decimal | None; ma_10: Decimal | None; ma_20: Decimal | None
    ma_30: Decimal | None; ma_60: Decimal | None; ma_90: Decimal | None
    ma_250: Decimal | None; ema_5: Decimal | None; ema_10: Decimal | None
    ema_20: Decimal | None; ema_30: Decimal | None; ema_60: Decimal | None
    ema_90: Decimal | None; ema_250: Decimal | None
    expma_12: Decimal | None; expma_50: Decimal | None; bbi: Decimal | None
    macd: Decimal | None; macd_dea: Decimal | None; macd_dif: Decimal | None
    kdj: Decimal | None; kdj_k: Decimal | None; kdj_d: Decimal | None
    rsi_6: Decimal | None; rsi_12: Decimal | None; rsi_24: Decimal | None
    cci: Decimal | None; wr: Decimal | None; wr1: Decimal | None
    bias1: Decimal | None; bias2: Decimal | None; bias3: Decimal | None
    psy: Decimal | None; psyma: Decimal | None
    roc: Decimal | None; maroc: Decimal | None; mfi: Decimal | None
    mtm: Decimal | None; mtmma: Decimal | None
    boll_lower: Decimal | None; boll_mid: Decimal | None; boll_upper: Decimal | None
    ktn_down: Decimal | None; ktn_mid: Decimal | None; ktn_upper: Decimal | None
    taq_up: Decimal | None; taq_mid: Decimal | None; taq_down: Decimal | None
    xsii_td1: Decimal | None; xsii_td2: Decimal | None
    xsii_td3: Decimal | None; xsii_td4: Decimal | None
    dmi_pdi: Decimal | None; dmi_mdi: Decimal | None
    dmi_adx: Decimal | None; dmi_adxr: Decimal | None
    obv: Decimal | None; vr: Decimal | None; emv: Decimal | None
    maemv: Decimal | None; cr: Decimal | None
    brar_ar: Decimal | None; brar_br: Decimal | None
    dpo: Decimal | None; madpo: Decimal | None
    dfma_dif: Decimal | None; dfma_difma: Decimal | None
    asi: Decimal | None; asit: Decimal | None; atr: Decimal | None
    mass: Decimal | None; ma_mass: Decimal | None
    trix: Decimal | None; trma: Decimal | None
    updays: int | None; downdays: int | None
    topdays: int | None; lowdays: int | None
```

编号规则：

1. 所有因子字段可空；空表示“来源未返回该值”，不等于业务无效。
2. 基础行情仅 `close` 必填（行情锚点）；`open/high/low/pre_close/change/
   pct_chg/vol/amount` 允许缺失，缺失以 None 正常保存。实测（2026-08-02，
   20260731 全量 3146 行）：439 行仅 `pre_close` 为空（新基日指数）、
   H 系列等指数 `open` 恒为空——均为有效行情形态。仅当 `close` 缺失时
   判定该指数当日无行情，按单条记录隔离（`ProviderInvalidCandidate`，
   类别 `INVALID_FIELD`，进入 batch.isolated），不阻断同交易日其他有效
   数据（spec FR-014/ED-004）；行内其余结构性错误（字段集合不精确、
   交易日错配）仍整批失败。
3. DTO 字段全集即白名单：供应商返回的文档外字段不得进入 DTO
   （ED-005），出现即整批失败。

## 4. 覆盖证据

```python
@dataclass(frozen=True)
class RetrievalEvidence:
    request_count: int                 # 真实 HTTP 请求次数（含重试与分页）
    completed_request_count: int       # 成功完成数
    retry_count: int                   # 重试次数（≤ 3）
    page_count: int                    # 分页批次数
    page_limit: int                    # 单次上限（8,000）
    last_page_count: int               # 最后一批行数
    pagination_enabled: bool           # 是否启用续取（本接口预期 False）
    continuation_exhausted: bool       # 续取是否穷尽
    repeated_page_detected: bool       # 是否检出重复批次
```

```python
@dataclass(frozen=True)
class ProviderIndexFactorBatch:
    records: tuple[ProviderIndexFactorRecord, ...]
    evidence: RetrievalEvidence
```

## 5. Port 与错误

```python
@runtime_checkable
class IndexFactorProvider(Protocol):
    provider_code: str
    def fetch_index_factors(
        self, request: IndexFactorRequest, *, deadline: float
    ) -> ProviderIndexFactorBatch: ...
```

- `deadline`（单调时钟绝对时刻）由调用方传入；超过即抛
  `ProviderDeadlineExceededError`，实现必须保证不越过 deadline。
- 错误统一使用 `src/lucking/ports/market_data_common.py` 的
  `ProviderError` 家族（`category` 属性）：`PROVIDER_RATE_LIMITED`、
  `PROVIDER_TIMEOUT`、`PROVIDER_NETWORK`、`PROVIDER_RESPONSE_CAPPED`、
  `PROVIDER_BAD_REQUEST`、`PROVIDER_AUTHENTICATION`、`PROVIDER_QUOTA_EXCEEDED`、
  `PROVIDER_BUSINESS_ERROR`、`ProviderDeadlineExceededError`。
- 实现内部必须遵守 30 次/分钟限流（任意 60 秒窗口 ≤ 30 次真实请求，
  最小间隔 2 秒）；节流不产生业务错误，仅延后请求。

## 6. 契约测试要点

- 使用固定 `retrieval` 替身（record 集 + evidence）验证消费者行为，
  不依赖真实供应商。
- 验证实现：请求参数只含 `trade_date`；白名单严格相等；触顶判定
  （8,000 行 → `PROVIDER_RESPONSE_CAPPED`）；节流间隔 ≥ 2 秒；
  重试退避与 deadline 约束；错误类别映射完整（§5 全集）。
- 验证替换性：替换为第二实现（测试替身或假供应商）时，
  Flow/Service/Repository 行为不变（ED-006）。
