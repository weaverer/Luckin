# 数据模型：股东数据交易日同步（008-sync-shareholder-data）

> `/speckit-plan` Phase 1 输出。依据：spec.md、research.md、项目宪章 1.2.0。
> 本功能**不新建任何 MySQL 表**：股票身份复用 003，同步审计复用 005；
> 唯一新增存储为 ClickHouse `shareholder_holding` 与
> `shareholder_count` 两张业务表（research 决策 2）。
> **结构性变更一项**（迁移 006，2026-08-06 应用）：`market_data_sync_run.data_kind`
> String(16)→32，例外登记见 §2.3。

## 1. 概览与数据归属

| 数据 | 存储 | 性质 | 说明 |
|------|------|------|------|
| 股票身份与来源映射 | MySQL（复用 003） | 事务型主数据 | `stock_current` + `stock_provider_mapping`，只读消费，无结构变更 |
| 前十大股东 / 前十大流通股东持仓 | ClickHouse（新建） | 分析型披露数据 | 单表 `shareholder_holding`，`holder_kind` 判别 TOP10 / TOP10_FLOAT |
| 股东人数 | ClickHouse（新建） | 分析型披露数据 | 单表 `shareholder_count` |
| 同步审计（run/attempt/issue） | MySQL（复用 005） | 事务型审计 | `market_data_sync_*`，新增 `data_kind` 取值 `TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/`HOLDER_COUNT` |

股东披露数据属于"按披露期追加、按报告期长期保留"的分析型数据，入
ClickHouse；股票身份与审计均复用既有表（research 决策 3），本功能不
拥有新的 MySQL 数据。

## 2. MySQL 表（全部复用；审计表一项结构性变更见 §2.3）

### 2.1 股票身份（003 复用）

`stock_current`/`stock_provider_mapping` 由 003 功能维护，本功能只读消费：

- 身份解析入口：`SqlAlchemyStockListRepository.provider_mappings(provider_code)`
  返回 `{provider_security_id: stock_id}`（如 `600000.SH → <uuid>`）；
- 覆盖范围：`stock_current` 的 `market_code='CN-S'`、
  `venue_code IN ('XSHG','XSHE','XBSE')`（含北交所）；
- 解析失败（ts_code 未映射，如退市股、新股未入列表）→ `invalid_count` +
  脱敏 issue（类别 `UNKNOWN_STOCK_IDENTITY`），跳过该条（spec ED-005）；
- 身份键 `stock_id String(36)` 即 ClickHouse 两表 `stock_id`
  的外键语义来源。

### 2.2 同步审计（005 复用）

`market_data_sync_run/attempt/issue` 三表复用，新增
`DataKind.TOP10_HOLDERS / TOP10_FLOAT_HOLDERS / HOLDER_COUNT`
三个取值（`DataKind` 纯枚举扩展；`market_data_sync_run.data_kind`
列宽经迁移 006 加宽，见 §2.3——与 005 每接口一 `data_kind`
的模式一致——`DAILY_QUOTE`/`ADJ_FACTOR`/`DAILY_BASIC`/
`WEEKLY_KLINE`/`MONTHLY_KLINE`）：

- **run 表**：`run_key` 输入 `<DATA_KIND> + SCHEDULED + schedule_slug +
  scheduled_for_utc + target_trade_date`（增量）或 `<DATA_KIND> +
  BACKFILL + backfill_batch_id + target_trade_date`（回补），
  `DATA_KIND` ∈ {`TOP10_HOLDERS`, `TOP10_FLOAT_HOLDERS`,
  `HOLDER_COUNT`}；`SUCCEEDED` 不可重开；`scope_fingerprint` 仅审计
  不参与 run_key。
- **attempt 表**：唯一键 `(run_id, attempt_no)`、`flow_run_id` 唯一、
  租约固定 2100 秒（必须大于提取 deadline 1500 秒）、全部提取计数
  （received/valid/added/updated/unchanged/duplicate/invalid/conflict）、
  `provider_retry_count ≤ 3`。**每接口独立 run/attempt**（3 Flow 拆分，
  用户显式要求）：任一接口失败只写该接口的 FAILED 终态，不影响
  其他两个接口的运行记录与计数。
- **issue 表**：attempt_id 关联；问题类别沿用 005 全集
  （含 `UNKNOWN_STOCK_IDENTITY`，无需新增类别）；脱敏摘要
  （哈希 + 白名单），禁止 Token/连接串/原始行。

### 2.3 宪章 VI 结论与例外登记

本功能不新建任何 MySQL 业务表（身份/审计全复用）；ClickHouse 两张表
属于宪章 II 明确划分的分析型数据存储，属宪章允许的"外部引擎承载业务
数据"情形，引擎、排序键、分区与幂等语义记录于 §3/§4。

**结构性变更一项（例外，2026-08-06 上线实测发现并应用迁移 006）**：

| 例外字段 | 表 | 变更 | 业务理由 | 唯一性保障 | 迁移影响 |
|---------|----|------|---------|-----------|---------|
| `data_kind` | `market_data_sync_run`（005 审计表，本功能复用） | 迁移 006：`String(16)` → `String(32)` | `TOP10_FLOAT_HOLDERS`（18 字符）超出原 16 字符列宽，增量 run 认领 INSERT 报 `DataError (1406) Data too long` | 不变：`run_key` UNIQUE 与 `run_id` 唯一键均未触及，加宽只增大容量 | 只增不缩，005 既有数据不受影响；`utf8mb4_bin` 排序规则保持；ORM 模型（`models/market_data.py`）与迁移一致 |

被拒绝的默认方案：新建本功能独立审计表（重复审计体系，违反宪章 II
边界）；缩短枚举取值（破坏 `run_key`/`scope_fingerprint` 与既有数据
一致性，且不可逆）。创建/更新时间字段语义由 005 功能保持。

## 3. ClickHouse 业务表

### 3.1 表一 `shareholder_holding`（前十大股东 / 前十大流通股东）

```sql
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(end_date)
ORDER BY (end_date, stock_id, holder_kind, holder_name)
```

- 行身份 = `(end_date, stock_id, holder_kind, holder_name)`
  （spec FR-007：股票标识 + 披露期 + 股东名称，`holder_kind` 区分
  前十大股东与前十流通股东两个名单——同一股东可能同时出现在两个名单，
  持仓数量/比例不同）；同一身份重复写入由 `ReplacingMergeTree` 按
  `updated_at` 版本列收敛，保留最新版本（更正公告修订语义，
  spec FR-010/ED-010）。
- 按报告期（end_date）月份分区；无 TTL，长期保留，清理按分区显式执行
  （NFR-009）。
- 中文表注释与每列中文 COMMENT 随 DDL 落库
  （`python -m lucking.clickhouse migrate` 注册）。

| 列 | 类型 | 注释 |
|----|------|------|
| end_date | Date | 披露期（报告期/截止日期，业务身份组成部分） |
| stock_id | FixedString(36) | 规范股票标识（stock_current.stock_id） |
| holder_kind | Enum8('TOP10' = 1, 'TOP10_FLOAT' = 2) | 股东名单类型（前十大股东/前十大流通股东） |
| holder_name | String | 股东名称（业务身份组成部分） |
| ann_date | Date | 公告日期（增量水位依据，最新公告值覆盖旧值） |
| stock_code | String | 来源股票代码（ts_code，含后缀） |
| hold_amount | Nullable(Decimal(24,2)) | 持有数量（股；实测 600000.SH 达 70 亿股，Decimal(12) 会溢出） |
| hold_ratio | Nullable(Decimal(12,4)) | 占总股本比例（%） |
| hold_float_ratio | Nullable(Decimal(12,4)) | 占流通股本比例（%） |
| hold_change | Nullable(Decimal(24,2)) | 持股变动（股，可为负） |
| holder_type | Nullable(String) | 股东类型（实测取值：一般企业、自然人、保险投资组合、开放式投资基金、证金等） |
| updated_at | DateTime64(3) | 应用写入版本时间（UTC，同批相同、跨重试递增） |

### 3.2 表二 `shareholder_count`（股东人数）

```sql
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(end_date)
ORDER BY (end_date, stock_id)
```

- 行身份 = `(end_date, stock_id)`（spec FR-007：股票标识 + 截止日期）；
  同一身份的更正公告按 `updated_at` 收敛为最新值（ED-010）。
- 与持仓表同分区与清理策略；注释与 DDL 注册方式相同。

| 列 | 类型 | 注释 |
|----|------|------|
| end_date | Date | 截止日期（股东户数统计日，业务身份组成部分） |
| stock_id | FixedString(36) | 规范股票标识（stock_current.stock_id） |
| ann_date | Date | 公告日期（增量水位依据） |
| stock_code | String | 来源股票代码（ts_code，含后缀） |
| holder_num | Nullable(UInt32) | 股东户数（实测 300199.SZ 为 98,777 户） |
| updated_at | DateTime64(3) | 应用写入版本时间（UTC，同批相同、跨重试递增） |

## 4. 幂等与发布语义

1. **水位计算（增量，按接口）**：水位 = 本接口数据的 `max(ann_date)
   FINAL`（research 决策 1）——`TOP10_HOLDERS` 取 `shareholder_holding
   WHERE holder_kind='TOP10'`、`TOP10_FLOAT_HOLDERS` 取
   `holder_kind='TOP10_FLOAT'`、`HOLDER_COUNT` 取 `shareholder_count`；
   **必须按接口（kind）分别取水位**：两 top10 接口写入同一张表，
   表级水位会让先运行的接口把后运行接口的当日公告一并跳过。
   表空则水位 = `2024-01-01`（与回补起点一致，首轮增量与回补重叠，
   幂等衔接，spec 边界情况）。窗口 =（水位, 目标日前一自然日]，
   逐日提取。
2. **提取（每接口）**：按公告日调用全市场查询（不传 ts_code，
   research 决策 1）：`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS` 用
   `ann_date=YYYYMMDD`，`HOLDER_COUNT` 用 `start_date=end_date=YYYYMMDD`；
   每页 6,000 行，`has_more=True` 时 `offset` 递增续取直至
   `has_more=False`；重复页、位置不前进、超过最大页数即不完整
   （spec FR-006/ED-003）。回补 `TOP10_*` 仅季度末（报告期）日期触发
   提取、`HOLDER_COUNT` 逐日（research 决策 1）。
3. **身份解析**：批次校验前，按 `provider_security_id` 查 003
   `provider_mappings`（tushare）解析 `stock_id`；未映射 → `invalid_count`
   + 脱敏 issue（`UNKNOWN_STOCK_IDENTITY`），跳过该条，不阻断整批
   （spec ED-005）。
4. **校验**：`end_date`/`ann_date` 为合法日期、`stock_id` 可解析、
   持仓记录 `holder_kind`/`holder_name` 非空、股东人数 `holder_num`
   有效；完全相同的重复行去重计 `duplicate_count`（spec FR-010）。
   字段集合与白名单严格相等，文档外字段出现即整批失败（ED-006）。
5. **发布**：有效行以单 block JSONEachRow 批量 INSERT 对应表；
   INSERT 前 `SELECT ... FINAL` 读取同键既有行计算
   added/updated/unchanged 计数（仅审计用途）；
   `ReplacingMergeTree(updated_at)` 保证同键替换（spec FR-010/SC-003）。
6. **终态**：发布成功后在**同一 MySQL 事务**写入 attempt 计数与终态、
   run 终态；失败时记录失败终态，已写入 ClickHouse 的数据不受影响
   （spec FR-013）。
7. **回补幂等**：逐日独立 `resolve`（START/SKIP_SUCCEEDED/RETRY/
   IN_PROGRESS），键 = `backfill_batch_id + <DATA_KIND> +
   target_trade_date`（`DATA_KIND` ∈ {`TOP10_HOLDERS`,
   `TOP10_FLOAT_HOLDERS`, `HOLDER_COUNT`}）；已成功日期跳过，失败日期
   可安全重试（spec FR-018）；三接口回补相互独立，任一接口失败不影响
   其他两个。回补的提取范围覆盖 2024-01-01 起全部披露数据
   （`top10_*` 按报告期季度末、`stk_holdernumber` 按公告日，
   research 决策 1）。

## 5. 需求追溯

| 模型/行为 | 需求 |
|-----------|------|
| 身份复用（003 `provider_mappings`，`UNKNOWN_STOCK_IDENTITY` 隔离） | FR-007、FR-009、ED-005、ED-007 |
| `shareholder_holding` 表（stock_id + end_date + holder_kind + holder_name） | FR-007、FR-008、FR-010、ED-010 |
| `shareholder_count` 表（stock_id + end_date） | FR-007、FR-008、FR-010、ED-010 |
| 水位 = `max(ann_date)`、按公告日增量窗口 | FR-002、FR-018、ED-010 |
| `has_more/offset` 分页与完整性门禁 | FR-006、ED-003、ED-008 |
| 同键替换（`ReplacingMergeTree(updated_at)`）修订语义 | FR-010、ED-010、SC-003 |
| 单 block 发布 + 失败不破坏已有数据 | FR-009、FR-013、NFR-003 |
| 审计三表复用（`data_kind=TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/`HOLDER_COUNT`，每接口独立 run） | FR-011、FR-012、NFR-005、SC-009 |
| 按月分区、无 TTL | NFR-009 |
| 无新 MySQL 表；审计表一项结构性变更（迁移 006，data_kind 加宽）已按宪章 VI 登记（§2.3） | 宪章 VI、宪章 II |
