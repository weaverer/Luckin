# 供应商契约：Tushare `idx_factor_pro` 指数技术因子

> 契约范围：Tushare Adapter 与供应商之间的边界。本契约定义唯一允许调用的接口、
> 请求参数、字段映射、错误与限流语义、完整性门禁。供应商细节不得泄漏进
> Flow / Service / Repository / ORM（宪章 II）。
> 关联：`index-factor-provider.md`（Port）、`index-factor-service.md`、
> `prefect-flow.md`、`data-model.md`、`research.md`。

## 1. 目的

本功能唯一允许调用的供应商接口是 Tushare `idx_factor_pro`（指数技术因子，专业版，
文档 https://tushare.pro/document/2?doc_id=358）。禁止调用该接口之外的任何
Tushare 端点（包括但不限于指数列表、指数行情、财务与预测类端点）。

## 2. 端点与请求

| 项 | 值 |
|----|-----|
| Adapter | `TushareIndexFactorProvider`（`src/lucking/integrations/tushare/index_factor_provider.py`） |
| API | `idx_factor_pro` |
| 提取模式 | 按交易日提取全市场全部指数，**不按指数循环**（spec FR-004） |
| 业务参数 | `target_trade_date`（日期） |
| 请求字段 | `trade_date=YYYYMMDD`；不传 `ts_code/start_date/end_date` |
| 输出字段 | 按 §3 白名单显式声明 |

## 3. 字段映射

规范字段 = 来源字段去掉 `_bfq` 后缀（research 决策 7）。基础行情来源共 10 字段
（含 `ts_code`/`trade_date`），其中 `ts_code` 经身份解析注册、`trade_date`
即目标交易日，二者不进入数据列；数据列共 87 个（基础行情 9 + 技术因子 78，
与 plan 摘要口径一致）：

| 来源字段 | 规范字段 | 类型 | 备注 |
|----------|----------|------|------|
| open | open | Decimal(12,4) | 开盘价 |
| high | high | Decimal(12,4) | 最高价 |
| low | low | Decimal(12,4) | 最低价 |
| close | close | Decimal(12,4) | 收盘价 |
| pre_close | pre_close | Decimal(12,4) | 昨收价 |
| change | change | Decimal(12,4) | 涨跌额 |
| pct_change | pct_chg | Decimal(12,4) | 涨跌幅（%） |
| vol | vol | Decimal(24,2) | 成交量（手） |
| amount | amount | Decimal(24,2) | 成交额（千元） |

技术因子 78 字段（全部 → `Nullable(Decimal(12,4))`，4 个天数 → `Nullable(UInt16)`）：

| 来源字段（→ 规范字段，去掉 `_bfq`） | 类别 | 说明 |
|-------------------------------------|------|------|
| ma_bfq_5/10/20/30/60/90/250 | 趋势 | 简单移动平均 |
| ema_bfq_5/10/20/30/60/90/250 | 趋势 | 指数移动平均 |
| expma_12_bfq / expma_50_bfq | 趋势 | 指数平均数 |
| bbi_bfq | 趋势 | BBI 多空指标 |
| macd_bfq / macd_dea_bfq / macd_dif_bfq | 趋势 | MACD 值/DEA/DIF |
| kdj_bfq / kdj_k_bfq / kdj_d_bfq | 摆动 | KDJ 指标/K/D |
| rsi_bfq_6/12/24 | 摆动 | RSI |
| cci_bfq | 摆动 | CCI 顺势指标 |
| wr_bfq / wr1_bfq | 摆动 | 威廉指标 |
| bias1_bfq / bias2_bfq / bias3_bfq | 摆动 | BIAS 乖离率 |
| psy_bfq / psyma_bfq | 摆动 | 心理线/均值 |
| roc_bfq / maroc_bfq | 摆动 | 变动率/均值 |
| mfi_bfq | 摆动 | MFI 资金流量 |
| mtm_bfq / mtmma_bfq | 摆动 | 动量指标/均值 |
| boll_lower_bfq / boll_mid_bfq / boll_upper_bfq | 通道 | 布林带 |
| ktn_down_bfq / ktn_mid_bfq / ktn_upper_bfq | 通道 | 肯特纳通道 |
| taq_up_bfq / taq_mid_bfq / taq_down_bfq | 通道 | 唐安奇通道 |
| xsii_td1_bfq ~ xsii_td4_bfq | 通道 | 薛斯通道 II |
| dmi_pdi_bfq / dmi_mdi_bfq / dmi_adx_bfq / dmi_adxr_bfq | 动量 | 动向指标 |
| obv_bfq | 动量 | 能量潮 |
| vr_bfq | 动量 | VR 容量比率 |
| emv_bfq / maemv_bfq | 动量 | 简易波动/均值 |
| cr_bfq | 动量 | CR 价格动量 |
| brar_ar_bfq / brar_br_bfq | 动量 | BRAR 情绪 |
| dpo_bfq / madpo_bfq | 动量 | 区间震荡线/均值 |
| dfma_dif_bfq / dfma_difma_bfq | 动量 | 平行线差/均值 |
| asi_bfq / asit_bfq | 动量 | 振动升降指标/均值 |
| atr_bfq | 动量 | 真实波幅均值 |
| mass_bfq / ma_mass_bfq | 动量 | 梅斯线/均值 |
| trix_bfq / trma_bfq | 动量 | 三重指数平滑/均值 |
| updays | 其他 | 连涨天数（→ updays） |
| downdays | 其他 | 连跌天数（→ downdays） |
| topdays | 其他 | 区间最高价天数（→ topdays） |
| lowdays | 其他 | 区间最低价天数（→ lowdays） |

**不进入 DTO 的字段**：`ts_code`（只用于身份解析，见
`index-factor-provider.md` §3）、`trade_date`（即目标交易日）、文档之外出现的
任何新字段（ED-005：不得进入业务表，且白名单严格校验失败时整批失败）。

**基础行情缺失形态**（实测 2026-08-02，20260731 全量 3146 行）：

- **439 行（约 14%）仅 `pre_close`（昨收）为空**（样本 801044.SI 等申万
  三级行业指数）——属有效行情，正常保存（pre_close 以 NULL 落库）；
- **H 系列中证指数（如 H30223.CSI）`open` 恒为空**——同样有效保存；
- 仅当 **`close`（收盘价）缺失**时才判定该指数当日无行情：单条隔离
  （`INVALID_FIELD` 类别 + 脱敏 issue，计数 invalid_count），不阻断整批；
  全部行均无行情时按 ED-004 判为当日失败。

另注意：实测发现接口返回的指数族远超文档所述“大盘/申万/中信”，
包含中证（CSI/CNI）、国证（CI）、中华（NH）、北证（BJ）等（8 种后缀）；
H30223.CSI 等指数在部分日期（如 2026-07-31）无数据行，属正常缺失。

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
  （最小间隔 2 秒），基于 `monotonic` 计算、`sleep` 可注入（便于测试与
  时间旅行），research 决策 4。
- 节流只负责“不超过限流”，不替代错误重试；被限流拒绝仍按 §4 映射重试。
- 回补与增量两条链路共用同一节流器（同一进程内全局生效）。

## 6. 完整性门禁

- 单次请求最大返回 **8,000 行**（spec FR-006；独立配置
  `index_factor_page_limit=8000`，不复用 005 的 6,000）。
- 按 `trade_date` 单请求返回该日全部指数；若返回行数 == 8,000（触顶）
  且未经验证的续取手段，判定 `ProviderResponseCappedError`（不完整）并失败，
  不得猜测 `ts_code` 过滤参数绕过门禁（ED-008；上线门禁实测验证，见
  research.md 部署前待验证项 1）。
- 重复批次（同页摘要 SHA-256 重复）、位置不前进、超过最大批次数均视为不完整。
- 空响应（0 行）：属正常业务结果当且仅当来源明确支持按交易日查询且当日
  无数据；与“提取中断/被截断”区分，由 Service 按 spec FR-014/ED-004 判定
  （个别指数无数据正常，全市场空响应由完整性校验把关）。

## 7. 契约测试要点

- 对 `TushareIndexFactorProvider` 使用**真实部署账户**或供应商沙箱验证
  一次 `trade_date` 全量请求：行数 < 8,000、字段全集与 §3 一致、
  无文档外字段、`_bfq` 后缀规律成立（上线门禁，research 待验证项 2）。
- 用可注入 `client/sleep/monotonic` 的测试替身验证：字段白名单严格相等
  （`set(row) != set(fields)` 整批失败）、触顶判定、节流间隔 ≥ 2 秒、
  重试退避序列与 deadline 约束、错误分类映射。
- 提供至少一个可替代实现或测试替身，证明更换供应商不改业务代码
  （宪章 II；ED-006/ED-007）。
- 契约测试不得依赖供应商 SDK；供应商错误码只出现在 Adapter 内部映射表。
