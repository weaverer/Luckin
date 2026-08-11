# 任务：股东数据交易日同步（008-sync-shareholder-data）

**输入**：`/specs/008-sync-shareholder-data/` 中的设计文档

**前置条件**：plan.md（必需）、spec.md（用户故事必需）、research.md、data-model.md、contracts/

> 任务文档必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**Tests**: Tests are REQUIRED for changes to observable behavior, public contracts, data
models, or failure handling. Defect fixes require a failing regression test first. Include
contract/integration tests for interface changes and focused end-to-end tests for critical flows.

**第三方数据集成**：本功能通过 Tushare `top10_holders`/`top10_floatholders`/
`stk_holdernumber` 获取数据，任务已覆盖供应商无关端口与规范化模型（T003/T005）、
独立供应商适配器（T010）、配置/依赖注入（T001/T006）、契约测试
（T008/T009/T016/T020），以及替代适配器或测试替身（T024）。
**接口分页与字段全集已由 2026-08-05 部署账户实测确认**（research 待验证项
1~3 ✅，`scripts/probe_shareholder_api{1,2,3,4}.py`），本功能无字段校准阻塞门禁。
业务层任务不得直接引用第三方 SDK、传输模型或供应商专有字段。

**MySQL 表结构**：本功能**不新建 MySQL 业务表**——股票身份读取复用
003 的 `stock_current`/`stock_provider_mapping`（`provider_mappings` 只读），审计复用
005 的 `market_data_sync_run/attempt/issue`（`DataKind` 枚举新增取值
`TOP10_HOLDERS`/`TOP10_FLOAT_HOLDERS`/`HOLDER_COUNT`）。
**结构性变更一项（2026-08-06 上线实测发现）**：`TOP10_FLOAT_HOLDERS` 18 字符超出
`market_data_sync_run.data_kind` String(16) 列宽，run 认领 INSERT 报
`DataError (1406)`——经迁移 006 加宽为 String(32)（宪章 VI 例外登记见
data-model.md §2.3）；枚举扩展包含在 T003。
ClickHouse `shareholder_holding`/`shareholder_count` 新表（分析型数据，宪章允许的
"外部引擎承载业务数据"情形）DDL 与验证包含在 T002/T004。

**流程拆分**：三个接口拆分为 3 套独立 Flow（增量 3 + 回补 3，用户显式要求），
任一接口失败只影响自身 run 终态；增量 Cron 错峰 `0/5/10 17 * * 1-5` 使三 Flow
串行执行（共享进程级节流器全局生效）；审计 `data_kind` 按接口取值；
水位按接口/kind 分别计算（两 top10 接口同表写入，表级水位会跳日，
shareholder-data-service.md §4-3）。

**组织方式**：按用户故事分组，使每个故事都能独立实现和测试。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## 路径约定

- **Single project**: `src/`, `tests/` at repository root（本功能按 plan.md 源代码树，
  Python 单项目结构）

## 阶段 1：初始化（共享基础设施）

**Purpose**: 配置、模型与数据库基础设施

- [x] T001 扩展 `src/lucking/config.py`：新增 `shareholder_data_*` 配置项
  （provider_code、timezone=Asia/Shanghai、log_dir/log_filename、
  fetch_deadline_seconds=1500、run_lease_seconds=2100、page_limit=6000、
  rate_limit_per_minute=400），并强制校验 lease_seconds > fetch_deadline_seconds
  （沿用 006/007 配置模式；页面限流 400/min 为用户显式指定）
- [x] T002 [P] 在 `src/lucking/clickhouse.py` 注册 `shareholder_holding` 与
  `shareholder_count` 两张表 DDL 与 `migrate` 命令：均
  `ReplacingMergeTree(updated_at)`、`PARTITION BY toYYYYMM(end_date)`；
  持仓表 `ORDER BY (end_date, stock_id, holder_kind, holder_name)`、
  股东人数表 `ORDER BY (end_date, stock_id)`；身份列 + 数据列
  （data-model.md §3.1/§3.2 全集：ann_date、holder_kind Enum8、holder_name、
  hold_amount Decimal(24,2)、hold_ratio/hold_float_ratio Decimal(12,4)、
  hold_change Decimal(24,2)、holder_type、holder_num UInt32 等，
  每列中文 COMMENT）、无 TTL
- [x] T003 [P] 在 `src/lucking/models/market_data.py` 增加 `DataKind.TOP10_HOLDERS`/
  `DataKind.TOP10_FLOAT_HOLDERS`/`DataKind.HOLDER_COUNT` 枚举值（纯枚举扩展，
  无表结构变更；与 005 每接口一取值模式一致）；新建
  `src/lucking/models/shareholder_data.py`：规范 DTO
  （ShareholderDataRequest、ProviderShareholderRecord 9 字段、
  ProviderShareholderCountRecord 4 字段、ShareholderDataSyncResult 含
  data_kind）与 `SHAREHOLDER_DATA_FIELDS` 白名单常量（top10 两接口 9 字段
  共用一组、`HOLDER_COUNT` 4 字段一组；字段全集已实测与文档逐名一致，
  research 决策 7）
- [x] T004 [P] 新建 `tests/integration/test_shareholder_data_schema.py`
  （按功能命名惯例）：验证 ClickHouse 两表 DDL——引擎
  `ReplacingMergeTree(updated_at)`、排序键（持仓 `(end_date, stock_id,
  holder_kind, holder_name)` / 人数 `(end_date, stock_id)`）、按月分区、
  每列非空中文注释、同键替换幂等语义（重复 INSERT 同键行后
  `SELECT ... FINAL` 只保留最新 `updated_at`）；并验证本功能未新增任何
  MySQL 表/结构变更（三个 `DataKind` 为纯枚举扩展，
  `SHOW CREATE TABLE market_data_sync_run` 与基线一致）

---

## 阶段 2：基础能力（阻塞性前置条件）

**Purpose**: 供应商无关 Port、注册中心与共享节流器复用验证——所有用户故事的前置

**⚠️ CRITICAL**: 本阶段完成前不得开始任何用户故事实现

- [x] T005 新建 `src/lucking/ports/shareholder_data_common.py`：
  `ShareholderDataProvider` Protocol（provider_code + fetch_top10_holders /
  fetch_top10_float_holders / fetch_holder_count(request, *, deadline)）、
  `ShareholderDataRequest`（date + holder_kind）、
  `ProviderShareholderBatch`/`ProviderShareholderCountBatch`；
  错误与 `RetrievalEvidence` 复用 `src/lucking/ports/market_data_common.py`
  （含 PROVIDER_RESPONSE_CAPPED；shareholder-data-provider.md §4/§5）；
  DTO 字段全集即白名单（ED-006）
- [x] T006 [P] 在 `src/lucking/integrations/registry.py` 增加
  `register_shareholder_data_provider`/`build_shareholder_data_provider`/
  `build_tushare_shareholder_data_provider`（依赖注入组装 client、
  共享 `RateLimiter`（`shareholder_data_rate_limit_per_minute=400`）、
  deadline 与 page_limit 配置）
- [x] T007 [P] 共享节流器复用验证：新建 `tests/unit/test_shareholder_data_rate_limit.py`——
  以 `shareholder_data_rate_limit_per_minute=400` 配置复用
  `src/lucking/integrations/tushare/rate_limiter.py` 的 `RateLimiter`：
  任意 60 秒窗口 ≤ 400 次、最小间隔 ≥ 150 毫秒（60/400）、
  `monotonic`/`sleep` 可注入（007 泛化参数化验证，无代码改动，
  research 决策 4）

**Checkpoint**: 基础能力就绪——用户故事实现可开始

---

## 阶段 3：用户故事 1 - 交易日股东数据增量同步（优先级：P1）🎯 MVP

**Goal**: 每个交易日错峰 17:00/17:05/17:10，三个接口各自按公告日全市场提取
新增披露数据（`top10_*` 用 ann_date、`stk_holdernumber` 用公告区间，
`has_more/offset` 分页至完整），经 003 身份解析后发布 ClickHouse 两表
并按接口完成 MySQL 审计终态；任一接口失败不影响其他两个（3 Flow 隔离）。

**Independent Test**: 分别触发 `前十大股东交易日同步`/`前十大流通股东交易日同步`/
`股东人数交易日同步` 三个 Deployment（显式 scheduled_at），预期：各接口 run
SUCCEEDED、ClickHouse 按 (end_date, stock_id[, holder_kind, holder_name])
可查（SELECT ... FINAL）、披露高峰日 page_count > 1 且 continuation_exhausted=True；
未知 ts_code 被隔离（invalid_count + issue UNKNOWN_STOCK_IDENTITY）不阻断整批；
重复触发同一 scheduled_at 幂等；先触发任一 top10 接口再触发另一个，
后者窗口仍覆盖当日公告（按 kind 水位不跳日）；模拟某接口来源失败，
其余两个接口仍 SUCCEEDED。非交易日触发 → SKIPPED_NOT_TRADING_DAY。

### 用户故事 1 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T008 [P] [US1] Provider 契约测试 `tests/contract/test_shareholder_data_provider.py`：
  白名单严格相等（set(row) != set(fields) 整批失败）、请求只含公告日且不传
  ts_code（top10 用 ann_date、holder_count 用 start_date=end_date）、
  `has_more=True` offset 续取与 `continuation_exhausted` 收尾、位置不前进/
  重复页/超最大页数 → PROVIDER_RESPONSE_CAPPED、错误映射全集、节流间隔
  ≥ 150 毫秒、重试退避 30/120/300 与 deadline 约束；三提取方法相互独立
  （单一方法抛错不影响其他两个，tushare-shareholder-data.md §7）
- [x] T009 [P] [US1] Service 契约测试（新建 `tests/unit/test_shareholder_data_service.py`，
  沿用仓库惯例：service 测试在 tests/unit/；含
  `tests/contract/shareholder_data_memory.py` 假 Provider 替身）：
  假 Provider 全流程——非交易日 SKIPPED、空水位窗口直接成功（不调用
  Provider）、身份未映射隔离跳过（UNKNOWN_STOCK_IDENTITY，不阻断整批）、
  完全重复去重、新公告（ann_date 更大）同键值变化按最新值更新计
  updated_count 不视为冲突、非新公告值变化 RECORD_CONFLICT 整批失败、
  公告日 0 行正常成功、重复同步 run_key 幂等（含接口维度）、失败不破坏
  已有数据、**按 kind 水位**（先同步 TOP10 再同步 TOP10_FLOAT 后者仍覆盖
  同日公告，替身调用计数断言）、**故障隔离**（A 接口抛错只写 A 的 FAILED
  终态，B/C 正常，shareholder-data-service.md §6）

### 用户故事 1 实现

- [x] T010 [P] [US1] 新建 `src/lucking/integrations/tushare/shareholder_data_provider.py`：
  TushareShareholderDataProvider——三个提取方法（top10 两接口共用
  `ann_date` 参数、holder_count 用 `start_date=end_date`，全市场不传
  ts_code）、字段白名单与映射（原样保留字段名）、`has_more/offset`
  分页续取（page_limit=6000、continuation_exhausted 收尾、重复页/位置
  不前进判定）、共享 RateLimiter（400/min）集成、瞬态重试 ≤ 3 次、
  错误映射（PROVIDER_RATE_LIMITED/QUOTA_EXCEEDED/...）
- [x] T011 [P] [US1] 新建 `src/lucking/repositories/shareholder_data_clickhouse.py`：
  watermark（按 kind 分别取 `max(ann_date) FINAL`：holder_kind='TOP10' /
  'TOP10_FLOAT' / shareholder_count 全表）、publish_batch（INSERT 前
  SELECT ... FINAL 读取同键既有行、按 ann_date 锚点计算
  added/updated/unchanged/conflict、单 block JSONEachRow 批量插入两表）、
  query_shareholder_holdings/query_shareholder_count（FINAL、排序、limit/offset）
- [x] T012 [US1] 新建 `src/lucking/services/shareholder_data.py`：
  ShareholderDataService——`sync_top10_holders`/`sync_top10_float_holders`/
  `sync_holder_count` 三个入口（交易日判断 → run_key 认领/租约（DATA_KIND
  按接口）→ 水位/窗口（水位+1 → 昨日）→ Provider 提取 → 003
  `provider_mappings` 身份解析（只读，复用 `src/lucking/repositories/
  stock_list.py`）→ 批次校验与修订/冲突判定 → 发布 → 同一 MySQL 事务写
  attempt/run 终态）；复用 `src/lucking/repositories/market_data.py`
  审计仓储，data_kind 按接口（shareholder-data-service.md §4 行为 1~10；
  依赖 T010/T011）
- [x] T013 [US1] 新建 `src/lucking/flows/shareholder_data.py`：
  三个增量 Flow **`前十大股东交易日同步`**/**`前十大流通股东交易日同步`**/
  **`股东人数交易日同步`**（retries=0；scheduled_at 从
  prefect.runtime.flow_run.scheduled_start_time 读取、直接调用必须显式提供；
  schedule_slug 校验（ASCII：top10-holders-sync/top10-floatholders-sync/
  holder-count-sync）；非交易日 SKIPPED 成功结束；日志白名单不含
  Token/签名/完整请求体，prefect-flow.md §1）
- [x] T014 [US1] 在 `prefect.yaml` 增加三个增量 Deployment
  `前十大股东交易日同步/前十大股东交易日同步`（Cron `0 17 * * 1-5`）、
  `前十大流通股东交易日同步/前十大流通股东交易日同步`（Cron `5 17 * * 1-5`）、
  `股东人数交易日同步/股东人数交易日同步`（Cron `10 17 * * 1-5`）：
  work pool local-pool、concurrency_limit 1 + collision_strategy ENQUEUE、
  参数 schedule_slug 分别对应（中文部署名 + ASCII slug 双轨；
  错峰使三 Flow 串行执行，research 决策 6）
- [x] T015 [US1] 集成测试 `tests/integration/test_shareholder_data_sync.py`
  （-m mysql）：真实 MySQL+ClickHouse 端到端——认领幂等（重复 scheduled_at
  不重复处理）、发布计数正确、更正公告修订（updated_count）不触发冲突、
  失败终态不破坏已有数据、租约过期 ABANDONED 可重开、三接口同交易日
  各自独立 run（互不覆盖）

**Checkpoint**: 用户故事 1 独立可测——MVP 完成，可部署演示

---

## 阶段 4：用户故事 2 - 初始化历史回补（优先级：P2）

**Goal**: 通过三个人工回补 Flow 从 2024-01-01 回补至当前增量（`top10_*`
按报告期季度末、`stk_holdernumber` 按公告日），全程遵守每分钟 400 次
限流，逐日独立终态、已成功日期跳过、失败日期可安全重试；三接口回补
相互独立。

**Independent Test**: 分别触发 `前十大股东历史回补`/`前十大流通股东历史回补`/
`股东人数历史回补`（start/end/backfill_batch_id），预期：各接口逐日独立
终态、请求间隔 ≥ 150 毫秒（≤ 400 次/分钟）；重复提交同 batch_id 已成功
日期 SKIP（替身调用计数不增加）；非法区间（未来/反向/早于 2024-01-01）
整体拒绝；`top10_*` 仅季度末日期调用来源（替身计数断言）；
与增量重叠日期同键幂等；任一接口回补失败不影响其他两个。

### 用户故事 2 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T016 [P] [US2] Service 回补契约测试（新建 `tests/unit/test_shareholder_data_backfill.py`，
  与 T009 分文件避免并行冲突）：
  区间校验拒绝（未来日期、反向区间、起点 < 2024-01-01）、逐日 resolve
  （START/SKIP_SUCCEEDED/RETRY/IN_PROGRESS，键含接口 DATA_KIND）、
  已成功日期不重复调用 Provider（替身调用计数断言）、失败日期重试只处理
  失败日期、`top10_*` 回补仅季度末日期触发提取（替身调用次数断言，
  shareholder-data-service.md §4 行为 11）

### 用户故事 2 实现

- [x] T017 [US2] 在 `src/lucking/services/shareholder_data.py` 实现
  `backfill_top10_holders`/`backfill_top10_float_holders`/`backfill_holder_count`
  三个入口：区间整体校验（BACKFILL_START=2024-01-01、无未来日期、
  start ≤ end）→ 逐日展开（top10 接口仅季度末日期调用）→ 逐日按
  backfill run_key 认领与终态（复用 T012 链路；依赖 T012）
- [x] T018 [US2] 在 `src/lucking/flows/shareholder_data.py` 实现
  三个回补 Flow **`前十大股东历史回补`**/**`前十大流通股东历史回补`**/
  **`股东人数历史回补`**（start_date/end_date/backfill_batch_id 参数、
  retries=0），并在 `prefect.yaml` 增加三个人工 Deployment（无 schedule；
  依赖 T017；**同文件 `flows/shareholder_data.py` 与 `prefect.yaml`
  分别紧随 T013/T014 顺序执行，勿并行**）
- [x] T019 [US2] 集成测试 `tests/integration/test_shareholder_data_backfill.py`
  （-m mysql）：回补与增量重叠日期幂等（无重复记录）、中断恢复（部分成功
  后再跑只补失败日期）、节流间隔实测 ≥ 150 毫秒、三接口回补互不影响
  （依赖 T017/T018）

**Checkpoint**: 用户故事 1 与 2 均独立可用

---

## 阶段 5：用户故事 3 - 识别同步失败和数据质量问题（优先级：P3）

**Goal**: 限流、超时、空响应、字段缺失、冲突与不完整结果均产生可识别的
终态、计数、issue 类别与脱敏摘要；运维可在 5 分钟内判断状态与处置方式。

**Independent Test**: 注入限流/超时/空响应/缺失字段/非新公告冲突/分页不完整
场景，预期：每个场景的 run 终态、计数、issue 类别（PROVIDER_RATE_LIMITED/
PROVIDER_TIMEOUT/PROVIDER_RESPONSE_CAPPED/RECORD_CONFLICT/...）准确；
更正公告更新场景计 updated 不产生告警；已有有效数据不被清空；
日志与 issue 不含 Token/原始行（脱敏断言）；**任一接口的失败场景只影响
该接口**。

### 用户故事 3 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T020 [P] [US3] 失败路径契约测试（新建 `tests/unit/test_shareholder_data_failure.py`，
  与 T009/T016 分文件避免并行冲突）：
  限流/超时重试耗尽 → FAILED + 计数；空响应（单公告日 0 行）正常成功 vs
  提取中断失败区分；字段缺失跳过 + 全无效失败；非新公告冲突整批失败
  （新公告修订不失败）；分页不完整（位置不前进/重复页/超页数）失败；
  所有失败不破坏已有数据；A 接口失败不影响 B/C 接口
  （spec FR-012~FR-015、ED-005）

### 用户故事 3 实现

- [x] T021 [P] [US3] issue 记录与脱敏：复用 `market_data_sync_issue`
  （attempt_id 关联；问题类别沿用 005 全集——`UNKNOWN_STOCK_IDENTITY`
  已存在，无需新增类别，本功能仅消费；safe_summary/payload_hash/
  provider_security_id_hash，禁止 Token/连接串/原始行；data-model.md §2.2）
- [x] T022 [US3] 可观测性：JsonlLogStore 接入（shareholder_data_log_dir/
  filename、白名单字段、10MiB 轮转 ×5）、窗口及时性计算（17:00 错峰启动
  当日终态）、错误摘要不泄漏敏感配置（NFR-005/NFR-006；依赖 T021）
- [x] T023 [US3] 运维验证：集成测试 `tests/integration/test_shareholder_data_observability.py`
  （-m mysql）断言 issue 脱敏与日志白名单；按 quickstart.md §7 五分钟排障
  步骤核对 run/attempt/issue/日志/ClickHouse 五步定位可执行
  （data_kind 按接口定位；依赖 T022）

**Checkpoint**: 三个用户故事全部独立可用

---

## 阶段 6：完善与横切关注点

**Purpose**: 替换性证明、质量门禁与上线验证

- [x] T024 [P] 替代实现验证：以第二 Provider 实现（`tests/contract/shareholder_data_memory.py`
  测试替身）重跑 T008/T009/T016/T020 契约测试，证明换源不改业务代码
  （ED-006/ED-007）；结果记录至 research.md
- [x] T025 [P] 质量门禁：`uv run ruff check .`、`uv run mypy --strict src`、
  `uv run pytest`、`uv run pytest -m mysql` 全量通过（含 006/007 既有测试，
  证明共享 RateLimiter 复用无回归）
- [x] T026 [P] quickstart.md 验证：§2 启动依赖、§3 增量（三个 Deployment）、
  §4 回补与幂等（三个 Deployment）、§5 非交易日、§6 失败与恢复
  （含故障隔离）逐项执行并记录结果
- [x] T027 文档一致性核对：spec/plan/research/data-model/contracts/quickstart
  六份文档交叉一致（3 Flow 拆分、按接口 data_kind 与水位、错峰调度），
  无遗留待澄清标记
- [x] T028 上线门禁实测并回填 research.md「部署前待验证项」4~8（已实测
  1~3 ✅：2026-08-05 无需 ts_code 全市场查询 + limit 生效 + 昨日 0 行属
  正常披露节奏；字段全集 9+9+4 与文档逐名一致；单次上限 6,000 行 +
  has_more/offset 分页有效、stk_holdernumber 文档 3,000 上限过时）。
  待实测 4~8：400 次/分钟限流实际行为与错误码、003 映射对三接口返回
  ts_code 覆盖度、各接口回补耗时（top10 ~90 次秒级 / 股东人数 ~630 次
  约 2 分钟）、更正公告收敛形态、错峰调度串行无叠加——需部署账户在
  交易日窗口执行，结果回填 research.md）

---

## 依赖与执行顺序

### Phase Dependencies

- **Setup（阶段 1）**: 无依赖，可立即开始
- **Foundational（阶段 2）**: 依赖阶段 1 完成——阻塞所有用户故事
  （接口分页/字段已实测确认，无字段校准阻塞门禁）
- **用户故事（阶段 3+）**: 依赖阶段 2 完成
  - US1 → US2 → US3 顺序推进（US2/US3 复用 US1 的 Service 链路与 Provider，
    见 User Story Dependencies；有足够人力时 US3 测试可并行准备）
- **Polish（阶段 6）**: 依赖所有用户故事完成

### User Story Dependencies

- **User Story 1（P1）**: 阶段 2 后可开始，无其他故事依赖
  （US1 内含三个接口的三条独立同步链路，彼此隔离）
- **User Story 2（P2）**: 依赖 US1 的 Service 命令链路（T012）与 Provider
  （T010）——回补按日复用同一链路；不可在 US1 之前独立完成
- **User Story 3（P3）**: 依赖 US1（审计终态/issue 链路）与 US2（回补失败
  路径）；在 US1 完成后即可并行推进其测试准备

### Within Each User Story

- 测试必须先写并使它们 FAIL，再开始实现
- 模型/仓储 → 服务 → Flow/Deployment → 集成验证
- 每个故事完成后再推进下一个优先级

### Parallel Opportunities

- 阶段 1 的 T002/T003/T004 均可并行（不同文件）；T001 先行（配置）
- 阶段 2 的 T006/T007 可并行；T005 先行（Port 定义）
- 用户故事内的测试任务（T008/T009）可并行；实现任务 T010/T011 可并行，
  T012 依赖二者
- 不同用户故事由不同成员推进时，US1 完成前 US3 的测试（T020）与
  US2 的测试（T016）可先写
- US1 内三个接口共享同一 Provider/Service/Repository 文件——
  三条链路的差异集中在命令入口与 Flow，实现不拆人并行

---

## Parallel Example: User Story 1

```bash
# 契约测试先行（先失败后实现；分页/字段行为已由实测确认）：
Task: "Provider 契约测试 tests/contract/test_shareholder_data_provider.py"
Task: "Service 契约测试 tests/unit/test_shareholder_data_service.py"

# 实现可并行启动：
Task: "TushareShareholderDataProvider src/lucking/integrations/tushare/shareholder_data_provider.py"
Task: "ClickHouse 仓储 src/lucking/repositories/shareholder_data_clickhouse.py"

# 汇总：
Task: "ShareholderDataService src/lucking/services/shareholder_data.py（依赖以上两者）"
Task: "三个增量 Flow src/lucking/flows/shareholder_data.py + prefect.yaml（依赖 Service）"
```

---

## 实施策略

### MVP First（用户故事 1）

1. 完成阶段 1：初始化
2. 完成阶段 2：基础能力（CRITICAL - 阻塞所有故事；分页/字段已实测确认）
3. 完成阶段 3：用户故事 1（三个接口各自独立的增量同步链路）
4. **STOP and VALIDATE**: 独立测试用户故事 1（T008~T015 + 契约/集成测试，
   含故障隔离与按 kind 水位用例）
5. 上线门禁实测（T028 中增量相关项）后可部署演示

### Incremental Delivery

1. Setup + Foundational → 基础就绪（分页/字段实测确认）
2. 用户故事 1 → 独立测试 → 部署演示（MVP：三个接口增量同步，互不影响）
3. 用户故事 2 → 独立测试 → 完成初始化回补（三个回补 Flow）
4. 用户故事 3 → 独立测试 → 失败可诊断
5. 阶段 6 → 替换性证明与质量门禁 → 上线

### Parallel Team Strategy

1. 团队共同完成阶段 1、2
2. 阶段 2 完成后：成员 A 推进 US1 实现；成员 B 可先行编写 US3 测试
   （T020）与 US2 测试（T016）
3. US1 完成后：成员 B 推进 US2，成员 C 推进 US3
4. 各故事独立集成验证（三接口隔离性由契约/集成测试持续守护）

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 每个用户故事独立可完成、可测试
- 验证测试先失败再实现（宪章 III：缺陷修复必须先有失败测试）
- 每个任务或逻辑组完成后提交
- 任一 Checkpoint 处可停下独立验证故事
- 避免：模糊任务、同文件冲突、破坏独立性的跨故事依赖
- 三接口拆分（用户显式要求）贯穿所有阶段：Flow/Deployment/run_key/
  data_kind/水位/终态均按接口独立；共享 Provider/Service/Repository 文件
  是实现层复用，不构成接口间耦合

## Phase 7: Convergence

- [x] T029 交易日窗口内实跑 quickstart.md §3~§6 并记录结果：§3 增量同步
  （触发 `前十大股东交易日同步`/`前十大流通股东交易日同步`/
  `股东人数交易日同步`，显式 scheduled_at，验证三接口独立终态与按 kind
  水位不跳日）、§4 回补与幂等（`前十大股东历史回补` 等三个 Deployment，
  串行执行）、§5 非交易日 SKIPPED、§6 失败与恢复（含故障隔离）；
  结果回填 quickstart.md 或任务注释 per T026/quickstart.md（partial）
- [x] T030 交易日窗口内执行 research.md 部署前待验证项 4/7/8 实测并回填
  （5 已由 T028 覆盖、6 由 T029 §4 实跑覆盖）：4）400 次/分钟限流实际
  行为与错误码、7）更正公告收敛形态（同一业务身份多个 ann_date 按最新
  值收敛不冲突）、8）错峰调度串行执行无叠加 per T028/research.md（partial）

## Phase 8: Convergence

- [x] T031 真实账户冒烟门禁（上线前执行，依赖 T010/T012 已实现链路）：
  拉取最近一个有披露的公告日，经 TushareShareholderDataProvider →
  ShareholderDataService → ClickHouse 两表全链路发布（三个接口各一次）；
  断言：白名单与响应字段逐名一致（无"字段集合不精确"）、
  hold_amount 大数（70 亿股级）正常落库（无 Decimal 溢出）、
  received/added 计数与行数一致、`has_more` 收尾（continuation_exhausted）
  无截断；失败类别记录于 issue 表 per T025/宪章 III（partial）

---

## 阶段 9：限流强化——账户级共享预算（Redis 分布式节流，2026-08-06 用户澄清补充）

**Purpose**: 用户澄清 400/min 是**账户级共享预算**（三个接口请求合计，非每接口各 400）；
3 Flow 拆分后多进程并发（回补与增量同跑）必须跨进程共享同一预算

- [x] T032 新建 `src/lucking/integrations/tushare/redis_rate_limiter.py`：
  RedisRateLimiter——Redis ZSET 滑窗 + Lua 原子判定（清理过期 → 计数 →
  判定 → 记录），任意 60 秒窗口跨进程合计 ≤ 400 次、最小间隔 ≥ 150 毫秒；
  与进程级 `RateLimiter` 共用 `Throttle` Protocol 契约（rate_limiter.py）；
  Redis 不可达降级进程级限流（fail-open）并上报
  `shareholder_rate_limiter_degraded` 事件
- [x] T033 [P] `src/lucking/config.py` 新增 `shareholder_data_rate_limiter`
  （redis/process，默认 redis）、`redis_url`、`redis_password`（SecretStr）
  及校验；`src/lucking/integrations/registry.py` 组装
  `build_tushare_shareholder_data_provider` 时按配置注入 RedisRateLimiter
  （Provider 新增 `limiter: Throttle | None` 参数，未注入回退进程级）
- [x] T034 [P] 单元测试 `tests/unit/test_shareholder_data_redis_rate_limit.py`：
  两个实例（模拟两进程）共享账户预算（合计 ≤ rate/窗口）、最小间隔
  ≥ 60/rate、窗口滑出后放行、Redis 不可达降级不抛异常（依赖本地
  compose Redis，不可达时跳过）
- [x] T035 文档语义修正：spec FR-005/边界情况/假设、research 决策 4（重写）
  与决策 6、contracts/tushare §5 与 prefect-flow §2/§4、plan 摘要/约束/
  复杂度登记、quickstart 配置块——统一为"账户级共享预算，三接口请求合计
  ≤ 400 次/分钟"；错峰调度降级为运维友好（正确性由 Redis 分布式保证）

## Phase 10: Convergence

- [x] T036 CRITICAL 将两项行为变更回填规格文档：①计划增量窗口最多回看
  30 天（`shareholder_data_window_lookback_days`，2026-08-06 实测空表
  612 天积压超出截止时间）②批内同日重复披露隔离（`DUPLICATE_ANN_DISCLOSURE`，
  保留首见、后见计质量问题不整批失败）——按宪章 I"先更新规格再调整实现"
  补齐 spec.md（边界情况/FR-010/假设）与 plan.md（约束/复杂度跟踪）的
  治理记录 per 宪章 I/FR-002/FR-010（contradicts）
- [x] T037 CRITICAL 完成 `market_data_sync_run.data_kind` 结构性变更治理：
  migration 006（String(16)→32，实测 `TOP10_FLOAT_HOLDERS` 18 字符溢出
  DataError）已应用——按宪章 VI 更正 plan.md 宪章检查"MySQL 表结构"段、
  tasks.md 头部"MySQL 表结构"说明、data-model.md §2.3/§3 与 quickstart
  中"无 Alembic 迁移、无列变更、宪章 VI 不适用"的错误表述，并逐表记录
  例外字段、业务理由、唯一性保障与迁移影响 per 宪章 VI/plan: MySQL 表结构（contradicts）
- [x] T038 完成 quickstart.md §3~§6 实跑结果回填：2026-08-06 已实跑
  §3 三个增量 Deployment（17:00/17:05/17:10 错峰，含窗口回看上限实际
  表现与同日重复披露隔离场景）与 §4 三个回补（repair-* 批次，逐日
  独立终态）、§6 失败与恢复（PROVIDER_DEADLINE/DataError/RECORD_CONFLICT
  分类处置），按 T029 要求记录结果 per T029/quickstart.md §3~§6（partial）
- [x] T039 完成 research.md 部署前待验证项 4/7/8 实测回填：4）账户级
  共享限流实际行为（2026-08-06 top10 空表 612 天积压 + 网络错误于 25
  分钟截止触发 PROVIDER_DEADLINE，Redis 限流器全程未出现限流拒绝）；
  7）更正公告收敛形态（实测同日重复披露温一峰 3709894.0 vs 3709912.0
  → `DUPLICATE_ANN_DISCLOSURE` 隔离，跨日更正收敛由 updated 逻辑覆盖）；
  8）错峰调度时间线（17:00 超时失败、17:05 失败、17:10 成功，三 Flow
  无并发叠加） per T030/research.md（partial）
- [x] T040 完成真实账户冒烟门禁结果记录：2026-08-06 已通过三接口全链路
  真实发布（top10_holders/top10_floatholders/stk_holdernumber →
  ShareholderDataService → ClickHouse，holding 520 行 / count 644 行，
  白名单一致、`has_more` 收尾、hold_amount 无 Decimal 溢出），按 T031
  补记断言结论至 research.md/quickstart §8 per T031/宪章 III（partial）

## Phase 11: Convergence

- [x] T041 同步三处旧语义表述与已实现行为一致：①`plan.md` 摘要段——增量
  窗口定义补"最多回看 30 天（`shareholder_data_window_lookback_days`）"、
  修订 vs 冲突句补"同日重复披露隔离（`DUPLICATE_ANN_DISCLOSURE`）"；
  ②`contracts/shareholder-data-service.md` §4-3 窗口定义（"窗口 =（水位,
  目标日前一自然日]"）补回看上限；③`quickstart.md` §3 预期句（"每接口
  窗口 =（水位, 昨日]"）补回看上限 per plan: 摘要/窗口与修订、ED-004（partial）

## Phase 12: Convergence

- [x] T042 更正两处残留旧表述与宪章 VI 治理登记一致：①`plan.md` 实施阶段 1
  （"config 扩展（无 Alembic 迁移）"）改为注明迁移 006（`market_data_sync_run.
  data_kind` 加宽，例外登记见 data-model §2.3）；②`data-model.md` 需求追溯表
  "无新 MySQL 表（宪章 VI 不适用）"改为"无新 MySQL 表；审计表一项结构性变更
  （迁移 006）已按宪章 VI 登记（§2.3）" per plan: 实施阶段 1、data-model: 需求追溯表（partial）
