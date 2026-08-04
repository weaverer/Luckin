# 数据模型：指数技术因子同步（006-sync-index-factors）

> `/speckit-plan` Phase 1 输出。依据：spec.md、research.md、项目宪章 1.2.0。
> 物理约定沿用 005 已验证模式：MySQL 事务型主数据/审计走宪章 VI 治理，
> ClickHouse 承载分析型行情数据，具体 DDL 与迁移在实现阶段落实。

## 1. 概览与数据归属

| 数据 | 存储 | 性质 | 说明 |
|------|------|------|------|
| 指数主数据与来源映射 | MySQL（新建） | 事务型主数据 | `index_current` + `index_provider_mapping` |
| 指数技术因子 | ClickHouse（新建） | 分析型行情 | 单表 `index_factor`，宽表，不复权 |
| 同步审计（run/attempt/issue） | MySQL（复用 005） | 事务型审计 | `market_data_sync_*`，新增 `data_kind=INDEX_FACTOR` |

技术因子属于“覆盖全历史、按日追加、宽列”的分析型数据，入 ClickHouse；
指数身份与来源映射属于规范化主数据，入 MySQL；同步运行与质量问题审计复用
005 三表（research 决策 3），不新建审计表。

## 2. MySQL 身份表（宪章 VI 治理）

两张新表均采用宪章 VI 标准物理治理：`id BIGINT UNSIGNED AUTO_INCREMENT` 物理主键
（中文注释）、业务标识 UUID `CHAR(36) ascii_bin` 带 `UNIQUE`、数据库维护的
`created_at/updated_at`（`CURRENT_TIMESTAMP` / `ON UPDATE CURRENT_TIMESTAMP`）、
中文表注释与每列非空中文注释；ORM、Alembic 迁移与实际 DDL 三方一致。

### 2.1 `index_current` —— 指数主数据

| 列 | 类型 | 约束/默认 | 注释 |
|----|------|-----------|------|
| id | BIGINT UNSIGNED | PK AUTO_INCREMENT | 主键ID |
| index_id | CHAR(36) | NOT NULL, UNIQUE, ascii_bin | 规范指数标识（UUID，应用生成） |
| index_code | VARCHAR(32) | NOT NULL, UNIQUE | 规范指数代码（来源 ts_code，含 .SH/.SZ/.CSI/.SI 后缀） |
| created_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

表注释：`'指数主数据（大盘指数、申万行业指数、中信指数）'`
唯一约束：`uq_index_current_index_code (index_code)`、`uq_index_current_index_id (index_id)`

- 业务身份：`index_code`（来源 ts_code 即规范代码，后缀区分市场，全局唯一）。
- 生命周期：由本功能首次见到合法 `ts_code` 时自举注册（幂等 upsert，research 决策 1）；
  首期不提供删除入口，停用由后续治理功能决定。
- 后缀白名单为部署账户实测全集 `.SH/.SZ/.CSI/.SI/.CI/.NH/.BJ/.CNI`
  （2026-08-02 实测；spec Clarifications 与边界情况）；非法后缀或空代码不得注册，
  记录为无效质量问题。

### 2.2 `index_provider_mapping` —— 来源标识映射

| 列 | 类型 | 约束/默认 | 注释 |
|----|------|-----------|------|
| id | BIGINT UNSIGNED | PK AUTO_INCREMENT | 主键ID |
| provider_code | VARCHAR(32) | NOT NULL | 数据来源代码（如 tushare） |
| provider_security_id | VARCHAR(64) | NOT NULL | 来源指数标识（ts_code） |
| index_id | CHAR(36) | NOT NULL, ascii_bin | 规范指数标识（FK 语义指向 index_current） |
| created_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

表注释：`'指数来源标识映射（一个来源标识只映射一个规范指数标识）'`
唯一约束：`uq_index_provider_mapping (provider_code, provider_security_id)`

- 一个 `(provider_code, provider_security_id)` 只能映射一个 `index_id`
  （沿用 003 股票映射语义）；同一 `index_id` 可有多来源映射。
- 该表只承载身份解析，业务键一律使用 `index_id`（spec FR-007）。

## 3. ClickHouse 业务表 `index_factor`

### 3.1 引擎与物理布局

```sql
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (trade_date, index_id)
```

- 行身份 = `(trade_date, index_id)`（spec FR-007）；同一身份重复写入由
  `ReplacingMergeTree` 按 `updated_at` 版本列收敛，保留最新版本。
- 按月分区；无 TTL，长期保留，清理按分区显式执行（NFR-009）。
- 中文表注释与每列中文 COMMENT 随 DDL 落库（`python -m lucking.clickhouse migrate`）。

### 3.2 身份列

| 列 | 类型 | 注释 |
|----|------|------|
| trade_date | Date | 交易日期 |
| index_id | FixedString(36) | 规范指数标识（index_current.index_id） |
| index_code | String | 规范指数代码（来源 ts_code，含后缀） |
| updated_at | DateTime64(3) | 应用写入版本时间（UTC，同批相同、跨重试递增） |

### 3.3 基础行情列（不复权，来源恒返回）

| 列 | 类型 | 注释 |
|----|------|------|
| open | Decimal(12,4) | 开盘价 |
| high | Decimal(12,4) | 最高价 |
| low | Decimal(12,4) | 最低价 |
| close | Decimal(12,4) | 收盘价 |
| pre_close | Decimal(12,4) | 昨收价 |
| change | Decimal(12,4) | 涨跌额 |
| pct_chg | Decimal(12,4) | 涨跌幅（%） |
| vol | Decimal(24,2) | 成交量（手） |
| amount | Decimal(24,2) | 成交额（千元） |

### 3.4 技术因子列（均不复权；默认 `Nullable(Decimal(12,4))`）

因子名 = 来源字段去掉 `_bfq` 后缀（research 决策 7）；缺失以 NULL 保存，
与“必需字段缺失”的无效记录严格区分（ED-004）。按类别分列如下。

**趋势/均线类（20）**

| 列 | 注释 |
|----|------|
| ma_5 / ma_10 / ma_20 / ma_30 / ma_60 / ma_90 / ma_250 | 简单移动平均（5/10/20/30/60/90/250 日） |
| ema_5 / ema_10 / ema_20 / ema_30 / ema_60 / ema_90 / ema_250 | 指数移动平均（5~250 日） |
| expma_12 / expma_50 | 指数平均数（12/50 日） |
| bbi | BBI 多空指标 |
| macd | MACD 值 |
| macd_dea | MACD 信号线（DEA） |
| macd_dif | MACD 差离值（DIF） |

**摆动/超买超卖类（19）**

| 列 | 注释 |
|----|------|
| kdj | KDJ 随机指标 |
| kdj_k | KDJ K 线 |
| kdj_d | KDJ D 线 |
| rsi_6 / rsi_12 / rsi_24 | 相对强弱指标 RSI（6/12/24 日） |
| cci | CCI 顺势指标 |
| wr / wr1 | 威廉指标（WR/WR1） |
| bias1 / bias2 / bias3 | BIAS 乖离率（1/2/3） |
| psy | 心理线 PSY |
| psyma | 心理线均值 |
| roc | 变动率 ROC |
| maroc | 变动率均值 |
| mfi | MFI 资金流量指标 |
| mtm | 动量指标 MTM |
| mtmma | 动量指标均值 |

**通道类（13）**

| 列 | 注释 |
|----|------|
| boll_lower / boll_mid / boll_upper | 布林带下/中/上轨 |
| ktn_down / ktn_mid / ktn_upper | 肯特纳通道下/中/上轨 |
| taq_up / taq_mid / taq_down | 唐安奇（海龟）通道上/中/下轨 |
| xsii_td1 / xsii_td2 / xsii_td3 / xsii_td4 | 薛斯通道 II（1~4） |

**动量/能量类（22）**

| 列 | 注释 |
|----|------|
| dmi_pdi / dmi_mdi | 动向指标 +DI / -DI |
| dmi_adx / dmi_adxr | 动向指标 ADX / ADXR |
| obv | 能量潮 OBV |
| vr | VR 容量比率 |
| emv / maemv | 简易波动 EMV / 其均值 |
| cr | CR 价格动量 |
| brar_ar / brar_br | BRAR 情绪指标 AR / BR |
| dpo / madpo | 区间震荡线 DPO / 其均值 |
| dfma_dif / dfma_difma | 平行线差 / 其均值 |
| asi / asit | 振动升降指标 ASI / 其均值 |
| atr | 真实波幅均值 ATR |
| mass / ma_mass | 梅斯线 MASS / 其均值 |
| trix / trma | 三重指数平滑 TRIX / 其均值 |

**其他（4，`Nullable(UInt16)`）**

| 列 | 类型 | 注释 |
|----|------|------|
| updays | Nullable(UInt16) | 连涨天数 |
| downdays | Nullable(UInt16) | 连跌天数 |
| topdays | Nullable(UInt16) | 当前最高价是近 N 周期内最高价的最大值（N 为周期数） |
| lowdays | Nullable(UInt16) | 当前最低价是近 N 周期内最低价的最小值 |

## 4. 审计表复用（`market_data_sync_run/attempt/issue`）

不新建审计表；复用 005 三表，新增数据类取值 `data_kind='INDEX_FACTOR'`：

- **run 表**：`run_key` 输入 `data_kind + SCHEDULED + schedule_slug + scheduled_for_utc + target_trade_date`
  （增量）或 `data_kind + BACKFILL + backfill_batch_id + target_trade_date`（回补）；
  `SUCCEEDED` 不可重开；`scope_fingerprint` 仅审计不参与 run_key。
- **attempt 表**：唯一键 `(run_id, attempt_no)`、`flow_run_id` 唯一、租约固定 2100 秒
  （必须大于提取 deadline）、全部提取计数（received/valid/added/updated/unchanged/
  duplicate/invalid/conflict）、`provider_retry_count ≤ 3`。
- **issue 表**：attempt_id 关联、统一问题类别全集（在既有 21 类基础上新增
  `UNKNOWN_INDEX_IDENTITY` 类别，与 `UNKNOWN_STOCK_IDENTITY` 平行——指数身份
  解析失败即记录该类别，其余类别沿用 005 全集）、脱敏摘要（哈希 + 白名单），
  禁止 Token/连接串/原始行。

## 5. 幂等与发布语义

1. **身份注册**：批次校验前，按 `ts_code` 后缀白名单解析并幂等 upsert
   `index_current`/`index_provider_mapping`（MySQL 唯一约束兜底）；非法后缀、
   空代码 → `invalid_count` + 脱敏 issue，跳过该条。
2. **校验**：交易日归属、`index_id` 可解析、必需字段（trade_date、index_code、
   基础行情列）有效；完全相同重复去重计 `duplicate_count`；同键字段冲突抛
   `RECORD_CONFLICT` 整批失败（不得任意覆盖，spec FR-011）。
3. **发布**：有效行以单 block JSONEachRow 批量 INSERT `index_factor`；
   INSERT 前 `SELECT ... FINAL` 读取既有同键行，计算 added/updated/unchanged
   计数（仅审计用途）；`ReplacingMergeTree(updated_at)` 保证同键替换。
4. **终态**：发布成功后在**同一 MySQL 事务**写入 attempt 计数与终态、run 终态；
   失败时记录失败终态，已写入 ClickHouse 的数据不受影响（spec FR-013）。
5. **回补幂等**：逐日独立 `resolve`（START/SKIP_SUCCEEDED/RETRY/IN_PROGRESS），
   键 = `backfill_batch_id + data_kind + target_trade_date`；已成功日期跳过，
   失败日期可安全重试（spec FR-018）。

## 6. 需求追溯

| 模型/行为 | 需求 |
|-----------|------|
| `index_current`/`index_provider_mapping` | FR-007、FR-009、FR-010、ED-004、ED-006 |
| `index_factor` 表（身份 + 基础行情 + 78 因子） | FR-008、FR-010、ED-005 |
| 单 block 发布 + 同键替换 | FR-009、FR-013、NFR-003、SC-003 |
| 审计三表复用（data_kind=INDEX_FACTOR） | FR-011、FR-012、NFR-004、SC-008 |
| 按月分区、无 TTL | NFR-009 |
| 身份自举注册 | ED-004（合法后缀注册、非法跳过）、研究决策 1 |
