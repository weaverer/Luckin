# 数据模型：A股行情数据交易日同步

## 1. 数据所有权与存储职责

- ClickHouse 拥有五张分析型行情业务表：日线行情、复权因子、基本面指标、
  周K线、月K线（周线与月线独立建模）。
- MySQL 拥有按 `data_kind` 共享的三张事务型同步审计表：同步运行、执行尝试、
  数据质量问题（幂等、状态机与并发认领的权威存储）。
- 现有 `trading_calendar`（CN-S 上交所日历）继续由交易日历领域拥有；
  行情同步只读取它判断交易日，不得创建或修改日历数据。
- 现有 `stock_current` 与 `stock_provider_mapping` 继续由股票列表领域拥有；
  行情同步只读取它们以解析稳定 `stock_id`，不得创建或修改股票主数据。
- Prefect 拥有编排运行状态，但不替代 MySQL 中的业务运行与尝试审计。
- 单日全部分页候选只存在于进程内存；不持久化原始 Tushare 行或完整响应。
- 应用 Redis 不参与本功能。

业务事件时间使用 UTC `DATETIME(6)`；MySQL 宪章治理字段 `created_at/updated_at`
使用数据库维护的 `DATETIME`；应用读取后均恢复为 aware UTC。
`trade_date` 使用无歧义的 `DATE` 表示交易日；周/月线的 `trade_date`
为来源返回的该周期最后交易日（每周五或月末最后一个交易日）。

### 1.1 存储选型与一致性语义

- 行情业务数据属分析型数据，由宪章 II 分配给 ClickHouse；按交易日批量追加、
  范围聚合查询为主、回补后约千万行的规模适合列存与按月分区。
- ClickHouse 不提供 MySQL 式事务回滚、行级 UPDATE/DELETE 与 UNIQUE 约束：
  幂等由 `ReplacingMergeTree` 同键替换与单 block 原子 INSERT 保证，
  发布语义见 §12。
- 三张 MySQL 审计表不申请宪章 VI 例外，统一遵循 §1.2。
- 五张 ClickHouse 业务表不适用宪章 VI 的 MySQL 治理（引擎无自增主键与
  行级事务，属“外部引擎完全管理”的豁免情形）：逐表记录引擎、排序键、
  分区与幂等语义；列注释使用 ClickHouse 引擎注释语法。

### 1.2 宪章 VI 统一物理治理（MySQL 审计表）

以下三张表均为项目自有新建业务表，统一遵循：

- `id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID'` 为物理主键。
- UUID 字段继续作为跨层稳定业务标识，使用 `CHAR(36)` 并建立 `UNIQUE`。
- 每表包含：
  `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'`；
  `updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'`。
- `created_at/updated_at` 完全由数据库维护；应用不得显式覆盖。
- 下列每个字段表的“说明”列即该列必须使用的非空中文 `COMMENT`；
  每个实体标题下单独给出的“表注释”即迁移必须使用的中文表 `COMMENT`。
- ORM、Alembic 迁移和实际 `SHOW CREATE TABLE` 必须在主键、唯一键、外键、
  默认值、`ON UPDATE`、排序规则及中文注释上完全一致。

## 2. ClickHouse 业务表公共设计

五张业务表统一采用：

- 引擎：`ReplacingMergeTree(updated_at)`，以 `updated_at` 为版本列，
  同排序键重复写入时保留版本最大的行。
- 排序键 ORDER BY：`(trade_date, stock_id)`（周/月线同构，`trade_date`
  为周期最后交易日）。
- 分区：`PARTITION BY toYYYYMM(trade_date)`，按月分区，支持按分区裁剪
  与按周期清理。
- 采样与索引：`SAMPLE BY` 不使用；`SETTINGS index_granularity` 采用默认。
- 无自增主键、无 UUID 列；行身份由排序键表达，写入幂等靠同键替换。
- `updated_at` 为应用写入的 UTC 时间戳（秒级精度 `DateTime64(3)`），
  作为版本列与最近写入时间；同一批次内逐行相同，跨重试递增。
- 每次写入必须携带全部规范列；不存在的值以 NULL 写入（基本面空字段）。
- 列注释与表注释使用 ClickHouse 引擎注释语法（`COMMENT`），
  迁移文件与 `SHOW CREATE TABLE` 必须一致。

## 3. DailyQuote（ClickHouse）

**表名**：`daily_quote`

**表注释**：`A股日线行情`

表示某只股票在某个交易日未复权行情（开/高/低/收、昨收、涨跌额、涨跌幅、
成交量、成交额）的长期有效业务事实。

| 列 | ClickHouse 类型 | 可空 | 说明 |
|----|-----------------|------|------|
| `trade_date` | `Date` | 否 | 交易日 |
| `stock_id` | `FixedString(36)` | 否 | 项目规范股票业务UUID |
| `venue_code` | `FixedString(4)` | 否 | 规范交易场所代码：XSHG、XSHE或XBSE |
| `security_code` | `String` | 否 | 来源明确返回的规范证券代码 |
| `open` | `Decimal(12,4)` | 否 | 未复权开盘价 |
| `high` | `Decimal(12,4)` | 否 | 未复权最高价 |
| `low` | `Decimal(12,4)` | 否 | 未复权最低价 |
| `close` | `Decimal(12,4)` | 否 | 未复权收盘价 |
| `pre_close` | `Decimal(12,4)` | 否 | 昨收价（除权后） |
| `change` | `Decimal(12,4)` | 否 | 涨跌额 |
| `pct_chg` | `Decimal(8,3)` | 否 | 涨跌幅（百分比，基于除权昨收） |
| `vol` | `Decimal(24,2)` | 否 | 成交量（手） |
| `amount` | `Decimal(24,2)` | 否 | 成交额（千元） |
| `updated_at` | `DateTime64(3)` | 否 | 最近写入UTC时间（版本列） |

**键与规则**：

- 排序键：`(trade_date, stock_id)`；同键重复写入以 `updated_at` 最新者为准。
- 停牌股票当日无记录属正常业务结果，不产生行。
- 成交量单位“手”（1 手 = 100 股）、成交额单位“千元”为来源约定，
  消费方复权计算时按需换算。
- 不保存 `ts_code`、Provider code、原始 payload 或范围外字段。

## 4. AdjFactor（ClickHouse）

**表名**：`adj_factor`

**表注释**：`A股日线复权因子`

表示某只股票在某个交易日的日线复权因子，用于前/后复权计算。

| 列 | ClickHouse 类型 | 可空 | 说明 |
|----|-----------------|------|------|
| `trade_date` | `Date` | 否 | 交易日 |
| `stock_id` | `FixedString(36)` | 否 | 项目规范股票业务UUID |
| `venue_code` | `FixedString(4)` | 否 | 规范交易场所代码：XSHG、XSHE或XBSE |
| `security_code` | `String` | 否 | 来源明确返回的规范证券代码 |
| `adj_factor` | `Decimal(20,6)` | 否 | 当日复权因子 |
| `updated_at` | `DateTime64(3)` | 否 | 最近写入UTC时间（版本列） |

**键与规则**：

- 排序键：`(trade_date, stock_id)`；`adj_factor` 必须大于 0。
- 来源未返回因子时不产生行；不保存 `ts_code` 或范围外字段。

## 5. DailyBasic（ClickHouse）

**表名**：`daily_basic`

**表注释**：`A股每日基本面指标`

表示某只股票在某个交易日的估值、换手、量比、市值、股本、除息与涨跌停状态
指标集合；亏损导致的 PE/PB 等空值以 NULL 保存。

| 列 | ClickHouse 类型 | 可空 | 说明 |
|----|-----------------|------|------|
| `trade_date` | `Date` | 否 | 交易日 |
| `stock_id` | `FixedString(36)` | 否 | 项目规范股票业务UUID |
| `venue_code` | `FixedString(4)` | 否 | 规范交易场所代码：XSHG、XSHE或XBSE |
| `security_code` | `String` | 否 | 来源明确返回的规范证券代码 |
| `pe` | `Nullable(Decimal(16,4))` | 是 | 市盈率（亏损公司为空） |
| `pe_ttm` | `Nullable(Decimal(16,4))` | 是 | 市盈率TTM（亏损公司为空） |
| `pb` | `Nullable(Decimal(16,4))` | 是 | 市净率（亏损公司为空） |
| `ps` | `Nullable(Decimal(16,4))` | 是 | 市销率 |
| `ps_ttm` | `Nullable(Decimal(16,4))` | 是 | 市销率TTM |
| `dv_ratio` | `Nullable(Decimal(12,4))` | 是 | 股息率 |
| `dv_ttm` | `Nullable(Decimal(12,4))` | 是 | 股息率TTM |
| `total_share` | `Nullable(Decimal(24,4))` | 是 | 总股本（万股） |
| `float_share` | `Nullable(Decimal(24,4))` | 是 | 流通股本（万股） |
| `free_share` | `Nullable(Decimal(24,4))` | 是 | 自由流通股本（万股） |
| `total_mv` | `Nullable(Decimal(24,4))` | 是 | 总市值（万元） |
| `circ_mv` | `Nullable(Decimal(24,4))` | 是 | 流通市值（万元） |
| `turnover_rate` | `Nullable(Decimal(12,4))` | 是 | 换手率（百分比） |
| `turnover_rate_f` | `Nullable(Decimal(12,4))` | 是 | 自由流通换手率（百分比） |
| `volume_ratio` | `Nullable(Decimal(12,4))` | 是 | 量比 |
| `limit_status` | `Nullable(UInt8)` | 是 | 涨跌停状态：0平盘、1涨停、2跌停、3炸板、4跌停打开、5跳水、6一字涨停、7一字跌停 |
| `updated_at` | `DateTime64(3)` | 否 | 最近写入UTC时间（版本列） |

**键与规则**：

- 排序键：`(trade_date, stock_id)`。
- NULL 表示来源未返回该值（亏损公司 PE/PB 等），与“必需身份字段缺失”
  的无效记录严格区分。
- 来源 19 个字段中的收盘价 `close` 与 `daily_quote.close` 语义重复，
  遵循单表事实原则不重复保存。
- 不保存 `ts_code`、Provider code、原始 payload 或范围外字段。

## 6. WeeklyKline（ClickHouse）

**表名**：`weekly_kline`

**表注释**：`A股周K线`

表示某只股票在某个自然周内合并的行情事实，包含未复权开/高/低/收价及
成交量、成交额、涨跌额、涨跌幅与计算截至日期。

| 列 | ClickHouse 类型 | 可空 | 说明 |
|----|-----------------|------|------|
| `trade_date` | `Date` | 否 | 周期最后交易日（每周五或该周最后交易日） |
| `stock_id` | `FixedString(36)` | 否 | 项目规范股票业务UUID |
| `venue_code` | `FixedString(4)` | 否 | 规范交易场所代码：XSHG、XSHE或XBSE |
| `security_code` | `String` | 否 | 来源明确返回的规范证券代码 |
| `open` | `Decimal(12,4)` | 否 | 未复权周期开盘价 |
| `high` | `Decimal(12,4)` | 否 | 未复权周期最高价 |
| `low` | `Decimal(12,4)` | 否 | 未复权周期最低价 |
| `close` | `Decimal(12,4)` | 否 | 未复权周期收盘价 |
| `vol` | `Decimal(24,2)` | 否 | 周期成交量（手） |
| `amount` | `Decimal(24,2)` | 否 | 周期成交额（千元） |
| `change` | `Decimal(12,4)` | 否 | 周期涨跌额 |
| `pct_chg` | `Decimal(8,3)` | 否 | 周期涨跌幅（百分比） |
| `end_date` | `Nullable(Date)` | 是 | 来源计算截至日期；与trade_date一致时为空 |
| `updated_at` | `DateTime64(3)` | 否 | 最近写入UTC时间（版本列） |

## 7. MonthlyKline（ClickHouse）

**表名**：`monthly_kline`

**表注释**：`A股月K线`

表示某只股票在某个自然月内合并的行情事实，包含未复权开/高/低/收价及
成交量、成交额、涨跌额、涨跌幅与计算截至日期。
结构与 `weekly_kline` 相同但独立建模（用户明确要求两个独立数据模型），
`trade_date` 为月末最后一个交易日；两表字段与分区策略可独立演进。

| 列 | ClickHouse 类型 | 可空 | 说明 |
|----|-----------------|------|------|
| `trade_date` | `Date` | 否 | 周期最后交易日（月末最后一个交易日） |
| `stock_id` | `FixedString(36)` | 否 | 项目规范股票业务UUID |
| `venue_code` | `FixedString(4)` | 否 | 规范交易场所代码：XSHG、XSHE或XBSE |
| `security_code` | `String` | 否 | 来源明确返回的规范证券代码 |
| `open` | `Decimal(12,4)` | 否 | 未复权周期开盘价 |
| `high` | `Decimal(12,4)` | 否 | 未复权周期最高价 |
| `low` | `Decimal(12,4)` | 否 | 未复权周期最低价 |
| `close` | `Decimal(12,4)` | 否 | 未复权周期收盘价 |
| `vol` | `Decimal(24,2)` | 否 | 周期成交量（手） |
| `amount` | `Decimal(24,2)` | 否 | 周期成交额（千元） |
| `change` | `Decimal(12,4)` | 否 | 周期涨跌额 |
| `pct_chg` | `Decimal(8,3)` | 否 | 周期涨跌幅（百分比） |
| `end_date` | `Nullable(Date)` | 是 | 来源计算截至日期；与trade_date一致时为空 |
| `updated_at` | `DateTime64(3)` | 否 | 最近写入UTC时间（版本列） |

**周/月K线通用规则**：

- 排序键：`(trade_date, stock_id)`；同一周期多日同步返回相同 `trade_date`，
  同键替换保证幂等更新，不重新计算周期行情。
- 未复权价格缺失的记录必须隔离；来源未提供时不得伪造默认值。
- `end_date` 仅在部署账户验证返回且与 `trade_date` 不同时填写。
- 不保存 `ts_code`、Provider code、原始 payload 或范围外字段。

## 8. MarketDataSyncRun（MySQL）

**表名**：`market_data_sync_run`

**表注释**：`行情数据同步运行`

表示一个计划时点，或某个回补批次中针对某 `data_kind` 某目标交易日的权威运行。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `run_id` | `CHAR(36)` ASCII | 否 | 同步运行业务UUID |
| `run_key` | `CHAR(64)` ASCII | 否 | 规范运行身份的SHA-256摘要 |
| `data_kind` | `VARCHAR(16)` ASCII | 否 | 数据类：DAILY_QUOTE、ADJ_FACTOR、DAILY_BASIC、WEEKLY_KLINE或MONTHLY_KLINE |
| `run_kind` | `VARCHAR(12)` ASCII | 否 | 运行类型：计划运行或历史回补 |
| `schedule_slug` | `VARCHAR(64)` ASCII | 是 | 计划运行标识；回补为空 |
| `scheduled_for` | `DATETIME(6)` | 是 | 计划运行原定UTC时点；回补为空 |
| `backfill_batch_id` | `VARCHAR(128)` ASCII | 是 | 历史回补批次幂等键；计划运行为空 |
| `target_trade_date` | `DATE` | 否 | 运行目标交易日 |
| `scope_fingerprint` | `CHAR(64)` ASCII | 否 | 交易日、数据类与契约版本的审计范围摘要 |
| `status` | `VARCHAR(12)` ASCII | 否 | 运行状态：待执行、执行中、成功或失败 |
| `attempt_count` | `INT UNSIGNED` | 否 | 已创建执行尝试数 |
| `successful_attempt_id` | `CHAR(36)` ASCII | 是 | 唯一成功执行尝试的业务UUID |
| `published_at` | `DATETIME(6)` | 是 | 成功发布的UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- 物理主键为 `id`，`run_id` 和 `run_key` 分别建立唯一约束。
- `run_key` 唯一；计划运行输入为
  `data_kind + SCHEDULED + schedule_slug + scheduled_for_utc + target_trade_date`；
  历史回补输入为
  `data_kind + BACKFILL + backfill_batch_id + target_trade_date`。
- `scope_fingerprint` 只记录实际处理范围用于审计，不参与 `run_key`；
  Provider、配置和实际启动时间也不得改变业务运行身份。
- `SCHEDULED` 必须提供 `schedule_slug` 和 `scheduled_for`，且 `backfill_batch_id` 为空；
  `BACKFILL` 必须提供非空 `backfill_batch_id`，且计划字段为空。
- 不同 `data_kind` 永远形成不同 run；同一 `data_kind` 不同交易日形成不同 run。
- 同一回补批次键与数据类中的每个交易日形成独立 run；相同批次键、数据类与
  交易日重复提交复用该 run，新批次键允许主动刷新同一历史交易日。
- Provider code 不进入 `run_key`，更换 Provider 不改变业务运行身份。
- `SUCCEEDED` 不可重开；重复触发直接返回既有结果且不调用 Provider。
- `FAILED` 只允许显式重试为 `RUNNING`，并新增 attempt。
- `successful_attempt_id` 只在同一事务将 run 置为 `SUCCEEDED` 时设置；
  迁移在 attempt 表创建后添加 FK。

## 9. MarketDataSyncAttempt（MySQL）

**表名**：`market_data_sync_attempt`

**表注释**：`行情数据同步执行尝试`

保存每次计划执行、历史回补或失败重试的起止、来源、提取计数和终态；
进入终态后业务内容不可再修改。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `attempt_id` | `CHAR(36)` ASCII | 否 | 执行尝试业务UUID |
| `run_id` | `CHAR(36)` ASCII | 否 | 所属同步运行的业务UUID |
| `attempt_no` | `INT UNSIGNED` | 否 | 运行内从 1 递增 |
| `flow_run_id` | `VARCHAR(64)` ASCII | 否 | Prefect工作流运行标识 |
| `provider_code` | `VARCHAR(32)` ASCII | 否 | 本次选中的数据来源代码 |
| `status` | `VARCHAR(12)` ASCII | 否 | 尝试状态：执行中、成功、失败或已放弃 |
| `started_at` | `DATETIME(6)` | 否 | 实际开始的UTC时间 |
| `lease_expires_at` | `DATETIME(6)` | 否 | 运行租约到期的UTC时间 |
| `completed_at` | `DATETIME(6)` | 是 | 进入终态的UTC时间 |
| `provider_request_count` | `SMALLINT UNSIGNED` | 否 | 实际数据来源请求次数 |
| `provider_retry_count` | `SMALLINT UNSIGNED` | 否 | 初次调用之外的重试次数 |
| `provider_page_count` | `SMALLINT UNSIGNED` | 否 | 已成功取得的提取批次数，包含空终止批 |
| `provider_page_limit` | `INT UNSIGNED` | 否 | 本次批次行数上限 |
| `provider_last_page_count` | `INT UNSIGNED` | 否 | 终止批次原始行数 |
| `received_count` | `INT UNSIGNED` | 否 | 来源行数 |
| `valid_count` | `INT UNSIGNED` | 否 | 去重后有效候选数 |
| `added_count` | `INT UNSIGNED` | 否 | 新增记录数 |
| `updated_count` | `INT UNSIGNED` | 否 | 业务字段更新数 |
| `unchanged_count` | `INT UNSIGNED` | 否 | 已确认但业务字段未变化数 |
| `duplicate_count` | `INT UNSIGNED` | 否 | 已解决的完全相同重复数 |
| `invalid_count` | `INT UNSIGNED` | 否 | 无效记录数 |
| `conflict_count` | `INT UNSIGNED` | 否 | 身份或字段冲突数 |
| `candidate_digest` | `CHAR(64)` ASCII | 是 | 规范排序候选的SHA-256摘要 |
| `error_category` | `VARCHAR(48)` ASCII | 是 | 统一安全错误类别 |
| `error_summary` | `VARCHAR(500)` | 是 | 脱敏摘要 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- 物理主键为 `id`，`attempt_id` 建立唯一约束。
- 唯一键：`(run_id, attempt_no)`；`flow_run_id` 唯一；一个 run 最多一个
  `SUCCEEDED` attempt。
- `lease_expires_at` 在认领 attempt 时使用数据库 UTC 时钟设置为当前时间加
  固定租约（大于 Provider 截止时间）；创建后不续租。Repository 必须使用
  数据库 UTC 比较租约，不使用 Worker 本地时钟。
- 成功要求：
  - 来源提取覆盖证据完整（请求全部完成、未触顶或已验证续取终止）；
  - `valid_count > 0`；
  - `conflict_count = 0`；`invalid_count` 可以大于 0，但每一条都必须有
    `UNKNOWN_STOCK_IDENTITY` issue，且该记录未进入业务表；
  - ClickHouse 批量 INSERT 已成功且 MySQL 审计终态已在同一事务提交。
- 失败 attempt 的所有计数也必须保存，以支持五分钟排障。
- `provider_retry_count ≤ 3`；确定性错误为 0。

## 10. MarketDataSyncIssue（MySQL）

**表名**：`market_data_sync_issue`

**表注释**：`行情数据同步质量问题`

保存 attempt 级或记录级的脱敏数据质量问题。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `issue_id` | `CHAR(36)` ASCII | 否 | 质量问题业务UUID |
| `attempt_id` | `CHAR(36)` ASCII | 否 | 所属执行尝试的业务UUID |
| `category` | `VARCHAR(48)` ASCII | 否 | 统一问题类别 |
| `provider_security_id_hash` | `CHAR(64)` ASCII | 是 | 可选数据来源股票标识摘要 |
| `venue_code` | `CHAR(4)` ASCII | 是 | 安全定位用交易场所代码 |
| `security_code` | `VARCHAR(32)` ASCII | 是 | 安全定位代码 |
| `field_name` | `VARCHAR(64)` ASCII | 是 | 规范字段名 |
| `safe_summary` | `VARCHAR(500)` | 否 | 白名单脱敏摘要 |
| `payload_hash` | `CHAR(64)` ASCII | 是 | 原始候选摘要，不保存原文 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- 物理主键为 `id`，`issue_id` 建立唯一约束；`attempt_id` 外键引用
  attempt 的唯一业务标识。
- issue 创建后业务内容不可修改；因此正常情况下 `updated_at = created_at`。

问题类别至少包括：

```text
PROVIDER_RATE_LIMITED
PROVIDER_UNAVAILABLE
PROVIDER_DEADLINE
AUTHENTICATION
QUOTA_EXCEEDED
EMPTY_AGGREGATE
RESPONSE_CAPPED
CONTINUATION_INCOMPLETE
PAGINATION_NOT_VERIFIED
PAGINATION_NOT_ADVANCING
REPEATED_PAGE
MAX_PAGES_EXCEEDED
TRADE_DATE_MISMATCH
PERIOD_MISMATCH
INVALID_FIELD
UNKNOWN_STOCK_IDENTITY
IDENTITY_CONFLICT
DUPLICATE
RECORD_CONFLICT
ABANDONED
PERSISTENCE_ERROR
```

问题表禁止 Token、连接串、完整请求/响应、供应商原始消息和原始行。
每个未进入有效集合的输入必须有可判断类别；完全重复可记录为 `DUPLICATE`
并在去重后继续成功。非交易日跳过属正常业务结果，不产生 issue。

## 11. 关系与状态转换

```text
TradingCalendar 1 ── * DailyQuote / AdjFactor / DailyBasic / WeeklyKline / MonthlyKline
    （交易日判断，读取不修改）
StockCurrent 1 ── * 五张 ClickHouse 业务表
MarketDataSyncRun 1 ── * MarketDataSyncAttempt
MarketDataSyncAttempt 1 ── * MarketDataSyncIssue
```

跨表引用均使用带唯一约束的 UUID 业务标识；ClickHouse 表不保存运行引用
（写入时间线由 MySQL 审计表提供）。正常业务流程不删除以上实体；
迁移 downgrade 仅用于明确的开发回滚。

### Run

```text
PENDING → RUNNING → SUCCEEDED
                  ↘ FAILED → RUNNING
```

- `PENDING → RUNNING`：数据库原子认领周期并创建 attempt。
- `RUNNING → SUCCEEDED`：ClickHouse 批量写入成功 + MySQL 审计终态同事务提交。
- `RUNNING → FAILED`：Provider、验证、身份、ClickHouse 写入或持久化失败。
- `FAILED → RUNNING`：显式重试原运行并创建新 attempt。
- `SUCCEEDED` 为不可重开终态。

### Attempt

```text
RUNNING → SUCCEEDED
        ↘ FAILED
        ↘ ABANDONED
```

- attempt 终态不可重开；`RUNNING` attempt 达到固定 `lease_expires_at` 后，
  下一次显式重试在数据库事务内再次确认已过期，先把旧 attempt 标记为
  `ABANDONED` 并写 issue，再创建新 attempt。首版不续租、不增加心跳字段。

### 历史回补交易日解析

回补 Flow 对区间内每个交易日按 `backfill_batch_id + data_kind + target_trade_date`
解析既有 run：

1. 不存在：创建 `BackfillMarketDataCommand`，形成首次 BACKFILL run/attempt。
2. `SUCCEEDED`：跳过 Provider 调用并计入回补汇总的成功跳过数。
3. `FAILED`：取得原 `run_id`，转换为 `RetryMarketDataSyncCommand` 并新增 attempt。
4. `RUNNING` 且租约有效：不创建第二 attempt，标记该日仍在进行。
5. `RUNNING` 且租约过期：先将旧 attempt 置为 `ABANDONED` 并记录问题，
   再以原 `run_id` 转换为 Retry 命令。
6. 相同批次键、数据类与交易日即使 Provider、配置或 `scope_fingerprint` 变化，
   仍解析到同一 run；主动刷新必须使用新的批次键。

交易日区间必须先整体校验并按首尾日期均计入的交易日数量展开：
2024-01-01 之前的日期、未来交易日、空区间或起始日晚于结束日时，
不得创建任何 run。

## 12. 身份解析与发布语义

### 身份解析

对每条规范候选：

1. 使用 `(provider_code, provider_security_id)` 查询现有 `stock_provider_mapping`。
2. 使用 `(CN-S, venue_code, security_code)` 查询 `stock_current`。
3. 两者命中同一 `stock_id`：接受。
4. Provider 映射缺失但规范键唯一命中：接受该 `stock_id`，但行情同步不创建
   Provider 映射；映射维护仍由股票列表领域负责。
5. 两者均缺失：`UNKNOWN_STOCK_IDENTITY`，保存脱敏 issue、增加 `invalid_count`
   并跳过该条；只要仍有其他有效记录，当日可以成功。
6. 两者指向不同股票或规范键不唯一：`IDENTITY_CONFLICT`，整批失败。

不得按股票简称猜测身份，不得创建或更新 `stock_current`。若全部输入都因未知身份
被跳过，因 `valid_count = 0` 整日失败。

### 发布语义（ClickHouse + MySQL 双写收敛）

所有候选在内存完成验证后：

1. 按唯一 `run_key` 锁定 MySQL run，确认状态为 `RUNNING` 且 attempt 所有权匹配；
   再次确认所有 `stock_id` 仍存在且身份一致。
2. 以一次 ClickHouse 批量 INSERT（单 block）写入对应业务表，
   每行携带统一的 `updated_at`（本次尝试的 UTC 时间）；同排序键已有行
   由 `ReplacingMergeTree` 以版本列取最新，块级原子性保证半批不可见。
3. INSERT 成功后，在同一 MySQL 事务内：
   - 写 attempt 的全部计数、摘要、`SUCCEEDED` 和完成时间；
   - 写 run 的 `successful_attempt_id`、`SUCCEEDED` 和 `published_at`；
   - 提交。
4. 任一步失败：MySQL run 保持非 `SUCCEEDED`；重试复用原运行新增 attempt，
   重新执行第 2、3 步。ClickHouse 侧不删除已写入数据，
   “失败批次零写入”的语义由单 block 原子性（不可见半批）与同键替换
   （重试后行集与成功执行一致）共同收敛。

注意：ClickHouse INSERT 成功但客户端未收到确认（网络断开）时，重试会以新的
`updated_at` 重写同键行，最终行集仍与成功执行一致；
“第 3 步失败但数据已写入”的情况同样在重试后收敛为一致行集。
查询任意时刻只能看到完整批次或上一状态，看不到半批结果。

## 13. 数据生命周期

- 五类行情记录长期保留，不因后续批次缺席自动清理；按月分区支持
  按分区显式清理（周线按周分区语义、月线按年分区语义均由消费方确认后执行）。
- run、attempt 和脱敏 issue 长期保留，用于幂等、审计和排障。
- 不保存候选快照、原始供应商行、行情以外的财务或预测字段。
- JSONL 日志按 10 MiB 轮转并保留 5 个归档；日志生命周期独立于两库。
- MySQL `id` 仅用于物理组织，不进入业务契约；UUID 业务标识和业务唯一键长期稳定；
  ClickHouse 行身份由排序键表达。

## 14. 查询语义

应用内查询必须提供 `data_kind` 与目标交易日（周/月线为周期最后交易日），
并可选按以下条件筛选：

- `stock_id`；
- `venue_code`；
- `security_code`。

查询执行于 ClickHouse，稳定排序为 `trade_date, stock_id`，分页限制
`1 ≤ limit ≤ 1000`、`offset ≥ 0`。
返回交易日（或周期）、项目股票 ID、规范 venue、证券代码和规范业务字段，
不返回 Provider 标识、运行问题或供应商字段。
查询仅供已完成授权的项目内部调用方使用；认证、授权和访问控制由调用入口负责。

## 15. 需求追溯

- 五张 ClickHouse 业务表、排序键和查询：FR-006/007/008/009/018，SC-002/004/007。
- 同键替换幂等且缺席不删除：FR-014，NFR-006/008，SC-005。
- MySQL run + attempt + issue（`data_kind` 参数化）：FR-001/002/003/004/005/012/013/016/020，
  NFR-001/003/004/007/009/010，SC-001/003/008/009。
- 全批校验与单 block 发布：FR-010/011/013/014，NFR-002/003/006，SC-002/003/005。
- 非交易日跳过与停牌处理：FR-001/015，NFR-009，SC-001。
- 股票身份与交易日历复用：FR-001/010，ED-006/007。
- Provider 隔离和字段限制：FR-008/019，ED-001 至 ED-008，NFR-005，SC-006/007。
- 存储分层与 ClickHouse 引擎豁免：宪章 II/VI；不改变 FR-009/012 的业务身份语义。
