# 实施计划：A股行情数据交易日同步

**分支**：`005-a-share-trend-data` | **日期**：2026-08-01 |
**规格**：[spec.md](spec.md)

**输入**：来自 `specs/005-a-share-trend-data/spec.md` 的功能规格

## 摘要

实现 A 股行情数据每日同步链路，由 Prefect 在 `Asia/Shanghai` 每个交易日按用户指定时点
运行四个接口：复权因子 9:00（开盘前完成）、日线 17:00、基本面指标 17:45、
周/月线在日线同步完成之后 18:30。四个接口均复用项目已同步的交易日历（CN-S）
判断交易日，非交易日不执行。首期真实来源固定为 Tushare `daily` / `adj_factor` /
`daily_basic` / `stk_week_month_adj` 四个接口，均按交易日（或周期）提取全市场数据，
不按股票循环；单次返回上限 6,000 行，全市场单日约 5,400 行，单次请求即可容纳，
仍保留提取完整性门禁与循环能力。上线初始化时四个接口均从 2024-01-01 起回补至当前增量，
按交易日逐日展开、独立幂等。

数据持久化采用 ClickHouse + MySQL 分层：五张 ClickHouse 业务表（日线行情、复权因子、
基本面指标、周K线、月K线——周线与月线虽来自同一接口但使用独立数据模型）以
“目标交易日（或周期）+ 稳定股票身份”为排序键，采用 `ReplacingMergeTree` 实现
同键幂等替换，按月分区承载 2024-01-01 起约千万级历史行；三张 MySQL 共享同步审计表
（同步运行、执行尝试、数据质量问题）以 `data_kind` 区分五类数据，复用金股验证过的
权威运行、不可变尝试、固定租约与质量问题模式。发布语义按 ClickHouse 能力调整：
单次同步的全部候选以一次批量 INSERT（block 级原子）写入，成功后在同一 MySQL 事务
写 attempt/run 成功终态；任一失败通过“同键替换 + 原运行重试”幂等收敛，
失败批次对查询始终不可见半批结果。供应商字段通过四个独立 Provider 契约与
Tushare Adapter 转换为项目规范语义，Service/Repository/Flow 不接触端点、
专有字段或错误码；内存替身与契约测试保证供应商可替换。

## 技术上下文

**语言/版本**：Python 3.12

**主要依赖**：Prefect 3.8+、HTTPX 0.28+、SQLAlchemy 2.0+、Alembic 1.18+、
PyMySQL 1.2+、Pydantic Settings 2.14+；复用现有 `TushareClient`、Provider Registry、
交易日历 Repository、`JsonlLogStore` 和数据库会话组件，不新增 Tushare SDK 或运行依赖

**存储**：ClickHouse 保存日线行情、复权因子、基本面指标、周K线、月K线五张
分析型业务表（`ReplacingMergeTree`、按月分区、按交易日查询排序键）；
MySQL 8.4 保存按 `data_kind` 共享的同步运行、执行尝试和质量问题审计三表
（幂等与状态机权威存储）；应用 Redis 不参与本功能

**测试**：pytest、HTTPX `MockTransport`、内存 Provider、SQLite 快速测试、
MySQL 并发/排序规则/事务集成测试、Prefect Flow 测试；质量命令为
`uv run ruff check .`、`uv run mypy src`、`uv run pytest`、
`uv run pytest -m mysql`、`uv run alembic upgrade head`

**目标平台**：Windows/WSL2 Ubuntu；应用和 Prefect Process Worker 在 WSL2 本机运行，
MySQL 与 Prefect Server 由现有 Docker Compose 提供

**项目类型**：Python 后台同步工作流与应用内部行情查询服务；
不新增公共 HTTP API 或用户界面

**性能目标**：正常来源条件下，日线（17:00 启动）、基本面（17:45 启动）和周/月线
（18:30 启动）同步当日形成终态，复权因子（9:00 启动）在开盘前形成终态；
单日全市场约 5,400 行、单次请求容纳，同步分钟级完成；
2024-01-01 起的回补（约 610 个交易日）在来源限流约束内逐日完成，可中断恢复

**约束**：

- 计划 Cron 固定为复权因子 `0 9 * * 1-5`、日线 `0 17 * * 1-5`、基本面 `45 17 * * 1-5`、
  周/月线 `30 18 * * 1-5`，时区 `Asia/Shanghai`。
- 每个 Flow 启动后必须查询项目交易日历（CN-S）判断目标时点是否为交易日；
  非交易日直接记录并成功结束，不调用任何来源接口；法定节假日自然跳过。
- 计划执行从 Prefect runtime `scheduled_start_time` 获取原计划时点；
  失败重试引用原 `run_id`，目标交易日不变，禁止用实际启动时间推导目标交易日。
- 四个接口各自独立调度、独立运行、独立恢复；任一接口失败不得阻塞或回滚其他接口。
- 唯一真实外部端点是 Tushare `daily`、`adj_factor`、`daily_basic`、`stk_week_month_adj`
  四个接口；`daily`、`adj_factor`、`daily_basic` 按 `trade_date` 提取全市场当日数据，
  `stk_week_month_adj` 按 `freq`（`week`/`month`）与周期最后交易日提取，
  均不得按股票逐只循环调用。
- 单次返回上限 6,000 行；全市场单日行数低于上限时单次请求即可，但 Adapter 必须
  保留提取完整性校验；任何返回达到上限且无法证明完整的批次不得标记成功。
- 来源限流（基础积分每分钟 500 次）与积分门槛（最低 2,000 积分）导致的拒绝
  映射为可识别错误类别；Adapter 仅对瞬态故障在初次调用后重试最多 3 次，
  Flow `retries=0`，防止重试层数相乘。
- 交易日判断必须复用现有交易日历数据，不得在本功能内创建第二套日历逻辑或新增接口。
- 日线保存未复权开/高/低/收、昨收、涨跌额、涨跌幅、成交量、成交额；停牌股票
  当日无记录属正常业务结果，与全市场空响应区分。复权因子保存复权因子值；
  基本面指标保存估值、换手、市值、股本、涨跌停状态等规范字段，亏损公司
  PE/PB 等空字段正常保存；周/月线保存未复权开/高/低/收价及成交量、
  成交额、涨跌额、涨跌幅与计算截至日期（部署账户实测该接口无 qfq/hfq 复权价格）。
- 业务唯一键：日线、复权因子、基本面指标为 `(trade_date, stock_id)`；
  周K线、月K线各自独立建模，唯一键均为 `(trade_date, stock_id)`，
  其中 `trade_date` 为来源返回的该周期最后交易日（每周五或月末最后一个交易日）；
  周线与月线不得共用同一模型或表。
- 完全重复可去重并计数；同一业务键字段冲突、交易日错配和无效核心字段整批失败；
  单条未知股票身份计入 `invalid_count`、保存 issue 后跳过，不阻止同交易日
  其他有效数据；有效集合为空则整日失败。
- 可信批次只新增、更新和确认本批出现的记录；永不扫描删除、失效或改写缺席记录；
  ClickHouse 以 `ReplacingMergeTree` 同键替换实现幂等更新，不做行级 UPDATE/DELETE。
- 回补从 2024-01-01 起按交易日逐日展开，四个接口独立回补；已成功日期跳过，
  失败或中断日期可安全重试；回补批次以 `backfill_batch_id + data_kind + target_trade_date`
  确定幂等身份。
- 运行 `run_key` 只由 `data_kind`、运行类型、计划 slug、原计划 UTC 时点
  （或回补批次键）和目标交易日生成；Provider、配置、范围指纹和实际启动时间
  只用于审计，不得改变业务运行身份；MySQL 唯一约束是幂等最终保障。
- 一个权威运行可以有多个不可变执行尝试；`SUCCEEDED` 运行不可重开，
  失败重试复用原运行并新增 attempt；attempt 认领时由数据库设置固定租约
  （大于 Provider 截止时间），过期判断和 `ABANDONED → Retry` 原子转换
  均使用数据库 UTC 时钟。
- 发布语义：单次同步的全部候选在内存校验完成后以一次 ClickHouse 批量 INSERT
  （block 级原子）写入对应业务表；INSERT 成功后在 MySQL 事务内写 attempt/run
  成功终态与计数。任一步失败时重试复用原运行，ClickHouse 同键替换保证与
  成功执行等幂等价；查询任意时刻只能看到完整批次或上一状态，看不到半批结果。
- ClickHouse 不提供事务回滚：失败批次可能已写入但未确认，验收语义以
  “同键替换后的最终行集与成功执行一致”为准，不以“零写入”为准。
- 五类数据各自持有 `data_kind` 隔离的规范模型；共享审计三表以 `data_kind`
  区分运行、尝试和问题归属，不允许跨数据类复用同一 run。
- 三张 MySQL 审计新表不申请宪章 VI 例外：统一以 `id BIGINT NOT NULL AUTO_INCREMENT`
  为物理主键；各 UUID 业务标识保留并建立 `UNIQUE`，所有表包含数据库维护的
  `created_at/updated_at`，且迁移定义中文表注释与每列非空中文注释。
- 五张 ClickHouse 业务表不适用宪章 VI 的 MySQL 治理（ClickHouse 引擎无自增主键、
  行级事务与 MySQL 式 UNIQUE），属宪章允许的“外部引擎承载业务数据”情形，
  在 `data-model.md` 逐表记录引擎、排序键、分区与幂等语义。
- Token、数据库连接串、完整请求/响应、供应商原始消息和原始行不得进入日志、
  错误摘要或业务表；issue 只保存哈希与白名单脱敏摘要。
- 查询仅供项目内部已授权调用方使用；调用入口承担认证、授权和数据访问控制。

**规模/范围**：第一版维护中国 A 股五类行情数据（日线、复权因子、基本面指标、
周K线、月K线），单日全市场约 5,400 行/类；五个数据类每个交易日各一个计划周期，
并支持 2024-01-01 起的交易日区间初始化回补（合计约千万行，ClickHouse 按月分区）。
明确排除复权价格计算、选股分析、绩效计算、历史回测、公共 API、管理页面、
应用 Redis 缓存和实时推送。

## 宪章检查

### 研究前门禁

- **规格与追溯：通过**。四个接口的调度时点对应 FR-002 至 FR-005；交易日判断与
  停牌/空响应区分对应 FR-001/015；业务唯一键、重复与冲突处理对应 FR-009 至 FR-011；
  提取完整性门禁对应 FR-017 与 ED-003；run/attempt/issue 审计对应 FR-012/013；
  回补与幂等对应 FR-020；Provider 隔离对应 ED-001 至 ED-008。
- **架构与数据边界：通过**。ClickHouse 拥有五张分析型行情业务表（日线、复权因子、
  基本面、周线、月线），MySQL 拥有按 `data_kind` 共享的事务型运行审计；
  交易日历和股票主数据继续由既有领域拥有，本功能只读取；Prefect 只编排，
  候选只在进程内存；应用 Redis、FastAPI 和前端均不适用。
  行情数据进 ClickHouse 符合宪章 II“分析型数据由 ClickHouse 承担”的分配，
  且回补后约千万行、按交易日追加的规模更适合列存；一致性要求
  （幂等、block 原子、同键替换）在 data-model.md §12 说明。
- **第三方数据源可替换性：通过**。计划定义四个独立 Provider 契约（日线、复权因子、
  基本面、周/月线）、规范 DTO、统一异常、四个 Tushare Adapter、显式 Registry、
  内存替身、契约测试和影子迁移；Service/Repository 不接触端点名、Tushare 字段
  或专有错误码。
- **测试与质量门禁：通过**。契约、单元、SQLite、真实 MySQL、Flow 和端到端层级覆盖
  四接口范围审计、触顶、身份、并发认领、原子回滚、无删除、非交易日跳过和重试边界，
  并明确 Ruff/mypy/pytest/Alembic。
- **安全与最小暴露：通过**。复用 `SecretStr` 延迟取 Token；只请求四接口必需字段；
  不保存原始 payload；不新增网络入口；日志和问题表采用字段白名单与哈希。
- **可观测与运维：通过**。按 `data_kind` 区分的权威 run、不可变 attempt、issue、
  Prefect 和 JSONL 形成可关联诊断链；quickstart 覆盖部署、回补、失败重试、
  五分钟排障和及时性统计。
- **MySQL 表结构：通过**。三张项目自有 MySQL 审计新表均采用
  `id BIGINT AUTO_INCREMENT` 物理主键，UUID 作为带 `UNIQUE` 的业务标识；
  每表包含数据库维护的 `created_at/updated_at`，`data-model.md` 逐表给出
  中文表注释且字段表“说明”列作为迁移 `COMMENT` 文本；无宪章 VI 例外。
  五张 ClickHouse 业务表不适用 MySQL 治理，属“外部引擎完全管理且不承载
  事务一致性语义”的豁免情形，已在 `data-model.md` 记录理由与幂等语义。
- **简洁性：通过**。复用现有包、Client、Registry、交易日历、日志、MySQL、Prefect
  和 Compose 已有 ClickHouse；共享审计三表加五业务表是“每类数据可独立同步”
  与“每次尝试可追踪”同时成立的最小结构，避免为五类数据重复五套运行审计表。

### 设计后复核

- **规格与追溯：通过**。`data-model.md`、四份契约和 quickstart 覆盖全部功能需求、
  外部依赖和成功标准；不含范围外字段或行为。
- **架构与数据边界：通过**。交易日历与股票主数据由既有领域拥有；行情领域只引用
  `stock_id` 与交易日历并保存规范字段；五类数据 `data_kind` 隔离；
  发布语义为“ClickHouse 单 block 原子写入 + MySQL 审计终态”，
  两写之间无事务，靠同键替换与运行重试收敛，最终行集与成功执行等幂等价。
- **第三方数据源可替换性：通过**。四个 Provider 契约只表达交易日（或周期）、
  规范记录与覆盖证据；Tushare 契约独占接口名、字段、触顶与错误映射；
  替代 Provider 通过相同 golden cases 后仅改配置。
- **测试与质量门禁：通过**。设计明确 Memory/Tushare 一致性契约、单次上限内完整提取、
  MySQL 首次并发认领、事务回滚与中文元数据、ClickHouse 同键替换幂等、
  单 block 原子可见、全市场 5,400 行容量、610 交易日回补代表性日期集、30 次重复、
  非交易日跳过、亏损空字段、停牌无记录与四接口独立失败恢复。
- **安全与最小暴露：通过**。五张 ClickHouse 业务表无 Token、`ts_code` 或原始载荷；
  issue 只保存哈希和安全摘要；内部查询不暴露 Provider 字段。
- **可观测与运维：通过**。run 保持单一权威状态，attempt 保存每次执行与重试计数和终态，
  issue 保存有限的脱敏问题样本；日志关联 flow/run/attempt 并记录窗口及时性。
- **MySQL 表结构：通过**。`data-model.md` 的三张 MySQL 审计表均使用统一物理主键和
  数据库维护时间，UUID 业务标识、业务唯一约束、中文表/字段注释、FK 目标及
  实际 schema 验证均已定义；ORM、Alembic 与 `SHOW CREATE TABLE` 必须三方一致。
  五张 ClickHouse 业务表记录引擎、排序键、分区与幂等语义，属外部引擎豁免。
- **简洁性：通过**。不新增框架、服务、队列、缓存、快照或公共接口；共享审计三表
  由五个数据类复用（`data_kind` 参数化）；五张业务表分离是五类数据不同字段集、
  周月线独立建模（用户明确要求）与 FR-009 业务唯一键同时成立所必需。
  行情数据进 ClickHouse 是宪章 II 对分析型数据的既定分配，且回补千万行规模
  与按交易日追加的写入模式不适合 MySQL 行存储。

## 项目结构

### 文档（本功能）

```text
specs/005-a-share-trend-data/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── daily-quote-provider.md
│   ├── adj-factor-provider.md
│   ├── daily-basic-provider.md
│   ├── weekly-monthly-kline-provider.md
│   ├── market-data-service.md
│   ├── prefect-flow.md
│   └── tushare-market-data.md
└── tasks.md
```

### 源代码（仓库根目录）

```text
prefect.yaml

migrations/
└── versions/
    ├── 004_create_market_data_audit_tables.py   # MySQL 审计三表
    └── 005_create_market_data_clickhouse.py     # ClickHouse 五张业务表

src/
└── lucking/
    ├── config.py
    ├── logging.py
    ├── clickhouse.py                             # ClickHouse 连接与会话组件
    ├── ports/
    │   ├── daily_quote_provider.py
    │   ├── adj_factor_provider.py
    │   ├── daily_basic_provider.py
    │   └── weekly_monthly_kline_provider.py
    ├── integrations/
    │   ├── registry.py
    │   └── tushare/
    │       ├── client.py
    │       ├── daily_quote_provider.py
    │       ├── adj_factor_provider.py
    │       ├── daily_basic_provider.py
    │       └── weekly_monthly_kline_provider.py
    ├── models/
    │   └── market_data.py                       # 五类规范模型 + MySQL 审计模型
    ├── repositories/
    │   ├── market_data.py                        # MySQL 审计 Repository
    │   └── market_data_clickhouse.py            # ClickHouse 批量写入与查询
    ├── services/
    │   └── market_data.py
    └── flows/
        └── market_data.py

tests/
├── contract/
│   ├── test_daily_quote_provider.py
│   ├── test_adj_factor_provider.py
│   ├── test_daily_basic_provider.py
│   ├── test_weekly_monthly_kline_provider.py
│   └── test_tushare_market_data.py
├── integration/
│   ├── test_market_data_repository.py
│   ├── test_market_data_mysql.py
│   ├── test_market_data_flow.py
│   └── test_market_data_capacity.py
└── unit/
    ├── test_market_data_config.py
    ├── test_market_data_identity.py
    ├── test_market_data_service.py
    └── test_market_data_logging.py
```

**结构决策**：继续使用 `src/lucking` 单体包并建立独立行情数据垂直切片。
四个 Port 各自持有供应商无关契约；四个 Tushare Adapter 独占端点和专有映射
（周/月线同一 Adapter 按 `freq` 区分请求，但映射为两个独立规范模型）；
共享 Service 核心负责运行认领、五类数据解析校验与发布（ClickHouse 单 block
写入 + MySQL 审计终态）；MySQL Repository 负责交易日历与股票身份读取、
并发认领与审计状态机，ClickHouse Repository 负责批量写入与查询；
一个参数化 Flow 由四个 Deployment 以不同 Cron 和 `data_kind` 复用，
回补 Flow 仅负责校验区间、逐日展开和汇总，不复制领域逻辑。
不把行情逻辑塞入 `stock_list` 或 `broker_recommendation`，
因为三者完整性规则不同（全量基线、月度追加、按交易日增量）。

## 实施阶段

### 阶段 1：数据库、配置与并发认领

1. 增加四个 Provider、时区、日志、截止时间、固定运行租约、页面上限、最大页数
   （按数据类）配置，共享现有 Tushare Token 与 URL；增加 ClickHouse 连接配置。
2. 创建 MySQL 按 `data_kind` 共享的三张审计表；每表使用自增 BIGINT 物理主键、
   唯一 UUID 业务标识、数据库维护时间和完整中文表/字段注释；迁移同时验证
   空库升级和 `003 → 004`。
3. 创建 ClickHouse 五张业务表：`ReplacingMergeTree(updated_at)`、ORDER BY
   业务键、按月分区、中文注释（ClickHouse 引擎列注释）、`004 → 005` 迁移；
   建立独立 ClickHouse 会话组件（连接、批量插入、超时与错误分类）。
4. 修正 `migrations/env.py` 的模型加载，使 Alembic metadata 可发现新增全部模型。
5. 先实现真实 MySQL 的原子 claim、唯一冲突重读、attempt 追加、成功不可重开和
   过期运行保护，固定租约 `lease_expires_at` 由数据库 UTC 时钟生成和比较；
   并验证 ORM metadata、迁移 DDL 和实际 schema 的主键、唯一键、时间默认值、
   `ON UPDATE` 与注释一致。

### 阶段 2：Provider 契约与 Tushare Adapter

1. 定义四个 Provider 契约、规范 DTO、覆盖证据和本域统一异常。
2. 实现四个 Memory Provider 一致性套件，再实现只调用对应接口的四个 Tushare Adapter。
3. 严格审计 `trade_date`（或 `freq` + 周期最后交易日）、字段白名单、
   交易日一致、代码后缀映射、空响应、单次上限触顶、重复、未前进和最大页数。
4. 复用通用 Client 错误分类，并补充积分门槛权限码映射；Adapter 封装最多 3 次瞬态重试。
5. Adapter 可构造后再注册到独立 Provider Registry，禁止 Service 依赖 Tushare 模块。

### 阶段 3：领域校验、发布与内部查询

1. 实现计划目标交易日推导、显式回补目标区间校验、含 `data_kind` 的 run key、
   非交易日跳过、必填字段、交易日一致、股票映射交叉校验、跨批重复和冲突判断。
2. 实现发布流程：全批内存校验后以一次 ClickHouse 批量 INSERT（单 block）写入
   对应业务表；成功后在同一 MySQL 事务写 attempt/run 成功终态与全部计数；
   ClickHouse 失败时保留 MySQL 运行非终态并允许幂等重试，同键替换保证
   最终行集与成功执行一致。
3. 失败路径独立保存 attempt 计数、run 终态和有限的安全 issue 样本；
   ClickHouse 侧不删除已写入数据，靠运行重试与同键替换收敛。
4. 提供按交易日（或周期）和股票筛选的内部 Repository/Service 查询
   （ClickHouse 读路径），不新增 HTTP API。

### 阶段 4：工作流、调度与运维

1. 新增 `market-data-sync` 参数化 Flow 与四个 Deployment：
   `adj-factor-sync`（Cron `0 9 * * 1-5`）、`daily-quote-sync`（Cron `0 17 * * 1-5`）、
   `daily-basic-sync`（Cron `45 17 * * 1-5`）、`kline-sync`（Cron `30 18 * * 1-5`，
   周线与月线以 `freq` 参数区分、各自独立运行），
   时区 `Asia/Shanghai`，并发 1，冲突策略 `ENQUEUE`。
2. 计划 Flow 从 Prefect runtime 读取计划时点，查询交易日历判断是否交易日；
   回补 Flow 校验交易日区间和 `backfill_batch_id`，逐日解析运行状态：
   成功跳过、失败/过期运行转换为引用原 `run_id` 的 Retry、未开始日期创建回补运行。
3. 增加独立 JSONL 文件及字段白名单，记录 `data_kind`、run/attempt、目标交易日、
   批次键、提取计数、retry 和窗口及时性。
4. 更新 README 的配置、部署、回补、失败重试、完整性门禁、五分钟排障和安全停止说明。

### 阶段 5：验证与上线门禁

1. 完成四接口端点/字段范围、Provider 替换、交易日、身份、重复、冲突和错误映射契约测试。
2. 完成 MySQL 首次并发认领、BIGINT 自增物理主键、UUID 业务唯一键、
   数据库维护 `created_at/updated_at`、中文表/字段注释、事务回滚和迁移测试；
   完成 ClickHouse 建表、分区、同键替换幂等、单 block 原子可见和迁移测试。
3. 执行核心场景：单日全市场（约 5,400 行）五类数据同步，验证新增/更新且缺席不删除；
   停牌股票无记录、亏损公司空字段、非交易日跳过；失败批次在查询中不可见半批结果，
   重试后最终行集与成功执行一致。
4. 用 Memory Provider 和 fixture 完成全市场容量与回补验证：连续 30 次重复同步、
   回补代表性交易日集合（含失败恢复与重复提交）、固定租约有效/过期边界、
   瞬态 3 次重试和确定性错误零重试；验证周线与月线写入互不串扰、
   同一周期重复同步只保留一行。
5. 上线前用部署账户或供应商沙箱验证四个接口的权限、积分门槛、频率限制，
   以及 `daily`/`adj_factor`/`daily_basic` 按 `trade_date` 全市场返回与
   `stk_week_month_adj` 按 `freq` + 周期最后交易日返回的行为；
   验证失败时不得启用对应数据类，必须切换兼容 Provider 或阻断上线。
6. 生产可用门槛必须同时完成 US1、US2、US3 和 `tasks.md` 全部分期，
   不得因增量采集已可运行而省略回补、失败保护或真实来源门禁。

## 复杂度跟踪

无宪章违反项，不需要复杂度例外。
