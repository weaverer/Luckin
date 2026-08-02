# 供应商契约：Tushare 行情数据 Adapter

## 1. 目的

本契约描述 Tushare 四个接口到项目规范模型的映射，由四个 Adapter
（`DailyQuoteProvider`、`AdjFactorProvider`、`DailyBasicProvider`、
`WeeklyMonthlyKlineProvider` 的 Tushare 实现）独占。
Service、Repository、Flow 和 ORM 不得依赖本契约的任何细节。

## 2. 端点与请求

| Adapter | Tushare 接口 | 业务参数 | 请求字段 |
|---------|--------------|----------|----------|
| DailyQuoteProvider | `daily` | `trade_date=YYYYMMDD` | 全量必需字段 |
| AdjFactorProvider | `adj_factor` | `trade_date=YYYYMMDD` | `ts_code,trade_date,adj_factor` |
| DailyBasicProvider | `daily_basic` | `trade_date=YYYYMMDD` | 除 `close` 外的规范必需字段 |
| WeeklyMonthlyKlineProvider | `stk_week_month_adj` | `freq=week/mon`、周期最后交易日 | 全量必需字段 |

规则：

- `trade_date` 参数格式为 `YYYYMMDD`；周/月线以 `freq` 与周期最后交易日提取，
  不得按 `ts_code` 逐只循环。
- 积分门槛：`adj_factor`、`daily_basic`、`stk_week_month_adj` 最低 2,000 积分，
  `daily` 基础积分可用；权限不足映射为 `AUTHENTICATION` 或 `QUOTA_EXCEEDED`。

## 3. 字段映射

### 3.1 `daily` → ProviderDailyQuote

| Tushare | 规范 DTO | 说明 |
|---------|----------|------|
| `ts_code` | `provider_security_id` + venue + 证券代码 | `.SH/.SZ/.BJ` → `XSHG/XSHE/XBSE`；未知后缀整批失败 |
| `trade_date` | `trade_date` | 必须等于请求交易日，否则 `TRADE_DATE_MISMATCH` |
| `open/high/low/close/pre_close` | 同名价格 | `DECIMAL(12,4)` |
| `change` | `change` | 涨跌额 |
| `pct_chg` | `pct_chg` | 基于除权昨收计算的涨跌幅 |
| `vol` | `vol` | 成交量（手） |
| `amount` | `amount` | 成交额（千元） |
| `ah_vol`/`ah_amount` | 不进入 DTO | 2026-07-06 起新增的盘后字段，首期不纳入；纳入需计划评审 |

### 3.2 `adj_factor` → ProviderAdjFactor

| Tushare | 规范 DTO | 说明 |
|---------|----------|------|
| `ts_code` | `provider_security_id` + venue + 证券代码 | 后缀映射同上 |
| `trade_date` | `trade_date` | 必须等于请求交易日 |
| `adj_factor` | `adj_factor` | 必须大于 0 |

### 3.3 `daily_basic` → ProviderDailyBasic

| Tushare | 规范 DTO | 说明 |
|---------|----------|------|
| `ts_code` | `provider_security_id` + venue + 证券代码 | 后缀映射同上 |
| `trade_date` | `trade_date` | 必须等于请求交易日 |
| `close` | 不进入 DTO | 与日线行情语义重复，单表事实原则 |
| `pe/pe_ttm/pb/ps/ps_ttm/dv_ratio/dv_ttm` | 同名可空字段 | 亏损公司为空 → `None` |
| `total_share/float_share/free_share` | 同名可空字段 | 股本（万股） |
| `total_mv/circ_mv` | 同名可空字段 | 市值（万元） |
| `turnover_rate/turnover_rate_f/volume_ratio` | 同名可空字段 | 换手与量比 |
| `limit_status` | `limit_status` | 0平盘、1涨停、2跌停、3炸板、4跌停打开、5跳水、6一字涨停、7一字跌停 |

### 3.4 `stk_week_month_adj` → 两个独立规范模型

同一接口按 `freq` 分派为两个独立规范模型：`freq=week` 的候选映射为
`ProviderWeeklyMonthlyKline(freq=WEEK)` 并写入周K线模型；
`freq=mon` 的候选映射为 `ProviderWeeklyMonthlyKline(freq=MONTH)` 并写入月K线模型。
两个模型字段结构相同但独立演进（用户明确要求），不得共用表。

| Tushare | 规范 DTO | 说明 |
|---------|----------|------|
| `ts_code` | `provider_security_id` + venue + 证券代码 | 后缀映射同上 |
| `freq` | `freq` | `week` → `WEEK`、`mon` → `MONTH`；未知值整批失败；决定写入哪个模型 |
| `trade_date` | `trade_date` | 周期最后交易日；不晚于请求交易日 |
| `open/high/low/close` | 未复权同名 | 未复权周期价（实测该接口无 qfq/hfq 复权价） |
| `vol/amount/change/pct_chg` | 同名 | 周期量额与涨跌 |
| `end_date` | `end_date` | 计算截至日期；与 `trade_date` 一致时为空 |

## 4. 错误映射与重试

- 复用通用 `TushareClient` 错误分类；补充积分门槛映射：
  权限/额度类错误映射为 `AUTHENTICATION` 或 `QUOTA_EXCEEDED`，不自动重试。
- 瞬态故障（网络/超时、HTTP 429、明确短期限流、5xx）在初次调用后
  重试最多 3 次，退避 30/120/300 秒并受整体截止时间约束；超过则
  `PROVIDER_RATE_LIMITED` / `PROVIDER_UNAVAILABLE` / `PROVIDER_DEADLINE`。
- 空响应映射为 `EMPTY_AGGREGATE`；单次返回达到上限且无法证明完整
  映射为 `RESPONSE_CAPPED`。

## 5. 完整性门禁

- 单日全市场行数低于 6,000 时单次请求即可；返回行数等于 `page_limit`
  必须视为潜在触顶。
- 仅当部署账户或供应商沙箱验证续取参数有效、位置前进、终止条件可靠后，
  才可启用有界循环；否则触顶即失败。
- 重复批次、位置不前进、超过最大批次数或提取中断均映射为对应问题类别并失败。

## 6. 契约测试要点

- 四个接口的 golden cases：字段转换、单位、后缀映射、空值、停牌、周期归属。
- 权限码、限流、触顶与瞬态重试边界；确定性错误零重试。
- 供应商新增字段（如 `ah_vol/ah_amount`）不得泄漏到规范 DTO。
