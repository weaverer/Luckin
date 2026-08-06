# 任务：股票技术面因子交易日同步（007-sync-stock-factors）

**输入**：`/specs/007-sync-stock-factors/` 中的设计文档

**前置条件**：plan.md（必需）、spec.md（用户故事必需）、research.md、data-model.md、contracts/

> 任务文档必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**Tests**: Tests are REQUIRED for changes to observable behavior, public contracts, data
models, or failure handling. Defect fixes require a failing regression test first. Include
contract/integration tests for interface changes and focused end-to-end tests for critical flows.

**第三方数据集成**：本功能通过 Tushare `stk_factor_pro` 获取数据，任务已覆盖供应商无关
端口与规范化模型（T003/T005）、独立供应商适配器（T011）、配置/依赖注入（T001/T007）、
契约测试（T009/T010/T017/T021），以及替代适配器或测试替身（T025）。
业务层任务不得直接引用第三方 SDK、传输模型或供应商专有字段。

**MySQL 表结构**：本功能**不新建、不结构性修改任何 MySQL 业务表**——股票身份读取复用
003 的 `stock_current`/`stock_provider_mapping`（`provider_mappings` 只读），审计复用
005 的 `market_data_sync_run/attempt/issue`（仅 `DataKind` 枚举新增取值，无列变更）；
因此无 Alembic 迁移任务，`DataKind.STOCK_FACTOR` 枚举扩展包含在 T003。
ClickHouse `stock_factor` 新表（分析型数据，宪章允许的“外部引擎承载业务数据”情形）
DDL 与验证包含在 T002/T004。

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

- [x] T001 扩展 `src/lucking/config.py`：新增 `stock_factor_*` 配置项
  （provider_code、timezone=Asia/Shanghai、log_dir/log_filename、
  fetch_deadline_seconds=1500、run_lease_seconds=2100、page_limit=10000、
  rate_limit_per_minute=30），并强制校验 lease_seconds > fetch_deadline_seconds
- [x] T002 [P] 在 `src/lucking/clickhouse.py` 注册 `stock_factor` 表 DDL 与
  `migrate` 命令：`ReplacingMergeTree(updated_at)`、`ORDER BY (trade_date, stock_id)`、
  `PARTITION BY toYYYYMM(trade_date)`、身份列 + 行情/估值/技术指标及全部复权变体
  数据列（data-model.md §3 全集，字段名原样保留含 `_bfq/_qfq/_hfq` 后缀，
  每列中文 COMMENT）、无 TTL
- [x] T003 [P] 在 `src/lucking/models/market_data.py` 增加 `DataKind.STOCK_FACTOR`
  枚举值（纯枚举扩展，无表结构变更）；新建 `src/lucking/models/stock_factor.py`：
  规范 DTO（StockFactorRequest、ProviderStockFactorRecord 全集、
  RetrievalEvidence、ProviderStockFactorBatch、StockFactorSyncResult）与
  `STOCK_FACTOR_FIELDS` 白名单常量（`(field_name, revision_allowed)` 元数据，
  可修订 = 字段名含 `_qfq/_hfq` 后缀者 + adj_factor，research 决策 7；
  初始按文档分组清单编写，T008 实测后校准）
- [x] T004 [P] 新建 `tests/integration/test_stock_factor_schema.py`（按功能命名惯例）：
  验证 ClickHouse `stock_factor` 表 DDL——引擎 `ReplacingMergeTree(updated_at)`、
  排序键 `(trade_date, stock_id)`、按月分区、每列非空中文注释、同键替换幂等语义
  （重复 INSERT 同键行后 `SELECT ... FINAL` 只保留最新 `updated_at`）；
  并验证本功能未新增任何 MySQL 表/结构变更（`DataKind.STOCK_FACTOR` 为
  纯枚举扩展，`SHOW CREATE TABLE market_data_sync_run` 与基线一致）

---

## 阶段 2：基础能力（阻塞性前置条件）

**Purpose**: 供应商无关 Port、共享节流器、注册中心与字段实测门禁——所有用户故事的前置

**⚠️ CRITICAL**: 本阶段完成前不得开始任何用户故事实现

- [x] T005 新建 `src/lucking/ports/stock_factor_common.py`：`StockFactorProvider`
  Protocol（provider_code + fetch_stock_factors(request, *, deadline)）、
  `StockFactorRequest`（target_trade_date）、`ProviderStockFactorBatch`；
  错误复用 `src/lucking/ports/market_data_common.py` 的 ProviderError 家族
  （含 PROVIDER_RESPONSE_CAPPED）；DTO 字段全集即白名单（ED-005）
- [x] T006 [P] 节流器泛化：新建 `src/lucking/integrations/tushare/rate_limiter.py`
  （类名 `RateLimiter`，迁移 `index_rate_limiter.py` 现有实现：任意 60 秒窗口
  ≤ 30 次真实请求、最小间隔 2 秒、`monotonic`/`sleep` 可注入、线程安全）；
  `src/lucking/integrations/tushare/index_rate_limiter.py` 仅保留
  `IndexRateLimiter = RateLimiter` 兼容别名；006 既有测试
  `tests/unit/test_index_rate_limiter.py` 全量通过作为回归证明
- [x] T007 [P] 在 `src/lucking/integrations/registry.py` 增加
  `register_stock_factor_provider`/`build_stock_factor_provider`/
  `build_tushare_stock_factor_provider`（依赖注入组装 client、共享 RateLimiter、
  deadline 与 page_limit 配置）
- [x] T008 [P] 上线前门禁（US1 契约测试前完成，依赖 T003 白名单常量）：部署账户
  实测 `stk_factor_pro` 按 `trade_date` 单次全量请求的字段全集——各指标复权变体
  （_bfq/_qfq/_hfq）返回规律、open/high/low/close 原值与变体并存形态、
  pct_chg/pre_close/adj_factor 语义；据此校准 `STOCK_FACTOR_FIELDS` 白名单
  （含可修订/稳定分级）并同步 data-model.md §3（research 待验证项 2；
  若与文档分组清单不符，先修订 data-model.md §3 与 T003 模型再继续）

**Checkpoint**: 基础能力就绪（含字段全集实测校准）——用户故事实现可开始

---

## 阶段 3：用户故事 1 - 交易日股票技术面因子增量同步（优先级：P1）🎯 MVP

**Goal**: 每个交易日北京时间 17:00，按交易日提取全部 A 股（三所）技术面因子
（行情/估值/技术指标及全部复权变体），经 003 身份解析后发布 ClickHouse
`stock_factor` 并完成 MySQL 审计终态；复权字段回溯更新按来源最新值落库。

**Independent Test**: 对最近一个交易日触发 `股票技术面因子交易日同步/股票技术面因子交易日同步`
（显式 scheduled_at），预期：received = 该日全部 A 股、run SUCCEEDED、
`stock_factor` 按 (trade_date, stock_id) 可查（SELECT ... FINAL）、复权变体列
有值；未知 ts_code 被隔离（invalid_count + issue UNKNOWN_STOCK_IDENTITY）
不阻断整批；重复触发同一 scheduled_at 幂等（第二次不重复处理）。
非交易日触发 → SKIPPED_NOT_TRADING_DAY。

### 用户故事 1 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US1] Provider 契约测试 `tests/contract/test_stock_factor_provider.py`：
  白名单严格相等（set(row) != set(fields) 整批失败，含可修订分级元数据完整）、
  请求只含 trade_date、触顶 10,000 行 → PROVIDER_RESPONSE_CAPPED、错误映射全集、
  节流间隔 ≥ 2 秒、重试退避 30/120/300 与 deadline 约束（tushare-stock-factor.md §7；
  白名单以 T008 实测校准后的 `STOCK_FACTOR_FIELDS` 为准）
- [x] T010 [P] [US1] Service 契约测试（新建 `tests/unit/test_stock_factor_service.py`，
  沿用仓库惯例：service 测试在 tests/unit/）：
  假 Provider 全流程——非交易日 SKIPPED、身份未映射隔离跳过
  （UNKNOWN_STOCK_IDENTITY，不阻断整批）、完全重复去重、
  可修订字段（_qfq/_hfq/adj_factor）差异按最新值更新计 updated_count 不视为冲突、
  稳定字段差异 RECORD_CONFLICT 整批失败、全市场空响应失败 vs 个别股票无数据成功、
  重复同步 run_key 幂等、失败不破坏已有数据（stock-factor-service.md §6）

### 用户故事 1 实现

- [x] T011 [P] [US1] 新建 `src/lucking/integrations/tushare/stock_factor_provider.py`：
  TushareStockFactorProvider——`stk_factor_pro` 调用（trade_date 参数）、
  字段白名单与映射（原样保留 `_bfq/_qfq/_hfq` 后缀，含可修订分级）、
  共享 RateLimiter 集成、瞬态重试 ≤ 3 次、错误映射
  （PROVIDER_RATE_LIMITED/QUOTA_EXCEEDED/...）、10,000 行触顶完整性门禁
- [x] T012 [P] [US1] 新建 `src/lucking/repositories/stock_factor_clickhouse.py`：
  publish_batch（INSERT 前 SELECT ... FINAL 读取同键既有行、按可修订/稳定
  分级计算 added/updated/unchanged、单 block JSONEachRow 批量插入）、
  query_stock_factors（FINAL、按 (trade_date, stock_id) 排序、limit/offset）
- [x] T013 [US1] 新建 `src/lucking/services/stock_factor.py`：
  StockFactorService——ScheduledStockFactorSyncCommand 处理（交易日判断 →
  run_key 认领/租约 → Provider 提取 → 003 `provider_mappings` 身份解析
  （只读，复用 `src/lucking/repositories/stock_list.py`）→ 批次校验与分级
  冲突判定 → 发布 → 同一 MySQL 事务写 attempt/run 终态）；复用
  `src/lucking/repositories/market_data.py` 审计仓储，
  data_kind='STOCK_FACTOR'（stock-factor-service.md §4 行为 1~8）
- [x] T014 [US1] 集成测试 `tests/integration/test_stock_factor_sync.py`（-m mysql）：
  真实 MySQL+ClickHouse 端到端——认领幂等（重复 scheduled_at 不重复处理）、
  发布计数正确、复权修订更新（updated_count）不触发冲突、
  失败终态不破坏已有数据、租约过期 ABANDONED 可重开
- [x] T015 [US1] 新建 `src/lucking/flows/stock_factor.py`：
  Flow **`股票技术面因子交易日同步`**（retries=0；scheduled_at 从
  prefect.runtime.flow_run.scheduled_start_time 读取、直接调用必须显式提供；
  schedule_slug 校验（ASCII，如 stock-factor-sync）；非交易日 SKIPPED 成功
  结束；日志白名单不含 Token/签名/完整请求体，prefect-flow.md §1）
- [x] T016 [US1] 在 `prefect.yaml` 增加 Deployment
  `股票技术面因子交易日同步/股票技术面因子交易日同步`：Cron `0 19 * * 1-5`（Asia/Shanghai）、
  work pool local-pool、concurrency_limit 1 + collision_strategy ENQUEUE、
  参数 schedule_slug=stock-factor-sync（中文部署名 + ASCII slug 双轨）

**Checkpoint**: 用户故事 1 独立可测——MVP 完成，可部署演示

---

## 阶段 4：用户故事 2 - 初始化历史回补（优先级：P2）

**Goal**: 通过人工回补 Flow 从 2024-01-01 逐交易日回补至当前增量，全程遵守
每分钟 30 次限流，逐日独立终态、已成功日期跳过、失败日期可安全重试。

**Independent Test**: 触发 `股票技术面因子历史回补/股票技术面因子历史回补`
（start/end/backfill_batch_id），预期：逐日独立终态、请求间隔 ≥ 2 秒
（≤ 30 次/分钟）；重复提交同 batch_id 已成功日期 SKIP（替身调用计数不增加）；
非法区间（未来/反向/早于 2024-01-01）整体拒绝；与增量重叠日期同键幂等。

### 用户故事 2 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T017 [P] [US2] Service 回补契约测试（新建 `tests/unit/test_stock_factor_backfill.py`，
  与 T010 分文件避免并行冲突）：
  区间校验拒绝（未来日期、反向区间、起点 < 2024-01-01）、逐日 resolve
  （START/SKIP_SUCCEEDED/RETRY/IN_PROGRESS）、已成功日期不重复调用 Provider
  （替身调用计数断言）、失败日期重试只处理失败日期
  （stock-factor-service.md §4 行为 9）

### 用户故事 2 实现

- [x] T018 [US2] 在 `src/lucking/services/stock_factor.py` 实现
  BackfillStockFactorCommand 处理：区间整体校验（BACKFILL_START=2024-01-01、
  无未来日期、start ≤ end）→ 交易日历逐日展开 → 逐日按 backfill run_key
  认领与终态（复用 T013 链路；依赖 T013）
- [x] T019 [US2] 在 `src/lucking/flows/stock_factor.py` 实现
  Flow **`股票技术面因子历史回补`**（start_date/end_date/backfill_batch_id
  参数、retries=0），并在 `prefect.yaml` 增加人工 Deployment
  `股票技术面因子历史回补/股票技术面因子历史回补`（无 schedule；依赖 T018）
- [x] T020 [US2] 集成测试 `tests/integration/test_stock_factor_backfill.py`（-m mysql）：
  回补与增量重叠日期幂等（无重复记录）、中断恢复（部分成功后再跑只补失败日期）、
  节流间隔实测 ≥ 2 秒（依赖 T018/T019）

**Checkpoint**: 用户故事 1 与 2 均独立可用

---

## 阶段 5：用户故事 3 - 识别同步失败和数据质量问题（优先级：P3）

**Goal**: 限流、超时、空响应、字段缺失、冲突与不完整结果均产生可识别的终态、
计数、issue 类别与脱敏摘要；运维可在 5 分钟内判断状态与处置方式。

**Independent Test**: 注入限流/超时/空响应/缺失字段/稳定字段冲突/触顶场景，
预期：每个场景的 run 终态、计数、issue 类别（PROVIDER_RATE_LIMITED/
PROVIDER_TIMEOUT/PROVIDER_RESPONSE_CAPPED/RECORD_CONFLICT/...）准确；
复权字段更新场景计 updated 不产生告警；已有有效数据不被清空；
日志与 issue 不含 Token/原始行（脱敏断言）。

### 用户故事 3 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T021 [P] [US3] 失败路径契约测试（新建 `tests/unit/test_stock_factor_failure.py`，
  与 T010/T017 分文件避免并行冲突）：
  限流/超时重试耗尽 → FAILED + 计数；空响应 vs 个别无数据区分；字段缺失跳过 +
  全无效失败；稳定字段冲突整批失败（复权字段更新不失败）；触顶不完整失败；
  所有失败不破坏已有数据（spec FR-012~FR-015、ED-004）

### 用户故事 3 实现

- [x] T022 [P] [US3] issue 记录与脱敏：复用 `market_data_sync_issue`
  （attempt_id 关联；问题类别沿用 005 全集——`UNKNOWN_STOCK_IDENTITY`
  已存在，无需新增类别，本功能仅消费；safe_summary/payload_hash/
  provider_security_id_hash，禁止 Token/连接串/原始行；data-model.md §2.2）
- [x] T023 [US3] 可观测性：JsonlLogStore 接入（stock_factor_log_dir/filename、
  白名单字段、10MiB 轮转 ×5）、窗口及时性计算（17:00 启动当日终态）、
  错误摘要不泄漏敏感配置（NFR-005/NFR-006；依赖 T022）
- [x] T024 [US3] 运维验证：集成测试 `tests/integration/test_stock_factor_observability.py`
  （-m mysql）断言 issue 脱敏与日志白名单；按 quickstart.md §7 五分钟排障
  步骤核对 run/attempt/issue/日志/ClickHouse 五步定位可执行（依赖 T023）

**Checkpoint**: 三个用户故事全部独立可用

---

## 阶段 6：完善与横切关注点

**Purpose**: 替换性证明、质量门禁与上线验证

- [x] T025 [P] 替代实现验证：以第二 Provider 实现（Memory 测试替身）重跑
  T009/T010/T017/T021 契约测试，证明换源不改业务代码（ED-006/ED-007）；
  结果记录至 research.md
- [x] T026 [P] 质量门禁：`uv run ruff check .`、`uv run mypy --strict src`、
  `uv run pytest`、`uv run pytest -m mysql` 全量通过（含 006 既有测试，
  证明 RateLimiter 泛化无回归）
- [x] T027 [P] quickstart.md 验证：§2 启动依赖、§3 增量、§4 回补与幂等、
  §5 非交易日、§6 失败与恢复逐项执行并记录结果
- [x] T028 文档一致性核对：spec（Clarifications 已落档）/plan/research/data-model/
  contracts/quickstart 六份文档交叉一致，无遗留待澄清标记
- [ ] T029 上线门禁实测并回填 research.md「部署前待验证项」1~7（已实测 1/2/5：
  2026-08-04 单日 5,529 行 << 10,000 上限；字段全集 261 个与校准后白名单
  完全匹配（价格仅 _qfq/_hfq 变体）；2026-08-05 003 映射对 5,529 个 ts_code
  完全覆盖（.SZ 2,889/.SH 2,308/.BJ 332）。待实测 3/4/6/7：30 次/分钟限流
  实际行为（006 已实测同档位，结论预期复用）、17:00 当日数据可得性、
  全量回补耗时（约 20~30 分钟）、复权字段回溯更新形态——需部署账户在
  交易日窗口执行，结果回填 research.md）

---

## 依赖与执行顺序

### Phase Dependencies

- **Setup（阶段 1）**: 无依赖，可立即开始
- **Foundational（阶段 2）**: 依赖阶段 1 完成——阻塞所有用户故事
  （含 T008 字段全集实测门禁，US1 契约测试前必须完成）
- **用户故事（阶段 3+）**: 依赖阶段 2 完成
  - US1 → US2 → US3 顺序推进（US2/US3 复用 US1 的 Service 链路与 Provider，
    见 User Story Dependencies；有足够人力时 US3 测试可并行准备）
- **Polish（阶段 6）**: 依赖所有用户故事完成

### User Story Dependencies

- **User Story 1（P1）**: 阶段 2 后可开始（含 T008 实测门禁），无其他故事依赖
- **User Story 2（P2）**: 依赖 US1 的 Service 命令链路（T013）与 Provider
  （T011）——回补按日复用同一链路；不可在 US1 之前独立完成
- **User Story 3（P3）**: 依赖 US1（审计终态/issue 链路）与 US2（回补失败
  路径）；在 US1 完成后即可并行推进其测试准备

### Within Each User Story

- 测试必须先写并使它们 FAIL，再开始实现
- 模型/仓储 → 服务 → Flow/Deployment → 集成验证
- 每个故事完成后再推进下一个优先级

### Parallel Opportunities

- 阶段 1 的 T002/T003/T004 均可并行（不同文件）；T001 先行（配置）
- 阶段 2 的 T006/T007/T008 可并行（T008 实测门禁依赖 T003 白名单常量）；
  T005 先行（Port 定义）
- 用户故事内的测试任务（T009/T010）可并行；实现任务
  T011/T012 可并行，T013 依赖二者
- 不同用户故事由不同成员推进时，US1 完成前 US3 的测试（T021）可先写

---

## Parallel Example: User Story 1

```bash
# 契约测试先行（先失败后实现；字段全集已由 T008 实测校准）：
Task: "Provider 契约测试 tests/contract/test_stock_factor_provider.py"
Task: "Service 契约测试 tests/unit/test_stock_factor_service.py"

# 实现可并行启动：
Task: "TushareStockFactorProvider src/lucking/integrations/tushare/stock_factor_provider.py"
Task: "ClickHouse 仓储 src/lucking/repositories/stock_factor_clickhouse.py"

# 汇总：
Task: "StockFactorService src/lucking/services/stock_factor.py（依赖以上两者）"
```

---

## 实施策略

### MVP First（用户故事 1）

1. 完成阶段 1：初始化
2. 完成阶段 2：基础能力（CRITICAL - 阻塞所有故事；含 T008 字段实测门禁）
3. 完成阶段 3：用户故事 1（增量同步）
4. **STOP and VALIDATE**: 独立测试用户故事 1（T009~T016 + 契约/集成测试）
5. 上线门禁实测（T029 中增量相关项）后可部署演示

### Incremental Delivery

1. Setup + Foundational → 基础就绪（含字段全集实测校准）
2. 用户故事 1 → 独立测试 → 部署演示（MVP）
3. 用户故事 2 → 独立测试 → 完成初始化回补
4. 用户故事 3 → 独立测试 → 失败可诊断
5. 阶段 6 → 替换性证明与质量门禁 → 上线

### Parallel Team Strategy

1. 团队共同完成阶段 1、2（含 T008 实测门禁）
2. 阶段 2 完成后：成员 A 推进 US1；成员 B 可先行编写 US3 测试
   （T021）与 US2 测试（T017）
3. US1 完成后：成员 B 推进 US2，成员 C 推进 US3
4. 各故事独立集成验证

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- 每个用户故事独立可完成、可测试
- 验证测试先失败再实现（宪章 III：缺陷修复必须先有失败测试）
- 每个任务或逻辑组完成后提交
- 任一 Checkpoint 处可停下独立验证故事
- 避免：模糊任务、同文件冲突、破坏独立性的跨故事依赖

## Phase 7: Convergence

- [ ] T030 交易日窗口内实跑 quickstart.md §3~§6 并记录结果：§3 增量同步（触发 `股票技术面因子交易日同步/股票技术面因子交易日同步`，显式 scheduled_at）、§4 回补与幂等（`股票技术面因子历史回补/股票技术面因子历史回补`）、§5 非交易日 SKIPPED、§6 失败与恢复；结果回填 quickstart.md 或任务注释 per T027/quickstart.md（partial）
- [ ] T031 交易日窗口内执行 research.md 部署前待验证项 3/4/6/7 实测并回填：3）30 次/分钟限流实际行为（006 同档位结论预期复用）、4）17:00 当日数据可得性、6）2024-01-01 起全量回补耗时（预期约 20~30 分钟）、7）复权字段回溯更新形态（期间除权事件后重复同步同交易日）per T029/research.md（partial）

## Phase 8: Convergence

- [ ] T032 真实账户单日冒烟门禁（全量回补与上线前执行，依赖 T011/T013 已实现链路）：拉取最近一个交易日 `stk_factor_pro` 单日数据，经 TushareStockFactorProvider → StockFactorService → ClickHouse `stock_factor` 全链路发布；断言：白名单与响应字段逐名一致（无 PROVIDER_PAYLOAD"字段集合不精确"）、大市值股票（total_mv ≥ 10^8 万元）正常落库（无 Decimal 溢出）、received/added 计数与行数一致；失败类别记录于 issue 表 per T026/宪章 III（partial）
