# 实施计划：每日股票列表同步

**分支**：`003-stock-list-sync` | **日期**：2026-07-26 |
**规格**：[spec.md](spec.md)

**输入**：来自 `specs/003-stock-list-sync/spec.md` 的功能规格

## 摘要

实现一条独立的股票列表同步链路，由 Prefect 在 `Asia/Shanghai` 每日 09:00 运行。
首期真实来源固定为 Tushare `stock_basic`，按 `SSE/SZSE/BSE × L/D/P/G`
执行 12 个明确分区，只请求股票身份、代码、名称、交易所、币种、状态和上市/退市日期
所需的 8 个字段。Adapter 将专有字段转换成项目 `StockListProvider` 规范模型；
Service 在内存完成全批身份、字段、分区、上限、重复、冲突和历史基线校验。

只有完整且可信的候选列表才在一个 MySQL 事务中 upsert 当前股票、供应商映射并把同步结果
标记为 `SUCCEEDED`。任一分区失败、恰好触及 6,000 行上限、聚合为空、出现无效/冲突记录
或相对上一成功结果缺少任一既有 Provider 身份时，整批拒绝，股票当前值不变，并单独记录
`FAILED` 结果和安全质量问题。
不保存每日快照或属性历史，不调用任何其他 Tushare 接口，不使用 ClickHouse、业务 Redis、
FastAPI 或前端。

## 技术上下文

**语言/版本**：Python 3.12

**主要依赖**：Prefect 3.8+、HTTPX 0.28+、SQLAlchemy 2.0+、Alembic 1.18+、
PyMySQL 1.2+、Pydantic Settings 2.14+；复用现有通用 `TushareClient`，
不新增 Tushare SDK 或其他运行依赖

**存储**：MySQL 8.4 保存股票当前值、供应商映射、同步结果和质量问题；
ClickHouse 与应用 Redis 不参与本功能数据存储

**测试**：pytest、HTTPX `MockTransport`、内存 Provider、SQLite 快速测试、
MySQL 集成测试、Prefect Flow 测试；质量命令为 `uv run ruff check .`、
`uv run mypy src`、`uv run pytest`、`uv run alembic upgrade head`

**目标平台**：Windows/WSL2 Ubuntu；应用和 Prefect Process Worker 在 WSL2 本机运行，
MySQL 与 Prefect Server 由现有 Docker Compose 提供

**项目类型**：Python 后台同步工作流与应用内列表查询服务；不新增公共 HTTP API 或用户界面

**性能目标**：正常来源条件下从计划时点到终态不超过 30 分钟；单批支持至少 10,000 条
候选记录；在包含 10,000 条当前记录的验收数据集中完成一次预热后，连续执行 100 次覆盖
无筛选、代码、venue、名称和状态的代表性查询，至少 95 次在 1 秒内返回

**约束**：

- 唯一允许的真实外部端点是 Tushare `stock_basic`；不得调用 `trade_cal`、行情、成交、
  指标、财务、公司、指数、基金或任何其他数据端点
- 请求字段严格限定为 `ts_code,symbol,name,exchange,curr_type,list_status,list_date,
  delist_date`，不得请求或保存 `area/industry/fullname/enname/cnspell/market/is_hs/
  act_name/act_ent_type`
- 首期范围固定为 `CN-S`，且固定完整覆盖 `SSE/SZSE/BSE`，不得通过配置排除任一交易所；
  Adapter 对 `SSE/SZSE/BSE × L/D/P/G` 的 12 个分区逐一调用同一端点，不使用未文档化分页参数
- 单分区成功空集允许存在；12 个分区聚合为空必须失败
- 任一分区返回恰好 6,000 行时无法证明未截断，整批失败；所有分区必须成功且字段集合精确匹配
- `L/D/P/G` 只存在于 Adapter；领域状态为 `ACTIVE/DELISTED/SUSPENDED/PENDING`
- `SSE/SZSE/BSE` 映射为项目 venue `XSHG/XSHE/XBSE`；Tushare `ts_code`
  仅用于 Provider 映射和后缀交叉校验
- `curr_type` 必须非空并显式映射；首期只接受经契约验证的 `CNY → CNY`，不得默认填充
- `list_date/delist_date` 严格按 `YYYYMMDD` 解析；日期同时存在时退市日期不得早于上市日期；
  状态所需日期缺失时整批失败，不静默补值
- 完全相同重复行可去重并计数；身份或字段冲突、未知枚举、非法字段整批失败
- 与上一成功列表相比，任一已有 Provider 身份完全消失时视为完整性异常，采用零容忍规则；
  不设置下降比例，不删除、不推断退市，也不发布本批
- 股票以项目生成 UUID 标识；Provider 映射优先，规范 venue + 证券代码仅用于无冲突匹配；
  代码变更无明确谱系时不得按名称猜测合并
- 候选列表不超过 10,000 条时全部在内存校验；不引入 staging、快照或历史版本表
- 每次计划周期通过 MySQL 唯一 `run_key` 去重；Prefect 并发限制只作为资源保护
- 网络、HTTP 429、短时频率限制和 5xx 可按 30/120/300 秒有界重试；
  认证、权限、额度、配置、载荷、完整性、冲突和数据库错误不自动重试
- 单分区重试由 Adapter 封装，并受整次运行 25 分钟外部获取截止时间约束；
  Flow 不再叠加整批外部重试，避免重试次数相乘
- 股票 upsert、Provider 映射和同步成功状态在一个 MySQL 事务中原子提交；
  失败信息和质量问题在当前值事务回滚后单独记录
- 事件瞬间保存为 UTC；计划业务日期由 `scheduled_at` 转换到 `Asia/Shanghai` 得出
- Token、数据库连接串、完整请求/响应和原始供应商行不得进入日志、错误摘要或业务表
- 当前列表查询只提供给项目内部已完成授权的调用方；本功能不新增公共网络入口或授权机制，
  调用入口必须在调用 Service 前完成认证、授权和访问控制

**规模/范围**：第一版维护 `CN-S` 最新有效股票列表，候选规模不超过 10,000 条；
每日一个计划周期。明确排除行情、成交、财务、指标、公司详情、其他证券品种、完整列表快照、
属性历史、实时推送、管理页面和公共 API。

## 宪章检查

### 研究前门禁

- **规格与追溯：通过**。唯一外部端点和字段白名单对应 FR-002、FR-003、FR-014、
  ED-001、ED-002；完整性和原子发布对应 FR-005 至 FR-012；每日调度对应 FR-001；
  应用内查询及可重复性能验收对应 FR-013、NFR-010、SC-009。
- **架构与数据边界：通过**。MySQL 拥有低量、强一致的股票当前值、Provider 映射、
  同步状态和问题；Prefect 只负责编排；没有分析型、临时或公共 API 数据，
  因而 ClickHouse、应用 Redis 和 FastAPI 均不适用。
- **第三方数据源可替换性：通过**。`StockListProvider`、规范 DTO、独立 Tushare Adapter、
  显式 Registry、统一异常、内存替身和 Provider 一致性契约均已定义；
  业务层不接触端点名、Tushare 字段或状态代码。
- **测试与质量门禁：通过**。计划覆盖字段白名单与唯一端点审计、12 分区、上限、
  空分区、身份、重复、冲突、事务回滚、唯一计划周期、调度和日志安全；
  pytest、Ruff、mypy、Alembic 与真实 MySQL 门禁均明确。
- **安全与最小暴露：通过**。Token 使用现有 `SecretStr` 延迟解密；只请求 8 个必要字段；
  不保存原始载荷；无新增监听端口或授权机制；内部调用入口承担认证授权；日志采用白名单与脱敏。
- **可观测与运维：通过**。MySQL 同步结果记录终态和计数，Prefect 提供运行状态，
  独立 JSONL 日志关联 run/flow 标识并记录 30 分钟及时性；quickstart 覆盖排障和补跑。
- **简洁性：通过**。复用现有 Python、HTTPX、Prefect、SQLAlchemy/Alembic 和 MySQL；
  不引入新依赖、服务、SCD、快照、staging、公共 API 或前端。

### 设计后复核

- **规格与追溯：通过**。`data-model.md` 仅包含 `stock_current`、
  `stock_provider_mapping`、`stock_list_sync_run` 和 `stock_list_sync_issue`；
  没有任何行情或附加字段。四份契约和 quickstart 覆盖全部验收场景。
- **架构与数据边界：通过**。候选数据只存在于进程内存，可信发布在单个 MySQL 事务完成；
  当前值长期保留，运行与日志生命周期独立；没有跨存储一致性问题。
- **第三方数据源可替换性：通过**。Provider 契约只表达股票列表、覆盖证明和统一错误；
  Tushare 契约独占 12 分区、字段及映射；迁移通过影子对账和 Provider 映射完成，
  不修改 Service、表的核心字段或消费者契约。
- **测试与质量门禁：通过**。契约给出精确请求、规范输出、状态转换、事务后置条件和失败语义；
  Memory Provider 与 Tushare Adapter 共享一致性测试，真实数据库验证唯一键及回滚；
  10,000 条当前记录上的 100 次代表性查询验证 NFR-010 与 SC-009。
- **安全与最小暴露：通过**。数据模型没有原始 payload 或不必要字段；
  日志与异常只保留白名单安全摘要；Tushare Token 不越过 Client/Adapter。
- **可观测与运维：通过**。同步表、质量问题、结构化日志和 Prefect Flow Run ID
  形成完整诊断链；人工补跑复用同一 `run_key`，不会创建第二权威结果。
- **简洁性：通过**。当前值四表、一个 Provider、一个 Service、一个 Flow 即可满足需求；
  无需复杂度例外。

## 项目结构

### 文档（本功能）

```text
specs/003-stock-list-sync/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── prefect-flow.md
│   ├── stock-list-provider.md
│   ├── stock-list-service.md
│   └── tushare-stock-basic.md
└── tasks.md
```

### 源代码（仓库根目录）

```text
prefect.yaml

migrations/
└── versions/
    └── <revision>_create_stock_list_tables.py

src/
└── lucking/
    ├── config.py
    ├── db.py
    ├── logging.py
    ├── ports/
    │   └── stock_list_provider.py
    ├── integrations/
    │   ├── registry.py
    │   └── tushare/
    │       ├── client.py
    │       └── stock_list_provider.py
    ├── models/
    │   └── stock_list.py
    ├── repositories/
    │   └── stock_list.py
    ├── services/
    │   └── stock_list.py
    └── flows/
        └── stock_list.py

tests/
├── contract/
│   ├── test_stock_list_provider.py
│   └── test_tushare_stock_basic.py
├── integration/
│   ├── test_stock_list_repository.py
│   └── test_stock_list_flow.py
└── unit/
    ├── test_stock_list_service.py
    ├── test_stock_list_identity.py
    └── test_stock_list_logging.py
```

**结构决策**：继续使用 `src/lucking` 单体包。`ports` 拥有供应商无关列表契约；
通用 `TushareClient` 仅处理 HTTP 信封，并增加默认关闭的“允许成功空表”选项；
端点 Adapter 独占 12 分区、字段白名单和映射；Service 负责跨行及历史基线校验；
Repository 在 MySQL 事务中发布当前值。现有交易日历领域类、表和 Flow 均不复用。

## 实施阶段

### 阶段 1：模式、配置与当前值存储

1. 增加股票列表 Provider、固定 `CN-S` 范围、时区、日志和完整性配置；不提供交易所排除配置。
2. 通过 Alembic 创建股票当前值、Provider 映射、同步结果和质量问题四张表。
3. 先实现 Repository 的唯一周期认领、原子 upsert、无删除及失败回滚测试。

### 阶段 2：Provider 契约与领域校验

1. 定义 `StockListProvider`、规范 DTO、覆盖证明和统一异常。
2. 兼容性扩展通用 Tushare Client，使指定调用可接受成功空表。
3. 实现只调用 `stock_basic` 的 Adapter、12 个分区、字段白名单和严格映射。
4. 在 Adapter 可构造后将 `tushare` 工厂注册到通用 Registry，避免 Registry 依赖未完成实现。
5. 实现身份解析、字段/日期、触顶、重复、冲突及上一成功基线零容忍校验。
6. 实现全批校验后单事务发布、失败记录和内部当前列表查询。

### 阶段 3：工作流、调度与可观测性

1. 新增 `stock-list-sync/default` Deployment 与每日 09:00 Schedule。
2. 实现计划与人工补跑参数、唯一 `run_key`、Adapter 有界重试和 25 分钟获取截止时间。
3. 泛化 JSONL 日志文件名、字段白名单和及时性阈值，保持交易日历行为兼容。
4. 更新 README 的配置、部署、补跑、排障和安全停止说明。

### 阶段 4：验证与范围审计

1. 完成 Memory Provider、Tushare `stock_basic` 和错误映射契约测试。
2. 验证唯一端点、精确字段、12 分区、合法空分区、6,000 行触顶和聚合空结果。
3. 验证新增/更新、完全重复、冲突、身份消失、数据库失败、重试和同周期重复触发。
4. 使用 10,000 条固定候选数据执行同步容量验证，并按预热后 100 次代表性查询验证 p95。
5. 模拟最近 30 次计划运行验证及时率统计，执行连续 30 次重复/补跑验证，并排除人工运行。
6. 按 quickstart 完成人工运行、当前列表检查、5 分钟排障演练、失败保护、替代 Provider 和范围审计。

## 复杂度跟踪

无宪章违反项，不需要复杂度例外。
