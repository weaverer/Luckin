# 实施计划：股东数据交易日同步（008-sync-shareholder-data）

**分支**：`008-sync-shareholder-data`（本次未配置分支创建钩子，沿用当前分支） | **日期**：2026-08-05 | **规格**：[spec.md](spec.md)

**输入**：来自 `/specs/008-sync-shareholder-data/spec.md` 的功能规格

**说明**：本模板由 `/speckit-plan` 填写；该命令定义具体执行流程。

## 摘要

每个交易日（Cron 错峰 `0/5/10 17 * * 1-5`，Asia/Shanghai，沿用项目惯例
17:00）通过 Tushare `top10_holders`（前十大股东）/ `top10_floatholders`
（前十大流通股东）/ `stk_holdernumber`（股东人数）三个接口按**公告日**
全市场提取新增披露的股东数据（**2026-08-05 部署账户实测**：无需 ts_code、
`has_more/offset` 分页有效、单次上限 6,000 行、字段全集 9+9+4 与文档
逐名一致），存入 ClickHouse `shareholder_holding`（前十大/前十大流通
股东统一表，`holder_kind` 判别）与 `shareholder_count` 两张业务表。
**三个接口拆分为 3 套独立 Flow（增量 3 + 回补 3，用户显式要求）**：
`前十大股东交易日同步`/`前十大流通股东交易日同步`/`股东人数交易日同步`
（schedule_slug 分别 `top10-holders-sync`/`top10-floatholders-sync`/
`holder-count-sync`）与对应三个 `* 历史回补` Flow——任一接口失败只影响
自身 run 终态，不影响其他两个接口，可单独重跑（FR-011 按接口独立成
终态，与 005 每接口独立 Deployment 模式一致）；错峰 5 分钟使三个增量
Flow 串行执行，时间轴可预期（账户级限流由 Redis 分布式节流器跨进程
强保证，错峰仅运维友好）。提供 2024-01-01 起的人工初始化回补
（`top10_*` 按报告期季度末约 90 次请求、`stk_holdernumber`
按公告日约 630 次）；全程遵守来源每分钟 400 次限流（用户显式指定，
**账户级共享预算：三个接口请求合计**，`RedisRateLimiter` 分布式节流
最小间隔 150 毫秒）；审计复用 005
的 `market_data_sync_run/attempt/issue` 三表（新增 `data_kind` 取值
`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/`HOLDER_COUNT`，与 005 每接口
一取值模式一致）；股票身份**复用 003** 的 `stock_current`/
`stock_provider_mapping`（不新建 MySQL 表、无 DDL 变更）。
增量窗口 =（本接口 `max(ann_date)` 水位, 昨日]（按接口/kind 分别取
水位——两 top10 接口同表写入，表级水位会跳日），水位自愈——失败
交易日由下一运行的自然窗口覆盖；更正公告（新 ann_date 同身份值变化）
按最新公告修订更新（`updated_count`），非新公告值变化
`RECORD_CONFLICT` 整批失败。实现沿用 005/006/007 已验证的
「Port 契约 + Tushare Adapter + Service 编排 + MySQL 审计 + ClickHouse
发布 + Prefect 参数化 Flow」模式（详见 research.md 决策 1~7）。

## 技术上下文

**语言/版本**：Python 3.12（与仓库一致）

**主要依赖**：httpx（Tushare/ClickHouse HTTP）、prefect ≥ 3.8（Flow 与调度）、
SQLAlchemy/Alembic（MySQL 审计与身份读取）、pydantic-settings（配置）；
不新增 tushare SDK、clickhouse-driver 或任何新依赖（沿用 005/006/007 的
HTTP 直连模式）

**存储**：MySQL（身份读取复用 003 两表 + 审计复用 005 三表，
**无新建表、无结构性 DDL 变更**）；ClickHouse（新建 `shareholder_holding`
与 `shareholder_count`，均 `ReplacingMergeTree(updated_at)`，
`PARTITION BY toYYYYMM(end_date)`，排序键见 data-model §3）

**测试**：pytest；契约测试（假 Provider/替身）、单元、集成（`-m mysql`）、
端到端；`uv run ruff check .`、`uv run mypy --strict src`（006 口径）、
`uv run pytest`

**目标平台**：WSL2 本地开发（应用进程），Docker Compose 承载 MySQL、
ClickHouse、Redis、Prefect Server（沿用 compose.yml，端口仅绑定 127.0.0.1）

**项目类型**：数据同步后台服务（Prefect 编排，无 UI 变更）

**性能目标**：增量同步（17:00 错峰启动）当日形成终态（NFR-001）；
通常 1~3 个公告日 × 1~10 页 ≈ 数 10 次请求/接口 @400/min 远小于单日
限流预算；回补按接口独立（`top10_*` ~10 期 × ~9 页 ≈ 90 次/接口
≈ 秒级；`stk_holdernumber` ~630 日 ≈ 630 次 ≈ 2 分钟，research
待验证项 6）；三个增量 Flow 错峰串行，任意时刻至多一个运行

**约束**：
- 三个接口拆分为 3 套独立 Flow（增量 + 回补，用户显式要求），
  **故障隔离**：任一接口失败只影响自身 run，不影响其他两个接口，
  可单独重跑（research 决策 6）。
- 计划 Cron 错峰 `0/5/10 17 * * 1-5`（增量三 Flow 分别
  `0 17`/`5 17`/`10 17`），时区 Asia/Shanghai；目标交易日以
  `prefect.runtime.flow_run.scheduled_start_time` 为准，直接调用必须显式
  提供 `scheduled_at`。
- 每个 Flow 启动后必须查询项目交易日历（CN-S）；非交易日直接记录
  `SKIPPED_NOT_TRADING_DAY` 并成功结束。
- 来源限流每分钟 400 次（用户显式指定，**账户级共享预算：三个接口的
  请求合计**，不是每接口各 400 次）：`RedisRateLimiter` 分布式节流
  （Redis ZSET 滑窗 + Lua 原子判定，任意 60 秒窗口跨进程合计 ≤ 400 次、
  最小间隔 150 毫秒；Registry 注入 Provider，三接口所有 flow run 进程
  共享同一预算；Redis 不可达降级进程级限流 fail-open，research 决策 4
  修订）；限流错误映射 `PROVIDER_RATE_LIMITED`，Adapter 初次调用后重试
  最多 3 次（退避 30/120/300 秒，受 deadline 约束）；Flow `retries=0`，
  防止重试层相乘。**错峰 5 分钟仅作运维友好**（账户级限流已强保证）。
- 运行 `run_key` 只由 `<DATA_KIND> + 运行类型（SCHEDULED/BACKFILL）+
  schedule_slug 或 backfill_batch_id + 原计划 UTC 时点或目标交易日 +
  target_trade_date` 生成，`DATA_KIND` ∈ {`TOP10_HOLDERS`,
  `TOP10_FLOAT_HOLDERS`, `HOLDER_COUNT`}；MySQL 唯一约束是幂等最终
  保障。
- 提取一律**不传 ts_code**（实测 2026-08-05 验证全市场查询可行，文档
  标注必填不准确）；`top10_*` 用 `ann_date=YYYYMMDD`、`stk_holdernumber`
  用 `start_date=end_date=YYYYMMDD`（两接口 start/end 参数语义不同，
  不得混用）。
- 单次上限按实测 6,000 行（`shareholder_data_page_limit=6000`，三接口
  统一）；`has_more=True` 必须 `offset` 续取至 `has_more=False`，
  位置不前进/重复页/超页数即不完整（`PROVIDER_RESPONSE_CAPPED`）；
  `stk_holdernumber` 文档"单次最大 3,000"已过时（实测 5,286 行完整
  返回），完整性判定只依赖 `has_more` 标志。
- 增量窗口 =（本接口水位, 昨日]；水位按接口/kind 分别取
  `max(ann_date) FINAL`（`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS` 各取
  `shareholder_holding` 对应 `holder_kind` 的水位、`HOLDER_COUNT` 取
  `shareholder_count` 水位——两 top10 接口同表写入，表级水位会让先
  运行的接口把后运行接口的当日公告跳过）；表空则水位 = `2024-01-01`
  （与回补起点一致，首轮重叠幂等衔接）；水位 ≥ 昨日时直接成功终态，
  不调用来源。
- 修订 vs 冲突（FR-010/ED-010）：同身份值变化按 `ann_date` 锚点判定——
  入站 ann_date > 既有 ann_date 为正常修订（更新，计 `updated_count`）；
  否则 `RECORD_CONFLICT` 整批失败，不得任意覆盖。
- 单批候选以一次 ClickHouse 批量 INSERT（block 级原子）写入，成功后
  在同一 MySQL 事务写 attempt/run 成功终态；失败不清空已有数据。
- 股票身份以 003 主数据为权威：`provider_mappings("tushare")` 解析
  ts_code → stock_id；未映射 → `invalid_count` + 脱敏 issue
  （`UNKNOWN_STOCK_IDENTITY`），跳过该条，不阻断整批。
- 宪章 VI：本功能不新建、不结构性修改任何 MySQL 业务表（身份复用 003、
  审计复用 005），逐表治理不适用；ClickHouse 两表属宪章允许的
  "外部引擎承载业务数据"情形，在 data-model.md §3/§4 记录引擎、排序键、
  分区与幂等语义。
- 流程名称使用简体中文且语义符合业务场景（FR-019）：增量
  "前十大股东交易日同步"/"前十大流通股东交易日同步"/"股东人数交易日
  同步"，回补 "前十大股东历史回补"/"前十大流通股东历史回补"/"股东人数
  历史回补"；内部 `schedule_slug` 保持 ASCII
  （`top10-holders-sync`/`top10-floatholders-sync`/`holder-count-sync`）
  作为幂等键与审计标识。
- 供应商细节（字段名、错误码、限流档位、分页参数）只存在于 Adapter；
  业务代码只依赖 Port 契约与规范模型。

**规模/范围**：全部 A 股（三所，按公告日全市场提取）；2024-01-01 起
回补至当前增量；三类股东数据（前十大股东 9 字段、前十大流通股东 9
字段、股东人数 4 字段）；明确不在范围内：股票列表维护（003 承担）、
股权集中度/筹码分布/股东户数变化计算（消费方职责）、公共 API/UI、
跨交易日自动补同步（补同步经人工触发回补 Flow）、与 005/007 行情
因子表的数据合并

## 宪章检查

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

> 所有项目文档必须使用简体中文（代码标识符、命令、协议字段及专有名词除外）。

### 研究前门禁

- **规格与追溯**：通过。spec.md 含 3 个按价值排序的用户故事（P1 增量同步、
  P2 初始化回补、P3 失败与质量识别）、19 条可测试功能需求、边界情况与
  SC-001~SC-010 可度量成功标准；本计划全部决策可追溯至 spec 需求
  （research.md 决策 1~7 均标注对应需求编号）。
- **架构与数据边界**：通过。职责边界沿用 005/006/007 已验证划分：
  Adapter（供应商隔离）→ Service（领域编排）→ Repository（MySQL 审计/
  ClickHouse 发布）；数据所有权明确（身份/审计 = MySQL 复用，股东数据 =
  ClickHouse 分析型），生命周期与一致性在 data-model.md §1、§4 记录；
  无跨边界新增基础设施。
- **第三方数据源可替换性**：通过。`shareholder-data-provider.md` 定义
  供应商无关 Port（三个提取方法）；`tushare-shareholder-data.md` 定义
  唯一允许的三个接口、字段白名单映射、错误映射与限流档位；契约测试
  要点含替代实现/测试替身验证（ED-006/ED-007）；业务代码不依赖供应商
  SDK/传输模型/专有字段（ED-006 白名单严格校验）。
- **测试与质量门禁**：通过。四份契约均含"契约测试要点"；Service 契约
  定义假 Provider 全流程测试（含修订 vs 冲突用例、水位窗口用例）；
  预计划 pytest 单元/契约/集成/端到端 + ruff + mypy 质量门禁，与
  005/006/007 相同工具链。
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
- **简洁性**：通过。不新增框架/服务/依赖；节流复用 007 泛化的共享
  `RateLimiter`（仅新配置），前十大股东与前十大流通股东合并为一张
  ClickHouse 表（`holder_kind` 判别），与 006/007 相比不增加新抽象。

### 设计后复核

- **规格与追溯**：通过。Phase 0/1 全部产物（research 7 决策 + 4 份实测
  探针、data-model 5 节、契约 4 份、quickstart 8 节）逐项对应 spec
  FR/NFR/ED/SC；tasks 阶段将按用户故事分组并编号追溯。
- **架构与数据边界**：通过。设计未引入新边界；股票身份只读复用 003
  （data-model §2.1），无第二套身份事实来源；ClickHouse 两表决策与
  修订/冲突发布语义在 data-model §3/§4 记录。
- **第三方数据源可替换性**：通过。四份契约明确 Port/实现/测试替身三层；
  供应商字段白名单、分页参数、限流档位全部封装在 Adapter
  （tushare 契约 §2/§3/§5/§6）；换源不改业务代码
  （shareholder-data-service.md §6）。
- **测试与质量门禁**：通过。设计后契约测试要点完整（Provider 白名单/
  节流/分页续取/触顶/重试；Service 按接口水位窗口/幂等/修订 vs 冲突/
  空响应区分/故障隔离；Flow 参数校验/中文名与 ASCII slug 双轨/日志
  白名单）；上线门禁 8 项实测项（quickstart §8）前 3 项（全市场查询、
  字段全集、分页机制）**已于 2026-08-05 实测完成**。
- **安全与最小暴露**：通过。设计确认无新秘密入码、无新端口暴露；
  issue 脱敏与日志白名单在契约层落实。
- **可观测与运维**：通过。审计复用保证跨功能一致的排障体验，
  `data_kind` 按接口取值（`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/
  `HOLDER_COUNT`）使三接口运行状态可直接定位；
  quickstart §7/§8 提供运行验证与上线实测步骤（含故障隔离验证）。
- **MySQL 表结构**：通过（不适用）。设计确认无新建/无结构性变更
  （data-model §2.3），无例外申请。
- **简洁性**：通过。复杂度跟踪无违反项；两接口共用一张表与水位自愈
  设计（research 决策 2/6）比独立表 + 独立水位存储更简单；3 接口拆分
  为 3 套 Flow 为用户显式要求（非新增抽象，与 005 每接口独立
  Deployment 模式一致，研究决策 6 已论证备选单 Flow 方案被拒理由），
  备选拒绝理由登记于复杂度表。

## 项目结构

### 文档（本功能）

```text
specs/008-sync-shareholder-data/
├── plan.md              # 本文件 (/speckit-plan 输出)
├── research.md          # Phase 0 输出（决策 1~7 + 实测摘要 + 待验证项）
├── data-model.md        # Phase 1 输出（身份/审计复用 + ClickHouse 两表）
├── quickstart.md        # Phase 1 输出（端到端验证与排障）
├── contracts/           # Phase 1 输出（4 份契约）
│   ├── tushare-shareholder-data.md
│   ├── shareholder-data-provider.md
│   ├── shareholder-data-service.md
│   └── prefect-flow.md
└── tasks.md             # Phase 2 输出 (/speckit-tasks 命令 - 本命令不创建)
```

### 源代码（仓库根目录）

```text
scripts/
├── probe_shareholder_api.py       # 探针 1：limit=1 昨日数据（用户指示）
├── probe_shareholder_api2.py      # 探针 2：全市场查询可行性
├── probe_shareholder_api3.py      # 探针 3：行数量级与 has_more
└── probe_shareholder_api4.py      # 探针 4：offset 分页验证

src/lucking/
├── config.py                      # + shareholder_data_* 配置项（含 page_limit=6000、rate_limit_per_minute=400）
├── clickhouse.py                  # + shareholder_holding / shareholder_count DDL 与 migrate 注册
├── models/
│   ├── market_data.py             # + DataKind.TOP10_HOLDERS / TOP10_FLOAT_HOLDERS /
│                                  #   HOLDER_COUNT 枚举值
│   └── shareholder_data.py        # + 规范 DTO（ShareholderDataRequest/ProviderShareholderRecord/
│                                  #   ProviderShareholderCountRecord/SHAREHOLDER_DATA_FIELDS 白名单/…）
├── ports/
│   ├── market_data_common.py      # （复用）RetrievalEvidence、ProviderError 家族
│   └── shareholder_data_common.py # + ShareholderDataProvider Protocol（三个提取方法）
├── integrations/
│   ├── registry.py                # + register/build_tushare_shareholder_data_provider
│   └── tushare/
│       ├── client.py              # （复用）TushareClient 信封
│       ├── rate_limiter.py        # （复用）共享 RateLimiter（007 泛化，新配置 400/min）
│       └── shareholder_data_provider.py   # + Adapter（三接口、字段白名单、分页、节流、重试、错误映射）
├── repositories/
│   ├── shareholder_data_clickhouse.py  # + 批量发布与查询（两表）、max(ann_date) 水位
│   ├── stock_list.py              # （复用）provider_mappings 身份解析
│   └── market_data.py             # （复用）审计 Repository（data_kind=TOP10_HOLDERS 等）
├── services/
│   └── shareholder_data.py        # + ShareholderDataService（按接口 6 入口：水位/窗口、校验、发布、终态）
└── flows/
    └── shareholder_data.py        # + 6 Flow：前十大股东交易日同步 / 前十大流通股东交易日同步 /
                                   #   股东人数交易日同步 / 前十大股东历史回补 / 前十大流通股东历史回补 /
                                   #   股东人数历史回补

prefect.yaml                          # + 6 个 Deployment（中文名，增量错峰 0/5/10 17 * * 1-5，
                                      #   回补人工触发无 schedule）

tests/
├── unit/…（水位窗口/修订 vs 冲突判定/节流/身份解析）
├── contract/…（Provider 白名单/分页/错误映射；Service 假 Provider 全流程；Flow 参数）
├── integration/…（MySQL 审计幂等 -m mysql；ClickHouse 发布 -m mysql；限流实测）
└── e2e/…（可选：真实账户冒烟，上线门禁）
```

**结构决策**：新建独立的 `shareholder_data` 垂直切片（ports/integrations/
repositories/services/flows/models 各一文件），理由：股东数据按公告日/
报告期推进、修订 vs 冲突按 ann_date 锚点判定、双表发布与 6,000 行分页
均与既有功能语义不同，但复用 003 身份解析、005 审计三表、TushareClient
信封、共享 RateLimiter、交易日历、配置前缀与 Deployment 模式；塞入既有
`market_data.py` 会造成职责混杂（宪章 II）。与 007 的结构差异：
**无字段校准需求**（探针已实测字段全集与文档一致）、**无功能私有节流器**
（直接复用 007 泛化模块）、回补的 `top10_*` 与 `stk_holdernumber`
按各自接口语义展开日期窗口。

## 实施阶段

1. **阶段 1：模型与数据库**——`DataKind.TOP10_HOLDERS`/
   `TOP10_FLOAT_HOLDERS`/`HOLDER_COUNT` 枚举、
   `shareholder_data` 规范 DTO 与 `SHAREHOLDER_DATA_FIELDS` 白名单、
   ClickHouse `shareholder_holding`/`shareholder_count` DDL 与 migrate
   注册、config 扩展（无 Alembic 迁移）。
2. **阶段 2：Provider 契约与 Adapter**——`ShareholderDataProvider` Port
   （三提取方法）、`TushareShareholderDataProvider`（字段白名单、`has_more`
   /offset 分页、节流器 400/min、重试/错误映射、完整性门禁）、Registry、
   契约测试与替身；**分页与字段行为已由 2026-08-05 实测确认
   （research 待验证项 1~3 ✅），无阻塞门禁**。
3. **阶段 3：领域校验/发布/审计**——水位计算（`max(ann_date)`）与窗口
   展开、身份解析（003 `provider_mappings` 只读）、批次校验与
   修订/冲突判定（ann_date 锚点）、ClickHouse 双表发布、MySQL 审计终态、
   `ShareholderDataService`、内部查询。
4. **阶段 4：工作流/调度/运维**——6 个 Flow（增量 3 + 回补 3，中文名）、
   prefect.yaml Deployment（增量错峰 `0/5/10 17 * * 1-5`）、
   日志与可观测（`data_kind` 按接口）、故障隔离验证、quickstart 验证。
5. **阶段 5：验证与上线门禁**——单元/契约/集成/端到端全量通过、
   ruff/mypy、上线门禁 7 项实测（research 待验证项 4~7）、quickstart §8
   逐项确认。

## 复杂度跟踪

> **Fill ONLY if Constitution Check has violations that must be justified**

无宪章违反项，不需要复杂度例外。

补充登记（不构成违反，记录备选拒绝理由）：① 前十大股东与前十大流通股东
合并为一张 ClickHouse 表（`holder_kind` 判别）——备选"两张同构表"被拒
（字段与处理逻辑完全同构，双表增加迁移与消费复杂度，research 决策 2）；
② 增量水位 = ClickHouse `max(ann_date)` 自愈计算，无独立水位存储——
备选"水位表/配置"被拒（无状态、自愈、免维护，research 决策 6）；
③ 限流为**账户级共享预算 400/min**（用户澄清）：新增 `RedisRateLimiter`
分布式节流器（research 决策 4 修订）——备选"仅进程级 `RateLimiter` +
错峰/串行约定"被拒（3 Flow 拆分后多进程并发时合计可超账户预算，
错峰只降低概率、串行是约定非强制；回补与增量同跑即可能超限）；
备选"每接口独立节流器"被拒（合计必然超限）；Redis 依赖为项目既有
（pyproject + compose），Lua 原子判定避免竞态，Redis 故障降级进程级
限流 fail-open（限流基础设施故障不阻断数据同步）。
⑤ 3 接口拆分为 3 套独立 Flow（增量 + 回补共 6 个）——用户显式要求
（故障隔离）；备选"单 Flow 处理全部接口"（初版设计）与"单 Flow +
接口参数"被拒（任一接口失败拖累全部 / run_key 与审计难分接口，
research 决策 6 备选方案）；错峰调度保留为运维友好（账户级限流已由
Redis 分布式节流器强保证）。
④ 自建股票身份表——被拒（与 003 重复，产生两套身份事实来源，
research 决策 3）。
