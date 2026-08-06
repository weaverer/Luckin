# 内部契约：股东数据 Provider Port

> 供应商无关的提取契约。领域与业务代码只依赖本契约，不依赖任何第三方 SDK、
> 端点、传输模型或供应商专有字段（宪章 II）。实现参见
> `tushare-shareholder-data.md`；消费者为 `shareholder-data-service.md`。

## 1. 目的

定义"按公告日获取全市场前十大股东、前十大流通股东、股东人数"这一业务
能力的抽象，使 Tushare 实现、测试替身与未来替代供应商可互换
（spec ED-007）。供应商字段（`provider_security_id`）仅用于身份解析，
不得成为业务键。
三个提取方法分别被三个独立 Flow/Service 链路消费（`prefect-flow.md`
§1/§3）；Provider 实例与节流器（§5）为共享资源，某一方法的失败只
影响对应链路，不影响其他两个方法（故障隔离契约）。

## 2. 请求

```python
@dataclass(frozen=True)
class ShareholderDataRequest:
    date: date              # 公告日期（单日；业务数据按公告日推进）
    holder_kind: str        # "TOP10" | "TOP10_FLOAT" | "HOLDER_COUNT"（对应三个接口）
```

- 每个请求对应一个接口在**一个公告日**的全市场数据；分页在实现内部完成
  （`has_more/offset`，`tushare-shareholder-data.md` §6），调用方不感知分页。

## 3. 规范记录

```python
@dataclass(frozen=True)
class ProviderShareholderRecord:
    provider_security_id: str        # 供应商股票标识（ts_code，仅身份解析用）
    ann_date: date                   # 公告日期（修订锚点，见 §5 编号行为 5）
    end_date: date                   # 披露期（报告期/截止日期，业务身份组成部分）
    holder_name: str                 # 股东名称（业务身份组成部分）
    hold_amount: Decimal | None      # 持有数量（股）
    hold_ratio: Decimal | None       # 占总股本比例（%）
    hold_float_ratio: Decimal | None # 占流通股本比例（%）
    hold_change: Decimal | None      # 持股变动（股，可为负）
    holder_type: str | None          # 股东类型

@dataclass(frozen=True)
class ProviderShareholderCountRecord:
    provider_security_id: str        # 供应商股票标识（ts_code，仅身份解析用）
    ann_date: date                   # 公告日期（修订锚点）
    end_date: date                   # 截止日期（业务身份组成部分）
    holder_num: int | None           # 股东户数

@dataclass(frozen=True)
class ProviderShareholderBatch:
    records: tuple[ProviderShareholderRecord, ...]
    isolated: tuple[ProviderInvalidCandidate, ...]
    evidence: RetrievalEvidence

@dataclass(frozen=True)
class ProviderShareholderCountBatch:
    records: tuple[ProviderShareholderCountRecord, ...]
    isolated: tuple[ProviderInvalidCandidate, ...]
    evidence: RetrievalEvidence
```

编号规则：

1. 所有数据字段可空；空表示"来源未返回该值"，不等于业务无效。
2. **结构性错误整批失败**：字段集合与白名单不严格相等、`end_date`/
   `ann_date` 非法、`ann_date` 与请求日期不一致（交易日错配，spec FR-009）、
   `holder_name` 缺失或空（持仓类）、`holder_num` 非法（人数类）。
3. 行内其余可隔离错误进入 `batch.isolated`
   （`ProviderInvalidCandidate`，类别 `INVALID_FIELD`），不阻断同批
   其他有效数据（spec ED-005）；若隔离后无任何有效数据，该次同步仍失败。
4. 分页完整性由实现保证并在 `RetrievalEvidence` 中披露
   （`continuation_exhausted=True` 表示 `has_more=False` 完整收尾）；
   位置不前进、重复页、超过最大页数 → 抛 `ProviderResponseCappedError`
   （不完整）。

## 4. 覆盖证据（复用 007 的 `RetrievalEvidence`）

```python
@dataclass(frozen=True)
class RetrievalEvidence:
    request_count: int                 # 真实 HTTP 请求次数（含重试与分页）
    completed_request_count: int       # 成功完成数
    retry_count: int                   # 重试次数（≤ 3）
    page_count: int                    # 分页批次数
    page_limit: int                    # 单次上限（6,000）
    last_page_count: int               # 最后一批行数
    pagination_enabled: bool           # 是否启用续取（本接口 True）
    continuation_exhausted: bool       # 续取是否穷尽（has_more=False）
    repeated_page_detected: bool       # 是否检出重复批次
```

## 5. Port 与错误

```python
@runtime_checkable
class ShareholderDataProvider(Protocol):
    provider_code: str
    def fetch_top10_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch: ...
    def fetch_top10_float_holders(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderBatch: ...
    def fetch_holder_count(
        self, request: ShareholderDataRequest, *, deadline: float
    ) -> ProviderShareholderCountBatch: ...
```

- `deadline`（单调时钟绝对时刻）由调用方传入；超过即抛
  `ProviderDeadlineExceededError`，实现必须保证不越过 deadline。
- 错误统一使用 `src/lucking/ports/market_data_common.py` 的
  `ProviderError` 家族（`category` 属性）：`PROVIDER_RATE_LIMITED`、
  `PROVIDER_TIMEOUT`、`PROVIDER_NETWORK`、`PROVIDER_RESPONSE_CAPPED`、
  `PROVIDER_BAD_REQUEST`、`PROVIDER_AUTHENTICATION`、`PROVIDER_QUOTA_EXCEEDED`、
  `PROVIDER_BUSINESS_ERROR`、`ProviderDeadlineExceededError`。
- 实现内部必须遵守 400 次/分钟限流（任意 60 秒窗口 ≤ 400 次真实请求，
  最小间隔 150 毫秒，共享 `RateLimiter`，三个接口共用）；
  节流不产生业务错误，仅延后请求。

## 6. 契约测试要点

- 使用固定 retrieval 替身（record 集 + evidence）验证消费者行为，
  不依赖真实供应商。
- 验证实现：请求参数只含公告日（`top10_*` 传 `ann_date`、
  `stk_holdernumber` 传 `start_date=end_date`，且不传 ts_code）；
  白名单严格相等；`has_more=True` 续取与 `continuation_exhausted`
  收尾、位置不前进/重复页/超页数判定；节流间隔 ≥ 150 毫秒；重试退避
  与 deadline 约束；错误类别映射完整（§5 全集）。
- 验证替换性：替换为第二实现（测试替身或假供应商）时，
  Flow/Service/Repository 行为不变（ED-007）。
- 验证三个提取方法相互独立：单一方法抛错（替身注入）不影响其他两个
  方法的调用与行为（故障隔离，prefect-flow.md §4）。
