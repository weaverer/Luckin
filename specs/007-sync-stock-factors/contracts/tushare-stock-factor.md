# 供应商契约：Tushare `stk_factor_pro` 股票技术面因子

> 契约范围：Tushare Adapter 与供应商之间的边界。本契约定义唯一允许调用的接口、
> 请求参数、字段映射、错误与限流语义、完整性门禁。供应商细节不得泄漏进
> Flow / Service / Repository / ORM（宪章 II）。
> 关联：`stock-factor-provider.md`（Port）、`stock-factor-service.md`、
> `prefect-flow.md`、`data-model.md`、`research.md`。

## 1. 目的

本功能唯一允许调用的供应商接口是 Tushare `stk_factor_pro`
（股票技术面因子，专业版，文档 https://tushare.pro/document/2?doc_id=328）。
禁止调用该接口之外的任何 Tushare 端点（包括但不限于行情、基本面、财务与
预测类端点，spec FR-017）。

## 2. 端点与请求

| 项 | 值 |
|----|-----|
| Adapter | `TushareStockFactorProvider`（`src/lucking/integrations/tushare/stock_factor_provider.py`） |
| API | `stk_factor_pro` |
| 提取模式 | 按交易日提取全市场全部 A 股，**不按股票循环**（spec FR-004） |
| 业务参数 | `target_trade_date`（日期） |
| 请求字段 | `trade_date=YYYYMMDD`；不传 `ts_code/start_date/end_date` |
| 输出字段 | 按 §3 白名单显式声明（部署账户实测校准） |

## 3. 字段映射

规范字段名 = 来源字段名**原样保留**（含 `_bfq/_qfq/_hfq` 后缀，research
决策 7）。`ts_code` 经身份解析（003 `provider_mappings`）、`trade_date`
即目标交易日，二者不进入数据列。字段全集以部署账户实测校准（research
部署前待验证项 2），本节按来源文档分组清单给出基线：

**行情**：`open/close/high/low`（原值即不复权；实测 2026-08-04 确认仅含
`_qfq/_hfq` 两复权变体，无 `_bfq` 变体）、`pre_close`、`change`、
`pct_chg`（除权后涨跌幅）、`vol`（手）、`amount`（千元）、`turnover_rate`、
`turnover_rate_f`、`volume_ratio`、`adj_factor`（复权因子）。

**估值**：`pe`、`pe_ttm`、`pb`、`ps`、`ps_ttm`、`dv_ratio`、`dv_ttm`、
`total_share`、`float_share`、`free_share`、`total_mv`、`circ_mv`。

**技术指标**（来源返回哪个变体保存哪个，`_bfq/_qfq/_hfq` 三变体并存）：
`ma_*_5/10/20/30/60/90/250`、`ema_*_5/10/20/30/60/90/250`、
`expma_*_12/50`、`bbi_*`、`macd_*`、`macd_dea_*`、`macd_dif_*`、
`kdj_*`、`kdj_k_*`、`kdj_d_*`、`rsi_*_6/12/24`、`cci_*`、`wr_*`、`wr1_*`、
`bias1_*`、`bias2_*`、`bias3_*`、`psy_*`、`psyma_*`、`roc_*`、`maroc_*`、
`mfi_*`、`mtm_*`、`mtmma_*`、`boll_lower_*`、`boll_mid_*`、`boll_upper_*`、
`ktn_down_*`、`ktn_mid_*`、`ktn_upper_*`、`taq_up_*`、`taq_mid_*`、
`taq_down_*`、`xsii_td1_*` ~ `xsii_td4_*`、`dmi_pdi_*`、`dmi_mdi_*`、
`dmi_adx_*`、`dmi_adxr_*`、`obv_*`、`vr_*`、`emv_*`、`maemv_*`、`cr_*`、
`brar_ar_*`、`brar_br_*`、`dpo_*`、`madpo_*`、`dfma_dif_*`、`dfma_difma_*`、
`asi_*`、`asit_*`、`atr_*`、`mass_*`、`ma_mass_*`、`trix_*`、`trma_*`
（`*` = `_bfq/_qfq/_hfq`）。

**天数**：`updays`、`downdays`、`lowdays`、`topdays`。

**不进入 DTO 的字段**：`ts_code`（只用于身份解析，见
`stock-factor-provider.md` §3）、`trade_date`（即目标交易日）、文档或实测
之外出现的任何新字段（ED-005：不得进入业务表，白名单严格校验失败时
整批失败）。

**字段分级**（spec FR-010/ED-009）：可修订字段 = 字段名含 `_qfq`/`_hfq`
后缀者 + `adj_factor`（随后续除权除息重算，按来源最新值更新）；
稳定字段 = 其余全部（不复权行情、估值、天数；同键值变化即冲突）。

**数据质量注记**（来源文档）：`pct_chg` 为除权后涨跌幅；`pre_close` 与
`close_qfq` 可能因复权因子时点差异不一致——不视为数据错误，正常保存
（ED-009）。

## 4. 错误映射与重试

| 供应商错误 | 规范类别 | 可重试 | 行为 |
|------------|----------|--------|------|
| HTTP/业务限流（429、频率超限业务码） | `PROVIDER_RATE_LIMITED` | 是 | 退避 30/120/300 秒，≤ 3 次，受整体 deadline 约束 |
| 超时/连接失败 | `PROVIDER_TIMEOUT` / `PROVIDER_NETWORK` | 是 | 同上 |
| 积分/权限不足 | `QUOTA_EXCEEDED` / `AUTHENTICATION` | 否 | 确定性失败，0 次重试 |
| 参数被拒、交易日无数据 | `PROVIDER_BAD_REQUEST` / 按空响应规则 | 按规则 | 确定性失败或空响应处理（见 §6） |
| 其他业务错误 | `PROVIDER_BUSINESS_ERROR` | 否 | 0 次重试 |

- 重试只在 Adapter 初次调用后进行；Flow `retries=0`，重试层数不叠加。
- 每次真实 HTTP 请求前必须经过节流器（§5），重试请求同样受节流约束。

## 5. 限流与节流（30 次/分钟）

- 供应商限流档位：5000 积分每分钟 30 次（本功能按此档保守执行，spec FR-005）。
- Adapter 内进程级节流：任意 60 秒窗口内真实 HTTP 请求数 ≤ 30
  （最小间隔 2 秒），基于 `monotonic` 计算、`sleep` 可注入
  （共享 `RateLimiter`，research 决策 4）。
- 节流只负责“不超过限流”，不替代错误重试；被限流拒绝仍按 §4 映射重试。
- 回补与增量两条链路共用同一节流器（同一进程内全局生效）。

## 6. 完整性门禁

- 单次请求最大返回 **10,000 行**（spec FR-006；独立配置
  `stock_factor_page_limit=10000`，不复用 005 的 6,000 或 006 的 8,000）。
- 按 `trade_date` 单请求返回该日全部 A 股；若返回行数 == 10,000（触顶）
  且未经验证的续取手段，判定 `ProviderResponseCappedError`（不完整）并
  失败，不得猜测 `ts_code` 过滤参数绕过门禁（ED-008；上线门禁实测验证，
  research 部署前待验证项 1）。
- 重复批次（同页摘要 SHA-256 重复）、位置不前进、超过最大批次数均视为
  不完整。
- 空响应（0 行）：属正常业务结果当且仅当来源明确支持按交易日查询且当日
  无数据；与“提取中断/被截断”区分，由 Service 按 spec FR-014/ED-004 判定
  （个别股票无数据正常，全市场空响应由完整性校验把关）。

## 7. 契约测试要点

- 对 `TushareStockFactorProvider` 使用**真实部署账户**或供应商沙箱验证
  一次 `trade_date` 全量请求：行数 < 10,000、字段全集与 §3 一致、
  无文档外字段、复权变体规律成立（上线门禁，research 待验证项 2）。
- 用可注入 `client/sleep/monotonic` 的测试替身验证：字段白名单严格相等
  （`set(row) != set(fields)` 整批失败）、触顶判定（10,000 行）、
  节流间隔 ≥ 2 秒、重试退避序列与 deadline 约束、错误分类映射。
- 提供至少一个可替代实现或测试替身，证明更换供应商不改业务代码
  （宪章 II；ED-006/ED-007）。
- 契约测试不得依赖供应商 SDK；供应商错误码只出现在 Adapter 内部映射表。
