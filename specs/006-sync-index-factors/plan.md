# 实施计划：指数技术因子交易日同步（006-sync-index-factors）

**分支**：`005-a-share-trend-data`（本次未配置分支创建钩子，沿用当前分支） | **日期**：2026-08-02 | **规格**：[spec.md](spec.md)

**输入**：来自 `/specs/006-sync-index-factors/spec.md` 的功能规格

**说明**：本模板由 `/speckit-plan` 填写；该命令定义具体执行流程。

## 摘要

每个交易日北京时间 17:00，通过 Tushare `idx_factor_pro`（指数技术因子，专业版）
按交易日提取全部指数（大盘指数、申万行业指数、中信指数）的技术因子与基础行情
（共 87 个规范字段，均不复权），存入 ClickHouse `index_factor` 宽表；提供
2024-01-01 起的人工初始化回补；全程遵守来源每分钟 30 次的限流（Adapter 内
进程级节流，最小间隔 2 秒）；审计复用 005 的 `market_data_sync_run/attempt/issue`
三表（新增 `data_kind=INDEX_FACTOR`）；指数身份由本功能自举注册
（`index_current` + `index_provider_mapping`）。实现沿用 005 已验证的
「Port 契约 + Tushare Adapter + Service 编排 + MySQL 审计 + ClickHouse 发布 +
Prefect 参数化 Flow」模式（详见 research.md 决策 1~7）。

## 技术上下文

**语言/版本**：Python 3.12（与仓库一致）

**主要依赖**：httpx（Tushare/ClickHouse HTTP）、prefect ≥ 3.8（Flow 与调度）、
SQLAlchemy/Alembic（MySQL 审计与身份表）、pydantic-settings（配置）；
不新增 tushare SDK、clickhouse-driver 或任何新依赖（沿用 005 的 HTTP 直连模式）

**存储**：MySQL（身份表 `index_current`/`index_provider_mapping` 新建 + 审计表
复用 005 三表）；ClickHouse（`index_factor` 新建宽表，
`ReplacingMergeTree(updated_at)`，`ORDER BY (trade_date, index_id)`，
`PARTITION BY toYYYYMM(trade_date)`）

**测试**：pytest；契约测试（假 Provider/替身）、单元、集成（`-m mysql`）、
端到端；`uv run ruff check .`、`uv run mypy src`、`uv run pytest`

**目标平台**：WSL2 本地开发（应用进程），Docker Compose 承载 MySQL、ClickHouse、
Redis、Prefect Server（沿用 compose.yml，端口仅绑定 127.0.0.1）

**项目类型**：数据同步后台服务（Prefect 编排，无 UI 变更）

**性能目标**：增量同步（17:00 启动）当日形成终态（NFR-001）；回补约 610 个
交易日，节流 ≥ 2 秒/请求，全程 ≤ 30 次/分钟，约 20~30 分钟完成（research
待验证项 6）；单日请求返回行数预期远小于 8,000 上限（research 待验证项 1）

**约束**：
- 计划 Cron 固定 `0 19 * * 1-5`，时区 Asia/Shanghai；目标交易日以
  `prefect.runtime.flow_run.scheduled_start_time` 为准，直接调用必须显式
  提供 `scheduled_at`。
- 每个 Flow 启动后必须查询项目交易日历（CN-S）；非交易日直接记录
  `SKIPPED_NOT_TRADING_DAY` 并成功结束。
- 来源限流每分钟 30 次：Adapter 内进程级节流（任意 60 秒窗口 ≤ 30 次，
  最小间隔 2 秒，`monotonic`/`sleep` 可注入）；限流错误映射
  `PROVIDER_RATE_LIMITED`，Adapter 初次调用后重试最多 3 次（退避
  30/120/300 秒，受 deadline 约束）；Flow `retries=0`，防止重试层相乘。
- 运行 `run_key` 只由 `data_kind + 运行类型（SCHEDULED/BACKFILL）+
  schedule_slug 或 backfill_batch_id + 原计划 UTC 时点或目标交易日 +
  target_trade_date` 生成；MySQL 唯一约束是幂等最终保障。
- 单次同步的全部候选以一次 ClickHouse 批量 INSERT（block 级原子）写入，
  成功后在同一 MySQL 事务写 attempt/run 成功终态；`ReplacingMergeTree(updated_at)`
  同键替换实现幂等。
- 单次返回上限 8,000 行（独立配置 `index_factor_page_limit=8000`）；
  触顶且无经验证续取手段时判定不完整并失败，禁止猜测参数。
- 指数 `ts_code` 后缀白名单 `.SH/.SZ/.CSI/.SI`；非法后缀、空代码 → 无效计数
  + 脱敏 issue，跳过该条；合法即自举注册指数身份。
- 宪章 VI：两张新建 MySQL 身份表统一以 `id BIGINT UNSIGNED AUTO_INCREMENT`
  为物理主键，业务标识 UUID 带 UNIQUE，数据库维护 `created_at/updated_at`，
  中文表注释与每列非空中文注释；ClickHouse `index_factor` 属宪章允许的
  “外部引擎承载业务数据”情形，在 data-model.md 记录引擎、排序键、分区与
  幂等语义；审计表复用不产生结构性 DDL 变更。
- 供应商细节（字段名、错误码、限流档位、ts_code 后缀）只存在于 Adapter；
  业务代码只依赖 Port 契约与规范模型。

**规模/范围**：全部指数（大盘指数、申万行业指数、中信指数，约千余只，
按交易日整体提取）；2024-01-01 起回补至当前增量；87 个规范字段/日/指数；
因子值以 `Nullable(Decimal)` 保存缺失；明确不在范围内：指数列表维护入口、
复权价格计算、因子重算、趋势分析/选股/回测、公共 API/UI、跨交易日自动补同步
（补同步经人工触发回补 Flow）

## 宪章检查

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

> 所有项目文档必须使用简体中文（代码标识符、命令、协议字段及专有名词除外）。

### 研究前门禁

- **规格与追溯**：通过。spec.md 含 3 个按价值排序的用户故事（P1 增量同步、
  P2 初始化回补、P3 失败与质量识别）、18 条可测试功能需求、边界情况与
  SC-001~SC-010 可度量成功标准；Clarifications 记录 3 条已确认决策
  （2024-01-01 起点、全部指数、全部因子字段）；本计划全部决策可追溯至
  spec 需求（research.md 决策 1~7 均标注对应需求编号）。
- **架构与数据边界**：通过。职责边界沿用 005 已验证划分：Adapter（供应商
  隔离）→ Service（领域编排）→ Repository（MySQL 审计/ClickHouse 发布）；
  数据所有权明确（指数主数据/审计 = MySQL 事务型；因子 = ClickHouse 分析型），
  生命周期与一致性在 data-model.md §1、§5 记录；无跨边界新增基础设施。
- **第三方数据源可替换性**：通过。`index-factor-provider.md` 定义供应商无关
  Port；`tushare-index-factor.md` 定义唯一允许接口、字段白名单映射、错误映射
  与限流档位；契约测试要点含替代实现/测试替身验证（ED-006/ED-007）；
  业务代码不依赖供应商 SDK/传输模型/专有字段（ED-005 白名单严格校验）。
- **测试与质量门禁**：通过。四份契约均含“契约测试要点”；Service 契约定义
  假 Provider 全流程测试；预计划 pytest 单元/契约/集成/端到端 + ruff + mypy
  质量门禁，与 005 相同工具链。
- **安全与最小暴露**：通过。Token 为 SecretStr 延迟读取、不入日志（NFR-005）；
  日志白名单仅 run_key/状态/计数/脱敏摘要；issue 表只存哈希与白名单摘要；
  无新增网络暴露（沿用 compose.yml 回环绑定）；回补区间校验拒绝未来日期。
- **可观测与运维**：通过。复用 005 审计三表（run/attempt/issue 计数与 21 类
  问题类别）；JsonlLogStore 结构化日志与窗口及时性；quickstart.md §7
  五分钟排障、§8 上线门禁；非交易日 SKIPPED 不产生误告警。
- **MySQL 表结构**：通过。新建 `index_current`/`index_provider_mapping` 两张
  表全部采用宪章 VI 标准物理治理（见 data-model.md §2 与 技术上下文-约束）；
  审计表复用 005 三表（data_kind 新取值，无结构变更）；无获批例外。
- **简洁性**：通过。不新增框架/服务/依赖；唯一新抽象为 Adapter 内进程级
  节流器（research 决策 4：仓库现无限流器，30 次/分钟为 spec 硬性要求，
  进程内实现优于 Redis 分布式方案，理由与备选记录于 research.md）。

### 设计后复核

- **规格与追溯**：通过。Phase 0/1 全部产物（research 7 决策、data-model
  6 节、契约 4 份、quickstart 8 节）逐项对应 spec FR/NFR/ED/SC；
  tasks 阶段将按用户故事分组并编号追溯。
- **架构与数据边界**：通过。设计未引入新边界；指数身份自举注册为
  003 股票映射模式的平行扩展（data-model §2）；ClickHouse 单表宽表
  决策与发布语义在 data-model §3/§5 记录。
- **第三方数据源可替换性**：通过。四份契约明确 Port/实现/测试替身三层；
  供应商后缀白名单、字段映射、限流档位全部封装在 Adapter（tushare 契约
  §3/§4/§5）；换源不改业务代码（index-factor-service.md §6）。
- **测试与质量门禁**：通过。设计后契约测试要点完整（Provider 白名单/节流/
  触顶/重试；Service 幂等/空响应区分/冲突；Flow 参数校验/日志白名单）；
  上线门禁 6 项实测项（quickstart §8）覆盖权限、积分、限流与数据完备性。
- **安全与最小暴露**：通过。设计确认无新秘密入码、无新端口暴露；
  issue 脱敏与日志白名单在契约层落实。
- **可观测与运维**：通过。审计复用保证跨功能一致的排障体验；
  quickstart §7/§8 提供运行验证与上线实测步骤。
- **MySQL 表结构**：通过。身份表 DDL 符合宪章 VI（data-model §2 逐列）；
  审计复用无结构变更；无例外申请。
- **简洁性**：通过。复杂度跟踪无违反项；节流器必要性已在研究决策 4 论证，
  复杂度表中登记备选拒绝理由（见下）。

## 项目结构

### 文档（本功能）

```text
specs/006-sync-index-factors/
├── plan.md              # 本文件 (/speckit-plan 输出)
├── research.md          # Phase 0 输出（决策 1~7 + 待验证项）
├── data-model.md        # Phase 1 输出（MySQL 身份表 / ClickHouse index_factor / 审计复用）
├── quickstart.md        # Phase 1 输出（端到端验证与排障）
├── contracts/           # Phase 1 输出（4 份契约）
│   ├── tushare-index-factor.md
│   ├── index-factor-provider.md
│   ├── index-factor-service.md
│   └── prefect-flow.md
└── tasks.md             # Phase 2 输出 (/speckit-tasks 命令 - 本命令不创建)
```

### 源代码（仓库根目录）

```text
src/lucking/
├── config.py                      # + index_factor_* 配置项（含 page_limit=8000、rate_limit_per_minute=30）
├── clickhouse.py                  # + index_factor 表 DDL 与 migrate 注册
├── models/
│   ├── index_factor.py            # + 规范 DTO（IndexFactorRequest/ProviderIndexFactorRecord/…）
│   └── market_data.py             # （复用）审计 ORM 与 run_key 生成（data_kind 新取值）
├── ports/
│   └── index_factor_common.py     # + IndexFactorProvider Protocol、RetrievalEvidence、ProviderError 复用
├── integrations/
│   ├── registry.py                # + register/build_tushare_index_factor_provider
│   └── tushare/
│       ├── client.py              # （复用）TushareClient 信封
│       ├── index_factor_provider.py   # + Adapter（字段白名单、节流器、重试、错误映射）
│       └── index_rate_limiter.py  # + 进程级节流器（最小间隔 2 秒，monotonic/sleep 注入）
├── repositories/
│   ├── index_factor_identity.py   # + 指数身份注册/解析（index_current/index_provider_mapping）
│   ├── index_factor_clickhouse.py # + 批量发布与查询（index_factor 表）
│   └── market_data.py             # （复用）审计 Repository（data_kind=INDEX_FACTOR）
├── services/
│   └── index_factor.py            # + IndexFactorService（命令分派、校验、发布、终态）
└── flows/
    └── index_factor.py            # + index_factor_sync / index_factor_backfill 两个 Flow

migrations/versions/
└── 005_create_index_identity_tables.py   # + index_current / index_provider_mapping DDL

prefect.yaml                          # + index-factor-sync/指数技术因子同步 与 index-factor-backfill/指数技术因子历史回补 Deployment

tests/
├── unit/…（index_factor 校验/节流/身份解析）
├── contract/…（Provider 白名单/错误映射/Service 假 Provider 全流程/Flow 参数）
├── integration/…（MySQL 审计幂等 -m mysql；ClickHouse 发布 -m mysql；限流实测）
└── e2e/…（可选：真实账户冒烟，上线门禁）
```

**结构决策**：新建独立的 `index_factor` 垂直切片（ports/integrations/
repositories/services/flows/models 各一文件），理由：指数身份体系、宽表因子
模型与 30 次/分钟节流均与股票行情（005）语义不同，但复用其审计三表、
TushareClient 信封、交易日历、配置前缀与 Deployment 模式；塞入既有
`market_data.py` 会造成职责混杂（宪章 II），独立切片保持边界清晰且
复用成本最低。

## 实施阶段

1. **阶段 1：身份与数据库**——`index_current`/`index_provider_mapping` DDL 与
   ORM、`index_factor` ClickHouse DDL、config 扩展、Alembic 迁移、migrate 注册。
2. **阶段 2：Provider 契约与 Adapter**——`IndexFactorProvider` Port、
   `TushareIndexFactorProvider`（字段白名单、节流器、重试/错误映射、触顶门禁）、
   Registry、契约测试与替身。
3. **阶段 3：领域校验/发布/审计**——身份注册与解析、批次校验、ClickHouse
   发布、MySQL 审计终态、`IndexFactorService`、内部查询。
4. **阶段 4：工作流/调度/运维**——`index_factor_sync`/`index_factor_backfill`
   Flow、prefect.yaml Deployment、日志与可观测、quickstart 验证。
5. **阶段 5：验证与上线门禁**——单元/契约/集成/端到端全量通过、ruff/mypy、
   上线门禁 6 项实测（research 待验证项）、quickstart §8 逐项确认。

## 复杂度跟踪

> **Fill ONLY if Constitution Check has violations that must be justified**

无宪章违反项，不需要复杂度例外。

补充登记（不构成违反，记录备选拒绝理由）：Adapter 内进程级节流器为
spec FR-005/NFR-004 硬性要求（仓库现无限流器）；备选“回补 Flow 循环内
逐日 sleep”被拒（增量链路不受保护、不可单测）；备选“Redis 分布式限流器”
被拒（首期单 worker 场景进程内足够，避免新增分布式复杂度；未来多 worker
时再评估，research 决策 4）。
