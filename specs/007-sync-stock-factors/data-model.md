# 数据模型：股票技术面因子同步（007-sync-stock-factors）

> `/speckit-plan` Phase 1 输出。依据：spec.md（含 Clarifications 2026-08-04）、
> research.md、项目宪章 1.2.0。
> 本功能**不新建任何 MySQL 表、无结构性 DDL 变更**：股票身份复用 003，
> 同步审计复用 005；唯一新增存储为 ClickHouse `stock_factor` 宽表。

## 1. 概览与数据归属

| 数据 | 存储 | 性质 | 说明 |
|------|------|------|------|
| 股票身份与来源映射 | MySQL（复用 003） | 事务型主数据 | `stock_current` + `stock_provider_mapping`，只读消费，无结构变更 |
| 股票技术面因子 | ClickHouse（新建） | 分析型行情 | 单表 `stock_factor`，宽表，含全部复权变体 |
| 同步审计（run/attempt/issue） | MySQL（复用 005） | 事务型审计 | `market_data_sync_*`，新增 `data_kind=STOCK_FACTOR` |

技术因子属于“覆盖全历史、按日追加、宽列”的分析型数据，入 ClickHouse；
股票身份与审计均复用既有表（research 决策 1/3），本功能不拥有新的
MySQL 数据。

## 2. MySQL 表（全部复用，无 DDL 变更）

### 2.1 股票身份（003 复用）

`stock_current`/`stock_provider_mapping` 由 003 功能维护，本功能只读消费：

- 身份解析入口：`SqlAlchemyStockListRepository.provider_mappings(provider_code)`
  返回 `{provider_security_id: stock_id}`（如 `600000.SH → <uuid>`）；
- 覆盖范围：`stock_current` 的 `market_code='CN-S'`、
  `venue_code IN ('XSHG','XSHE','XBSE')`（含北交所），`listing_status`
  含 `ACTIVE/DELISTED/SUSPENDED/PENDING`；
- 解析失败（ts_code 未映射）→ `invalid_count` + 脱敏 issue
  （类别 `UNKNOWN_STOCK_IDENTITY`），跳过该条（spec ED-004）；
- 身份键 `stock_id String(36)` 即 ClickHouse `stock_factor.stock_id`
  的外键语义来源。

### 2.2 同步审计（005 复用）

`market_data_sync_run/attempt/issue` 三表原样复用，新增
`DataKind.STOCK_FACTOR = "STOCK_FACTOR"` 取值（纯代码枚举扩展，无列变更）：

- **run 表**：`run_key` 输入 `STOCK_FACTOR + SCHEDULED + schedule_slug +
  scheduled_for_utc + target_trade_date`（增量）或 `STOCK_FACTOR + BACKFILL +
  backfill_batch_id + target_trade_date`（回补）；`SUCCEEDED` 不可重开；
  `scope_fingerprint` 仅审计不参与 run_key。
- **attempt 表**：唯一键 `(run_id, attempt_no)`、`flow_run_id` 唯一、
  租约固定 2100 秒（必须大于提取 deadline）、全部提取计数
  （received/valid/added/updated/unchanged/duplicate/invalid/conflict）、
  `provider_retry_count ≤ 3`。
- **issue 表**：attempt_id 关联；问题类别沿用 005 全集
  （含 `UNKNOWN_STOCK_IDENTITY`，无需新增类别）；脱敏摘要
  （哈希 + 白名单），禁止 Token/连接串/原始行。

### 2.3 宪章 VI 结论

本功能不新建、不结构性修改任何项目拥有的 MySQL 业务表，因此宪章 VI
“逐表治理”要求在本功能**不适用**（无新建/无结构变更）；复用表的结构
治理义务仍归属其创建功能（003/005）。ClickHouse `stock_factor` 属于
宪章 II 明确划分的分析型数据存储，且属宪章允许的“外部引擎承载业务数据”
情形——引擎、排序键、分区与幂等语义记录于 §3/§5。

## 3. ClickHouse 业务表 `stock_factor`

### 3.1 引擎与物理布局

```sql
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, stock_id)
```

- 行身份 = `(trade_date, stock_id)`（spec FR-007）；同一身份重复写入由
  `ReplacingMergeTree` 按 `updated_at` 版本列收敛，保留最新版本
  （支持复权字段回溯更新，spec FR-010/ED-009）。
- 按月分区；无 TTL，长期保留，清理按分区显式执行（NFR-009）。
- 中文表注释与每列中文 COMMENT 随 DDL 落库
  （`python -m lucking.clickhouse migrate` 注册）。

### 3.2 身份列

| 列 | 类型 | 注释 |
|----|------|------|
| trade_date | Date | 交易日期 |
| stock_id | FixedString(36) | 规范股票标识（stock_current.stock_id） |
| stock_code | String | 来源股票代码（ts_code，含后缀） |
| updated_at | DateTime64(3) | 应用写入版本时间（UTC，同批相同、跨重试递增） |

### 3.3 字段命名与分级（research 决策 7）

- **规范字段名 = 来源字段名原样保留**（含 `_bfq/_qfq/_hfq` 后缀），
  如 `ma_bfq_5`、`ma_qfq_5`、`close_hfq`、`adj_factor`。
- **可修订字段**（值随后续除权除息重算，重复同步按来源最新值更新，
  计 `updated_count`，不视为冲突）：字段名含 `_qfq` 或 `_hfq` 后缀者 +
  `adj_factor`。
- **稳定字段**（其余全部；同键值变化即 `RECORD_CONFLICT` 整批失败）：
  不复权行情（含 `_bfq` 变体与原值）、估值、连涨连跌天数。
- 全部数据列 `Nullable`；缺失以 NULL 保存，与“必需字段缺失”的无效
  记录严格区分（ED-004）。

> 字段全集以部署账户实测为准校准（research 待验证项 2）；下表按来源文档
> 分组清单给出基线。文档注明各指标默认参数（如 MACD 12/26/9），
> 取值以来源为准，本功能不重算（spec 假设）。

### 3.4 行情列（来源“恒返回”组）

| 列（组） | 类型 | 注释 |
|----------|------|------|
| open / open_qfq / open_hfq | Nullable(Decimal(12,4)) | 开盘价（原值即不复权；实测 2026-08-04 确认无 _bfq 变体） |
| high / high_qfq / high_hfq | Nullable(Decimal(12,4)) | 最高价 |
| low / low_qfq / low_hfq | Nullable(Decimal(12,4)) | 最低价 |
| close / close_qfq / close_hfq | Nullable(Decimal(12,4)) | 收盘价（复权因子时点差异见 spec ED-009；close 为 NOT NULL 行情锚点） |
| pre_close | Nullable(Decimal(12,4)) | 昨收价 |
| change | Nullable(Decimal(12,4)) | 涨跌额 |
| pct_chg | Nullable(Decimal(12,4)) | 涨跌幅（%，除权后口径） |
| vol | Nullable(Decimal(24,2)) | 成交量（手） |
| amount | Nullable(Decimal(24,2)) | 成交额（千元） |
| turnover_rate | Nullable(Decimal(12,4)) | 换手率（%） |
| turnover_rate_f | Nullable(Decimal(12,4)) | 自由流通换手率（%） |
| volume_ratio | Nullable(Decimal(12,4)) | 量比 |
| adj_factor | Nullable(Decimal(12,4)) | 复权因子（可修订字段） |

### 3.5 估值列

默认 `Nullable(Decimal(12,4))`；**股本/市值类 5 列使用 `Nullable(Decimal(24,4))`**
（与 005 `daily_basic` 约定一致——大市值股票 `total_mv` 可达 1 万亿元
约 10^8 万元，`Decimal(12,4)` 会溢出，实测 2026-08-05 回补确认）：

| 列 | 类型 | 注释 |
|----|------|------|
| pe / pe_ttm | Nullable(Decimal(12,4)) | 市盈率（动态/滚动 TTM） |
| pb | Nullable(Decimal(12,4)) | 市净率 |
| ps / ps_ttm | Nullable(Decimal(12,4)) | 市销率（动态/滚动） |
| dv_ratio / dv_ttm | Nullable(Decimal(12,4)) | 股息率（静态/滚动） |
| total_share | Nullable(Decimal(24,4)) | 总股本（万股） |
| float_share | Nullable(Decimal(24,4)) | 流通股本（万股） |
| free_share | Nullable(Decimal(24,4)) | 自由流通股本（万股） |
| total_mv | Nullable(Decimal(24,4)) | 总市值（万元） |
| circ_mv | Nullable(Decimal(24,4)) | 流通市值（万元） |

### 3.6 技术指标列（默认 `Nullable(Decimal(12,4))`；来源返回哪个变体
保存哪个，`_bfq/_qfq/_hfq` 三变体并存）

**趋势/均线类**

| 列 | 注释 |
|----|------|
| ma_bfq_5/10/20/30/60/90/250、ma_qfq_*、ma_hfq_* | 简单移动平均（5~250 日） |
| ema_bfq_5/10/20/30/60/90/250、ema_qfq_*、ema_hfq_* | 指数移动平均（5~250 日） |
| expma_bfq_12/50、expma_qfq_*、expma_hfq_* | 指数平均数（12/50 日） |
| bbi_bfq / bbi_qfq / bbi_hfq | BBI 多空指标 |
| macd_bfq / macd_qfq / macd_hfq | MACD 值 |
| macd_dea_bfq / macd_dea_qfq / macd_dea_hfq | MACD 信号线（DEA） |
| macd_dif_bfq / macd_dif_qfq / macd_dif_hfq | MACD 差离值（DIF） |

**摆动/超买超卖类**

| 列 | 注释 |
|----|------|
| kdj_bfq / kdj_qfq / kdj_hfq | KDJ 随机指标 |
| kdj_k_bfq / kdj_k_qfq / kdj_k_hfq | KDJ K 线 |
| kdj_d_bfq / kdj_d_qfq / kdj_d_hfq | KDJ D 线 |
| rsi_bfq_6/12/24、rsi_qfq_*、rsi_hfq_* | 相对强弱指标 RSI（6/12/24 日） |
| cci_bfq / cci_qfq / cci_hfq | CCI 顺势指标 |
| wr_bfq / wr_qfq / wr_hfq、wr1_* | 威廉指标（WR/WR1） |
| bias1_bfq / bias1_qfq / bias1_hfq、bias2_*、bias3_* | BIAS 乖离率（1/2/3） |
| psy_bfq / psy_qfq / psy_hfq、psyma_* | 心理线 PSY / 其均值 |
| roc_bfq / roc_qfq / roc_hfq、maroc_* | 变动率 ROC / 其均值 |
| mfi_bfq / mfi_qfq / mfi_hfq | MFI 资金流量指标 |
| mtm_bfq / mtm_qfq / mtm_hfq、mtmma_* | 动量指标 MTM / 其均值 |

**通道类**

| 列 | 注释 |
|----|------|
| boll_lower_bfq / boll_lower_qfq / boll_lower_hfq、boll_mid_*、boll_upper_* | 布林带下/中/上轨 |
| ktn_down_bfq / ktn_down_qfq / ktn_down_hfq、ktn_mid_*、ktn_upper_* | 肯特纳通道下/中/上轨 |
| taq_up_bfq / taq_up_qfq / taq_up_hfq、taq_mid_*、taq_down_* | 唐安奇（海龟）通道上/中/下轨 |
| xsii_td1_bfq / xsii_td1_qfq / xsii_td1_hfq、xsii_td2_*、xsii_td3_*、xsii_td4_* | 薛斯通道 II（TD1~TD4） |

**动量/能量类**

| 列 | 注释 |
|----|------|
| dmi_pdi_bfq / dmi_pdi_qfq / dmi_pdi_hfq、dmi_mdi_* | 动向指标 +DI / -DI |
| dmi_adx_bfq / dmi_adx_qfq / dmi_adx_hfq、dmi_adxr_* | 动向指标 ADX / ADXR |
| obv_bfq / obv_qfq / obv_hfq | 能量潮 OBV |
| vr_bfq / vr_qfq / vr_hfq | VR 容量比率 |
| emv_bfq / emv_qfq / emv_hfq、maemv_* | 简易波动 EMV / 其均值 |
| cr_bfq / cr_qfq / cr_hfq | CR 价格动量 |
| brar_ar_bfq / brar_ar_qfq / brar_ar_hfq、brar_br_* | BRAR 情绪指标 AR / BR |
| dpo_bfq / dpo_qfq / dpo_hfq、madpo_* | 区间震荡线 DPO / 其均值 |
| dfma_dif_bfq / dfma_dif_qfq / dfma_dif_hfq、dfma_difma_* | 平行线差 / 其均值 |
| asi_bfq / asi_qfq / asi_hfq、asit_* | 振动升降指标 ASI / 其均值 |
| atr_bfq / atr_qfq / atr_hfq | 真实波幅均值 ATR |
| mass_bfq / mass_qfq / mass_hfq、ma_mass_* | 梅斯线 MASS / 其均值 |
| trix_bfq / trix_qfq / trix_hfq、trma_* | 三重指数平滑 TRIX / 其均值 |

### 3.7 其他列（`Nullable(UInt16)`）

| 列 | 注释 |
|----|------|
| updays | 连涨天数 |
| downdays | 连跌天数 |
| lowdays | 区间最低价天数 |
| topdays | 区间最高价天数 |

## 4. 幂等与发布语义

1. **身份解析**：批次校验前，按 `provider_security_id` 查 003
   `provider_mappings`（tushare）解析 `stock_id`；未映射 → `invalid_count`
   + 脱敏 issue（`UNKNOWN_STOCK_IDENTITY`），跳过该条，不阻断整批
   （spec ED-004）。
2. **校验**：交易日归属、`stock_id` 可解析、必需字段（trade_date、
   stock_code、`close` 行情锚点）有效；完全相同的重复行去重计
   `duplicate_count`（spec FR-010）。
3. **冲突 vs 修订**：同键既有行比较——仅可修订字段（`_qfq/_hfq` +
   `adj_factor`）差异 → 按来源最新值更新，计 `updated_count`
   （正常数据修订，spec FR-010/ED-009）；稳定字段差异 →
   `RECORD_CONFLICT` 整批失败，不得任意覆盖（FR-010）。
4. **发布**：有效行以单 block JSONEachRow 批量 INSERT `stock_factor`；
   INSERT 前 `SELECT ... FINAL` 读取既有同键行计算
   added/updated/unchanged 计数（仅审计用途）；
   `ReplacingMergeTree(updated_at)` 保证同键替换（spec FR-009/SC-003）。
5. **终态**：发布成功后在**同一 MySQL 事务**写入 attempt 计数与终态、
   run 终态；失败时记录失败终态，已写入 ClickHouse 的数据不受影响
   （spec FR-013）。
6. **回补幂等**：逐日独立 `resolve`（START/SKIP_SUCCEEDED/RETRY/
   IN_PROGRESS），键 = `backfill_batch_id + STOCK_FACTOR +
   target_trade_date`；已成功日期跳过，失败日期可安全重试
   （spec FR-018）。

## 5. 需求追溯

| 模型/行为 | 需求 |
|-----------|------|
| 身份复用（003 `provider_mappings`，`UNKNOWN_STOCK_IDENTITY` 隔离） | FR-007、FR-009、ED-004、ED-006 |
| `stock_factor` 表（身份 + 行情/估值/技术指标及全部复权变体） | FR-008、FR-010、ED-005、ED-009 |
| 可修订/稳定字段分级更新语义 | FR-010、ED-009 |
| 单 block 发布 + 同键替换（`ReplacingMergeTree(updated_at)`） | FR-009、FR-013、NFR-003、SC-003 |
| 审计三表复用（`data_kind=STOCK_FACTOR`） | FR-011、FR-012、NFR-005、SC-009 |
| 按月分区、无 TTL | NFR-009 |
| 无新 MySQL 表（宪章 VI 不适用） | 宪章 VI、宪章 II |
