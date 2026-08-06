# 实施计划：股票技术面因子交易日同步（007-sync-stock-factors）

**分支**：`007-sync-stock-factors`（本次未配置分支创建钩子，沿用当前分支） | **日期**：2026-08-04 | **规格**：[spec.md](spec.md)

**输入**：来自 `/specs/007-sync-stock-factors/spec.md` 的功能规格

**说明**：本模板由 `/speckit-plan` 填写；该命令定义具体执行流程。

## 摘要

每个交易日北京时间 17:00，通过 Tushare `stk_factor_pro`（股票技术面因子，
专业版，doc_id=328）按交易日提取全部 A 股股票的技术面因子（基础行情、
估值、技术指标及其全部 `_bfq/_qfq/_hfq` 复权变体，Clarifications
2026-08-04 确认），存入 ClickHouse `stock_factor` 宽表；提供 2024-01-01
起的人工初始化回补；全程遵守来源每分钟 30 次的限流（共享 `RateLimiter`
进程级节流，最小间隔 2 秒）；审计复用 005 的
`market_data_sync_run/attempt/issue` 三表（新增 `data_kind=STOCK_FACTOR`）；
股票身份**复用 003** 的 `stock_current`/`stock_provider_mapping`
（`provider_mappings` 解析，不新建 MySQL 表、无 DDL 变更）。实现沿用
005/006 已验证的「Port 契约 + Tushare Adapter + Service 编排 + MySQL 审计
+ ClickHouse 发布 + Prefect 参数化 Flow」模式（详见 research.md 决策 1~7）。
与 006 指数的关键差异：字段**保留复权后缀**并区分可修订/稳定字段更新
语义（`_qfq/_hfq`/`adj_factor` 随后续除权除息按来源最新值更新）、
单次返回上限 10,000 行、流程名称使用**中文**（spec FR-019）。

## 技术上下文

**语言/版本**：Python 3.12（与仓库一致）

**主要依赖**：httpx（Tushare/ClickHouse HTTP）、prefect ≥ 3.8（Flow 与调度）、
SQLAlchemy/Alembic（MySQL 审计与身份读取）、pydantic-settings（配置）；
不新增 tushare SDK、clickhouse-driver 或任何新依赖（沿用 005/006 的
HTTP 直连模式）

**存储**：MySQL（身份读取复用 003 两表 + 审计复用 005 三表，
**无新建表、无结构性 DDL 变更**）；ClickHouse（`stock_factor` 新建宽表，
`ReplacingMergeTree(updated_at)`，`ORDER BY (trade_date, stock_id)`，
`PARTITION BY toYYYYMM(trade_date)`）

**测试**：pytest；契约测试（假 Provider/替身）、单元、集成（`-m mysql`）、
端到端；`uv run ruff check .`、`uv run mypy --strict src`（006 口径）、
`uv run pytest`

**目标平台**：WSL2 本地开发（应用进程），Docker Compose 承载 MySQL、
ClickHouse、Redis、Prefect Server（沿用 compose.yml，端口仅绑定 127.0.0.1）

**项目类型**：数据同步后台服务（Prefect 编排，无 UI 变更）

**性能目标**：增量同步（17:00 启动）当日形成终态（NFR-001）；回补约 630
个交易日，节流 ≥ 2 秒/请求，全程 ≤ 30 次/分钟，约 20~30 分钟完成
（research 待验证项 6）；单日请求返回行数预期远小于 10,000 上限
（research 待验证项 1）

**约束**：
- 计划 Cron 固定 `0 19 * * 1-5`，时区 Asia/Shanghai；目标交易日以
  `prefect.runtime.flow_run.scheduled_start_time` 为准，直接调用必须显式
  提供 `scheduled_at`。
- 每个 Flow 启动后必须查询项目交易日历（CN-S）；非交易日直接记录
  `SKIPPED_NOT_TRADING_DAY` 并成功结束。
- 来源限流每分钟 30 次：共享 `RateLimiter` 进程级节流（任意 60 秒窗口
  ≤ 30 次，最小间隔 2 秒，`monotonic`/`sleep` 可注入）；限流错误映射
  `PROVIDER_RATE_LIMITED`，Adapter 初次调用后重试最多 3 次（退避
  30/120/300 秒，受 deadline 约束）；Flow `retries=0`，防止重试层相乘。
- 运行 `run_key` 只由 `STOCK_FACTOR + 运行类型（SCHEDULED/BACKFILL）+
  schedule_slug 或 backfill_batch_id + 原计划 UTC 时点或目标交易日 +
  target_trade_date` 生成；MySQL 唯一约束是幂等最终保障。
- 单次同步的全部候选以一次 ClickHouse 批量 INSERT（block 级原子）写入，
  成功后在同一 MySQL 事务写 attempt/run 成功终态；`ReplacingMergeTree(updated_at)`
  同键替换实现幂等，并天然支持复权字段回溯更新。
- 单次返回上限 10,000 行（独立配置 `stock_factor_page_limit=10000`）；
  触顶且无经验证续取手段时判定不完整并失败，禁止猜测参数。
- 字段分级：`_qfq/_hfq` 后缀字段与 `adj_factor` 为可修订字段
  （重复同步按来源最新值更新，计 `updated_count`，不视为冲突）；
  其余为稳定字段（同键值变化即 `RECORD_CONFLICT` 整批失败）。
- 股票身份以 003 主数据为权威：`provider_mappings("tushare")` 解析
  ts_code → stock_id；未映射 → `invalid_count` + 脱敏 issue
  （`UNKNOWN_STOCK_IDENTITY`），跳过该条，不阻断整批。
- 宪章 VI：本功能不新建、不结构性修改任何 MySQL 业务表（身份复用 003、
  审计复用 005），逐表治理不适用；ClickHouse `stock_factor` 属宪章允许的
  “外部引擎承载业务数据”情形，在 data-model.md 记录引擎、排序键、分区与
  幂等语义。
- 流程名称使用简体中文且语义符合业务场景（FR-019）：“股票技术面因子
  交易日同步”“股票技术面因子历史回补”；内部 `schedule_slug` 保持 ASCII
  （如 `stock-factor-sync`）作为幂等键与审计标识。
- 供应商细节（字段名、错误码、限流档位）只存在于 Adapter；
  业务代码只依赖 Port 契约与规范模型。

**规模/范围**：全部 A 股（三所：上交所/深交所/北交所，约 5,400 只，
按交易日整体提取）；2024-01-01 起回补至当前增量；行情/估值/技术指标
及其全部复权变体字段/日/股票；因子值以 `Nullable(Decimal)` 保存缺失；
明确不在范围内：股票列表维护（003 承担）、因子重算、趋势分析/选股/回测、
公共 API/UI、跨交易日自动补同步（补同步经人工触发回补 Flow）、与 005
行情/基本面表的数据合并

## 宪章检查

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

> 所有项目文档必须使用简体中文（代码标识符、命令、协议字段及专有名词除外）。

### 研究前门禁

- **规格与追溯**：通过。spec.md 含 3 个按价值排序的用户故事（P1 增量同步、
  P2 初始化回补、P3 失败与质量识别）、19 条可测试功能需求、边界情况与
  SC-001~SC-010 可度量成功标准；Clarifications 记录 1 条已确认决策
  （保存全部字段含全部复权变体）；本计划全部决策可追溯至 spec 需求
  （research.md 决策 1~7 均标注对应需求编号）。
- **架构与数据边界**：通过。职责边界沿用 005/006 已验证划分：Adapter
  （供应商隔离）→ Service（领域编排）→ Repository（MySQL 审计/
  ClickHouse 发布）；数据所有权明确（身份/审计 = MySQL 复用，因子 =
  ClickHouse 分析型），生命周期与一致性在 data-model.md §1、§4 记录；
  无跨边界新增基础设施。
- **第三方数据源可替换性**：通过。`stock-factor-provider.md` 定义供应商
  无关 Port；`tushare-stock-factor.md` 定义唯一允许接口、字段白名单映射
  （含可修订/稳定分级）、错误映射与限流档位；契约测试要点含替代实现/
  测试替身验证（ED-006/ED-007）；业务代码不依赖供应商 SDK/传输模型/
  专有字段（ED-005 白名单严格校验）。
- **测试与质量门禁**：通过。四份契约均含“契约测试要点”；Service 契约
  定义假 Provider 全流程测试（含复权更新 vs 冲突用例）；预计划 pytest
  单元/契约/集成/端到端 + ruff + mypy 质量门禁，与 005/006 相同工具链。
- **安全与最小暴露**：通过。Token 为 SecretStr 延迟读取、不入日志
  （NFR-005）；日志白名单仅 run_key/状态/计数/脱敏摘要；issue 表只存
  哈希与白名单摘要；无新增网络暴露（沿用 compose.yml 回环绑定）；
  回补区间校验拒绝未来日期。
- **可观测与运维**：通过。复用 005 审计三表（run/attempt/issue 计数与
  问题类别全集，含 `UNKNOWN_STOCK_IDENTITY`）；JsonlLogStore 结构化日志
  与窗口及时性；quickstart.md §7 五分钟排障、§8 上线门禁；非交易日
  SKIPPED 不产生误告警。
- **MySQL 表结构**：通过（不适用）。本功能不新建、不结构性修改任何
  MySQL 业务表——身份表读取复用 003（`provider_mappings` 只读）、审计表
  复用 005（仅 `DataKind` 枚举新增取值，无列变更）；无 Alembic 迁移；
  宪章 VI 逐表治理对本功能无适用对象，复用表治理义务仍归属其创建功能。
- **简洁性**：通过。不新增框架/服务/依赖；唯一抽象为共享 `RateLimiter`
  的泛化重命名（research 决策 4：006 节流器实现已与指数解耦，重命名
  低风险且有测试兜底），与 006 相比反而**减少**了新抽象数量
  （身份复用 003，无需自举注册基础设施）。

### 设计后复核

- **规格与追溯**：通过。Phase 0/1 全部产物（research 7 决策、data-model
  5 节、契约 4 份、quickstart 8 节）逐项对应 spec FR/NFR/ED/SC；
  tasks 阶段将按用户故事分组并编号追溯。
- **架构与数据边界**：通过。设计未引入新边界；股票身份只读复用 003
  （data-model §2.1），无第二套身份事实来源；ClickHouse 单表宽表决策与
  可修订/稳定字段发布语义在 data-model §3/§4 记录。
- **第三方数据源可替换性**：通过。四份契约明确 Port/实现/测试替身三层；
  供应商字段白名单、字段分级、限流档位全部封装在 Adapter（tushare 契约
  §3/§4/§5）；换源不改业务代码（stock-factor-service.md §6）。
- **测试与质量门禁**：通过。设计后契约测试要点完整（Provider 白名单/
  节流/触顶/重试；Service 幂等/空响应区分/复权修订 vs 冲突；Flow 参数
  校验/中文名与 ASCII slug 双轨/日志白名单）；上线门禁 7 项实测项
  （quickstart §8）覆盖权限、积分、限流、字段全集与数据完备性。
- **安全与最小暴露**：通过。设计确认无新秘密入码、无新端口暴露；
  issue 脱敏与日志白名单在契约层落实。
- **可观测与运维**：通过。审计复用保证跨功能一致的排障体验；
  quickstart §7/§8 提供运行验证与上线实测步骤。
- **MySQL 表结构**：通过（不适用）。设计确认无新建/无结构性变更
  （data-model §2.3），无例外申请。
- **简洁性**：通过。复杂度跟踪无违反项；`RateLimiter` 泛化重命名必要性
  已在研究决策 4 论证，复杂度表中登记备选拒绝理由（见下）。

## 项目结构

### 文档（本功能）

```text
specs/007-sync-stock-factors/
├── plan.md              # 本文件 (/speckit-plan 输出)
├── research.md          # Phase 0 输出（决策 1~7 + 待验证项）
├── data-model.md        # Phase 1 输出（身份/审计复用 + ClickHouse stock_factor）
├── quickstart.md        # Phase 1 输出（端到端验证与排障）
├── contracts/           # Phase 1 输出（4 份契约）
│   ├── tushare-stock-factor.md
│   ├── stock-factor-provider.md
│   ├── stock-factor-service.md
│   └── prefect-flow.md
└── tasks.md             # Phase 2 输出 (/speckit-tasks 命令 - 本命令不创建)
```

### 源代码（仓库根目录）

```text
src/lucking/
├── config.py                      # + stock_factor_* 配置项（含 page_limit=10000、rate_limit_per_minute=30）
├── clickhouse.py                  # + stock_factor 表 DDL 与 migrate 注册
├── models/
│   ├── market_data.py             # + DataKind.STOCK_FACTOR 枚举值
│   └── stock_factor.py            # + 规范 DTO（StockFactorRequest/ProviderStockFactorRecord/
│                                  #   STOCK_FACTOR_FIELDS 白名单含可修订分级/…）
├── ports/
│   └── stock_factor_common.py     # + StockFactorProvider Protocol、RetrievalEvidence 复用
├── integrations/
│   ├── registry.py                # + register/build_tushare_stock_factor_provider
│   └── tushare/
│       ├── client.py              # （复用）TushareClient 信封
│       ├── rate_limiter.py        # ~ 006 的 index_rate_limiter.py 泛化重命名
│       │                           #   （RateLimiter，保留 IndexRateLimiter 兼容别名）
│       ├── index_rate_limiter.py  # （迁移）仅保留兼容 import
│       └── stock_factor_provider.py   # + Adapter（字段白名单、分级、节流、重试、错误映射）
├── repositories/
│   ├── stock_factor_clickhouse.py # + 批量发布与查询（stock_factor 表）
│   ├── stock_list.py              # （复用）provider_mappings 身份解析
│   └── market_data.py             # （复用）审计 Repository（data_kind=STOCK_FACTOR）
├── services/
│   └── stock_factor.py            # + StockFactorService（命令分派、校验、发布、终态）
└── flows/
    └── stock_factor.py            # + 股票技术面因子交易日同步 / 股票技术面因子历史回补 两 Flow

prefect.yaml                          # + 股票技术面因子交易日同步/股票技术面因子交易日同步 与
                                      #   股票技术面因子历史回补/股票技术面因子历史回补 Deployment（中文名）

tests/
├── unit/…（stock_factor 校验/分级/节流/身份解析）
├── contract/…（Provider 白名单/错误映射/Service 假 Provider 全流程/Flow 参数）
├── integration/…（MySQL 审计幂等 -m mysql；ClickHouse 发布 -m mysql；限流实测）
└── e2e/…（可选：真实账户冒烟，上线门禁）
```

**结构决策**：新建独立的 `stock_factor` 垂直切片（ports/integrations/
repositories/services/flows/models 各一文件），理由：股票因子模型（宽表
含复权变体）、字段分级更新语义与 10,000 行上限均与既有功能语义不同，
但复用 003 身份解析、005 审计三表、TushareClient 信封、交易日历、配置
前缀与 Deployment 模式；塞入既有 `market_data.py` 会造成职责混杂
（宪章 II）。与 006 的结构差异：**无 index_factor_identity 类身份仓储**
（身份只读复用 003）、**无新 Alembic 迁移**（data_kind 为纯枚举扩展）、
节流器为共享模块而非功能私有。

## 实施阶段

1. **阶段 1：模型与数据库**——`DataKind.STOCK_FACTOR` 枚举、`stock_factor`
   规范 DTO 与 `STOCK_FACTOR_FIELDS` 白名单（含可修订分级）、ClickHouse
   `stock_factor` DDL 与 migrate 注册、config 扩展（无 Alembic 迁移）。
2. **阶段 2：Provider 契约与 Adapter**——`StockFactorProvider` Port、
   `TushareStockFactorProvider`（字段白名单、分级元数据、节流器、重试/
   错误映射、触顶门禁）、`RateLimiter` 泛化重命名（含 006 兼容别名）、
   Registry、契约测试与替身；
   **字段白名单以部署账户实测字段全集校准（前置门禁，research 待验证项 2，
   须在 US1 契约测试前完成）**。
3. **阶段 3：领域校验/发布/审计**——身份解析（003 `provider_mappings`
   只读）、批次校验与字段分级冲突判定、ClickHouse 发布、MySQL 审计终态、
   `StockFactorService`、内部查询。
4. **阶段 4：工作流/调度/运维**——`股票技术面因子交易日同步`/
   `股票技术面因子历史回补` Flow（中文名）、prefect.yaml Deployment、
   日志与可观测、quickstart 验证。
5. **阶段 5：验证与上线门禁**——单元/契约/集成/端到端全量通过、
   ruff/mypy、上线门禁 7 项实测（research 待验证项）、quickstart §8
   逐项确认。

## 复杂度跟踪

> **Fill ONLY if Constitution Check has violations that must be justified**

无宪章违反项，不需要复杂度例外。

补充登记（不构成违反，记录备选拒绝理由）：`RateLimiter` 泛化重命名为
spec FR-005/NFR-004 与可维护性要求的低风险重构（006 测试兜底）；
备选“原样 import IndexRateLimiter”被拒（stock 功能依赖 index 命名模块，
语义误导且错误命名随后续功能扩散）；备选“新建功能私有节流器”被拒
（重复实现，违反宪章 V）。备选“自建股票身份表”被拒（与 003 重复，
产生两套身份事实来源，research 决策 1）。
