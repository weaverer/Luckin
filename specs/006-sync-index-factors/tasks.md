# 任务：指数技术因子交易日同步（006-sync-index-factors）

**输入**：`/specs/006-sync-index-factors/` 中的设计文档

**前置条件**：plan.md（必需）、spec.md（用户故事必需）、research.md、data-model.md、contracts/

> 任务文档必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**Tests**: Tests are REQUIRED for changes to observable behavior, public contracts, data
models, or failure handling. Defect fixes require a failing regression test first. Include
contract/integration tests for interface changes and focused end-to-end tests for critical flows.

**第三方数据集成**：本功能通过 Tushare `idx_factor_pro` 获取数据，任务已覆盖供应商无关
端口与规范化模型（T006）、独立供应商适配器（T011）、配置/依赖注入（T001/T008）、
契约测试（T009/T010/T018/T022），以及替代适配器或测试替身（T026）。
业务层任务不得直接引用第三方 SDK、传输模型或供应商专有字段。

**MySQL 表结构**：本功能新建 `index_current`/`index_provider_mapping` 两张项目拥有的
MySQL 表（宪章 VI 标准治理：BIGINT AUTO_INCREMENT 主键、数据库维护的
created_at/updated_at、业务唯一约束、中文表/字段注释），任务已覆盖迁移（T003）、
ORM（T004）与三方一致验证（T005）。

**组织方式**：按用户故事分组，使每个故事都能独立实现和测试。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## 路径约定

- **Single project**: `src/`, `tests/` at repository root（本功能按 plan.md 源代码树，
  Python 单项目结构）

## 阶段 1：初始化（共享基础设施）

**Purpose**: 配置、数据库与模型基础设施

- [x] T001 扩展 `src/lucking/config.py`：新增 `index_factor_*` 配置项
  （provider_code、timezone=Asia/Shanghai、log_dir/log_filename、
  fetch_deadline_seconds=1500、run_lease_seconds=2100、page_limit=8000、
  rate_limit_per_minute=30），并强制校验 lease_seconds > fetch_deadline_seconds
- [x] T002 [P] 在 `src/lucking/clickhouse.py` 注册 `index_factor` 表 DDL 与
  `migrate` 命令：`ReplacingMergeTree(updated_at)`、`ORDER BY (trade_date, index_id)`、
  `PARTITION BY toYYYYMM(trade_date)`、身份列 + 87 个数据列（data-model.md §3
  全集，每列中文 COMMENT）、无 TTL
- [x] T003 [P] 新建 Alembic 迁移 `migrations/versions/005_create_index_identity_tables.py`：
  `index_current`（id BIGINT UNSIGNED AUTO_INCREMENT 主键中文注释、index_id
  CHAR(36) ascii_bin UNIQUE、index_code UNIQUE、created_at/updated_at
  CURRENT_TIMESTAMP）、`index_provider_mapping`（唯一键
  (provider_code, provider_security_id)）；每张表中文表注释、每列非空中文注释
- [x] T004 [P] 新建 `src/lucking/models/index_factor.py`：`IndexCurrent`/
  `IndexProviderMapping` ORM（与 T003 DDL 一致）；规范 DTO
  （IndexFactorRequest、ProviderIndexFactorRecord 全集、
  RetrievalEvidence、ProviderIndexFactorBatch、IndexFactorSyncResult）
- [x] T005 [P] 新建 `tests/integration/test_index_factor_schema.py`（按功能命名惯例）：
  验证 `index_current`/`index_provider_mapping` 主键为 BIGINT AUTO_INCREMENT、
  含数据库维护的 created_at/updated_at、业务唯一约束存在、表与每列有非空中文注释，
  ORM/Alembic/实际 SHOW CREATE TABLE 三方一致

---

## 阶段 2：基础能力（阻塞性前置条件）

**Purpose**: 供应商无关 Port、限流器与注册中心——所有用户故事的前置

**⚠️ CRITICAL**: 本阶段完成前不得开始任何用户故事实现

- [x] T006 新建 `src/lucking/ports/index_factor_common.py`：`IndexFactorProvider`
  Protocol（provider_code + fetch_index_factors(request, *, deadline)）、
  `IndexFactorRequest`（target_trade_date）、`ProviderIndexFactorBatch`；
  错误复用 `src/lucking/ports/market_data_common.py` 的 ProviderError 家族
  （含 PROVIDER_RESPONSE_CAPPED）；DTO 字段全集即白名单（ED-005）
- [x] T007 [P] 新建 `src/lucking/integrations/tushare/index_rate_limiter.py`：
  进程级节流器（任意 60 秒窗口 ≤ 30 次真实请求、最小间隔 2 秒、
  `monotonic`/`sleep` 可注入、线程安全）；配套单元测试
  `tests/unit/test_index_rate_limiter.py`（时间旅行验证窗口计数与间隔）
- [x] T008 [P] 在 `src/lucking/integrations/registry.py` 增加
  `register_index_factor_provider`/`build_index_factor_provider`/
  `build_tushare_index_factor_provider`（依赖注入组装 client、节流器、
  分页与 deadline 配置）

**Checkpoint**: 基础能力就绪——用户故事实现可开始

---

## 阶段 3：用户故事 1 - 交易日指数技术因子增量同步（优先级：P1）🎯 MVP

**Goal**: 每个交易日北京时间 17:00，按交易日提取全部指数（大盘/申万/中信）技术因子与
基础行情，自举注册指数身份，发布 ClickHouse `index_factor` 并完成 MySQL 审计终态。

**Independent Test**: 对最近一个交易日触发 `index-factor-sync/指数技术因子同步`（显式
scheduled_at），预期：received = 该日全部指数、run SUCCEEDED、`index_factor` 按
(trade_date, index_id) 可查（SELECT ... FINAL）、身份表自动注册；重复触发同一
scheduled_at 幂等（第二次不重复处理）。非交易日触发 → SKIPPED_NOT_TRADING_DAY。

### 用户故事 1 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T009 [P] [US1] Provider 契约测试 `tests/contract/test_index_factor_provider.py`：
  白名单严格相等（set(row) != set(fields) 整批失败）、请求只含 trade_date、
  触顶 8,000 行 → PROVIDER_RESPONSE_CAPPED、错误映射全集、节流间隔 ≥ 2 秒、
  重试退避 30/120/300 与 deadline 约束（tushare-index-factor.md §7）
- [x] T010 [P] [US1] Service 契约测试 `tests/unit/test_index_factor_service.py`
  （沿用仓库惯例：service 测试在 tests/unit/）：
  假 Provider 全流程——非交易日 SKIPPED、身份自举注册与非法后缀跳过、
  去重/冲突整批失败、全市场空响应失败 vs 个别指数无数据成功、
  重复同步 run_key 幂等、失败不破坏已有数据（index-factor-service.md §6）

### 用户故事 1 实现

- [x] T011 [P] [US1] 新建 `src/lucking/integrations/tushare/index_factor_provider.py`：
  TushareIndexFactorProvider——`idx_factor_pro` 调用（trade_date 参数）、
  字段白名单与映射（去 `_bfq` 后缀）、节流器集成、瞬态重试 ≤ 3 次、
  错误映射（PROVIDER_RATE_LIMITED/QUOTA_EXCEEDED/...）、触顶完整性门禁
- [x] T012 [P] [US1] 新建 `src/lucking/repositories/index_factor_identity.py`：
  身份解析与自举注册（后缀白名单 .SH/.SZ/.CSI/.SI；合法 upsert
  index_current/index_provider_mapping，MySQL 唯一约束兜底；非法 → 脱敏 issue +
  无效计数，data-model.md §5 步骤 1）
- [x] T013 [P] [US1] 新建 `src/lucking/repositories/index_factor_clickhouse.py`：
  publish_batch（INSERT 前 SELECT ... FINAL 计算 added/updated/unchanged、
  单 block JSONEachRow 批量插入）、query_index_factors（FINAL、按
  (trade_date, index_id) 排序、limit/offset）
- [x] T014 [US1] 新建 `src/lucking/services/index_factor.py`：
  IndexFactorService——ScheduledIndexFactorSyncCommand 处理（交易日判断 →
  run_key 认领/租约 → Provider 提取 → 身份注册 → 批次校验 → 发布 →
  同一 MySQL 事务写 attempt/run 终态）；复用 `src/lucking/repositories/market_data.py`
  审计仓储，data_kind='INDEX_FACTOR'（index-factor-service.md §4 行为 1~7）
- [x] T015 [US1] 集成测试 `tests/integration/test_index_factor_sync.py`（-m mysql）：
  真实 MySQL+ClickHouse 端到端——认领幂等（重复 scheduled_at 不重复处理）、
  发布计数正确、失败终态不破坏已有数据、租约过期 ABANDONED 可重开
- [x] T016 [US1] 新建 `src/lucking/flows/index_factor.py`：`index_factor_sync` Flow
  （retries=0；scheduled_at 从 prefect.runtime.flow_run.scheduled_start_time 读取、
  直接调用必须显式提供；schedule_slug 校验；非交易日 SKIPPED 成功结束；
  日志白名单不含 Token/签名/完整请求体，prefect-flow.md §1）
- [x] T017 [US1] 在 `prefect.yaml` 增加 Deployment `index-factor-sync/指数技术因子同步`：
  Cron `0 19 * * 1-5`（Asia/Shanghai）、work pool local-pool、
  concurrency_limit 1 + collision_strategy ENQUEUE、参数 schedule_slug

**Checkpoint**: 用户故事 1 独立可测——MVP 完成，可部署演示

---

## 阶段 4：用户故事 2 - 初始化历史回补（优先级：P2）

**Goal**: 通过人工回补 Flow 从 2024-01-01 逐交易日回补至当前增量，全程遵守
每分钟 30 次限流，逐日独立终态、已成功日期跳过、失败日期可安全重试。

**Independent Test**: 触发 `index-factor-backfill/指数技术因子历史回补`（start/end/backfill_batch_id），
预期：逐日独立终态、请求间隔 ≥ 2 秒（≤ 30 次/分钟）；重复提交同 batch_id 已成功
日期 SKIP（替身调用计数不增加）；非法区间（未来/反向/早于 2024-01-01）整体拒绝；
与增量重叠日期同键幂等。

### 用户故事 2 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T018 [P] [US2] Service 回补契约测试（新建 `tests/unit/test_index_factor_backfill.py`，
  与 T010 分文件避免并行冲突）：
  区间校验拒绝（未来日期、反向区间、起点 < 2024-01-01）、逐日 resolve
  （START/SKIP_SUCCEEDED/RETRY/IN_PROGRESS）、已成功日期不重复调用 Provider
  （替身调用计数断言）、失败日期重试只处理失败日期（index-factor-service.md §4 行为 8）

### 用户故事 2 实现

- [x] T019 [US2] 在 `src/lucking/services/index_factor.py` 实现
  BackfillIndexFactorCommand 处理：区间整体校验（BACKFILL_START=2024-01-01、
  无未来日期、start ≤ end）→ 交易日历逐日展开 → 逐日按 backfill run_key
  认领与终态（复用 T014 链路；依赖 T014）
- [x] T020 [US2] 在 `src/lucking/flows/index_factor.py` 实现 `index_factor_backfill`
  Flow（start_date/end_date/backfill_batch_id 参数、retries=0），并在
  `prefect.yaml` 增加人工 Deployment `index-factor-backfill/指数技术因子历史回补`
  （无 schedule；依赖 T019）
- [x] T021 [US2] 集成测试 `tests/integration/test_index_factor_backfill.py`（-m mysql）：
  回补与增量重叠日期幂等（无重复记录）、中断恢复（部分成功后再跑只补失败日期）、
  节流间隔实测 ≥ 2 秒（依赖 T019/T020）

**Checkpoint**: 用户故事 1 与 2 均独立可用

---

## 阶段 5：用户故事 3 - 识别同步失败和数据质量问题（优先级：P3）

**Goal**: 限流、超时、空响应、字段缺失、冲突与不完整结果均产生可识别的终态、
计数、issue 类别与脱敏摘要；运维可在 5 分钟内判断状态与处置方式。

**Independent Test**: 注入限流/超时/空响应/缺失字段/冲突/触顶场景，预期：每个场景
的 run 终态、计数、issue 类别（PROVIDER_RATE_LIMITED/PROVIDER_TIMEOUT/
PROVIDER_RESPONSE_CAPPED/RECORD_CONFLICT/...）准确；已有有效数据不被清空；
日志与 issue 不含 Token/原始行（脱敏断言）。

### 用户故事 3 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T022 [P] [US3] 失败路径契约测试（新建 `tests/unit/test_index_factor_failure.py`，
  与 T010/T018 分文件避免并行冲突）：
  限流/超时重试耗尽 → FAILED + 计数；空响应 vs 个别无数据区分；字段缺失跳过 +
  全无效失败；冲突整批失败；触顶不完整失败；所有失败不破坏已有数据
  （spec FR-012~FR-015、ED-004）

### 用户故事 3 实现

- [x] T023 [P] [US3] issue 记录与脱敏：复用 `market_data_sync_issue`（attempt_id
  关联；统一问题类别全集在既有 21 类基础上**新增 `UNKNOWN_INDEX_IDENTITY`**
  类别，与 `UNKNOWN_STOCK_IDENTITY` 平行，指数身份解析失败即记录该类别；
  其余类别沿用 005 全集；safe_summary/payload_hash/provider_security_id_hash，
  禁止 Token/连接串/原始行；data-model.md §4）
- [x] T024 [US3] 可观测性：JsonlLogStore 接入（index_factor_log_dir/filename、
  白名单字段、10MiB 轮转 ×5）、窗口及时性计算（17:00 启动当日终态）、
  错误摘要不泄漏敏感配置（NFR-004/NFR-005；依赖 T023）
- [x] T025 [US3] 运维验证：集成测试 `tests/integration/test_index_factor_observability.py`
  （-m mysql）断言 issue 脱敏与日志白名单；按 quickstart.md §7 五分钟排障
  步骤核对 run/attempt/issue/日志/ClickHouse 五步定位可执行（依赖 T024）

**Checkpoint**: 三个用户故事全部独立可用

---

## 阶段 6：完善与横切关注点

**Purpose**: 替换性证明、质量门禁与上线验证

- [x] T026 [P] 替代实现验证：以第二 Provider 实现（测试替身或假供应商）重跑
  T009/T010/T018/T022 契约测试，证明换源不改业务代码（ED-006/ED-007）；
  结果记录至 research.md
- [x] T027 [P] 质量门禁：`uv run ruff check .`、`uv run mypy src`、
  `uv run pytest`、`uv run pytest -m mysql` 全量通过
- [x] T028 [P] quickstart.md 验证：§2 启动依赖、§3 增量、§4 回补与幂等、
  §5 非交易日、§6 失败与恢复逐项执行并记录结果
- [x] T029 文档一致性核对：spec（Clarifications 已落档）/plan/research/data-model/
  contracts/quickstart 六份文档交叉一致，无遗留待澄清标记
- [ ] T030 上线门禁实测并回填 research.md「部署前待验证项」1~6：8,000 行上限
  行为、字段全集（78 因子 + 10 基础行情）、30 次/分钟限流实际行为、
  17:00 数据可得性、ts_code 后缀全集、回补耗时（约 20~30 分钟）
  （2026-08-02 已实测 1/2/5：单日 3146 行 << 8,000；字段全集完全匹配白名单；
  后缀全集 8 种已校准白名单并同步迁移/ORM/实测库。待实测 3/4/6：限流实际
  行为、17:00 当日数据可得性、全量回补耗时——需部署账户在交易日窗口执行，
  结果回填 research.md）

---

## 依赖与执行顺序

### Phase Dependencies

- **Setup（阶段 1）**: 无依赖，可立即开始
- **Foundational（阶段 2）**: 依赖阶段 1 完成——阻塞所有用户故事
- **用户故事（阶段 3+）**: 依赖阶段 2 完成
  - US1 → US2 → US3 顺序推进（US2/US3 复用 US1 的 Service 链路与 Provider，
    见 User Story Dependencies；有足够人力时 US3 测试可并行准备）
- **Polish（阶段 6）**: 依赖所有用户故事完成

### User Story Dependencies

- **User Story 1（P1）**: 阶段 2 后可开始，无其他故事依赖
- **User Story 2（P2）**: 依赖 US1 的 Service 命令链路（T014）与 Provider
  （T011）——回补按日复用同一链路；不可在 US1 之前独立完成
- **User Story 3（P3）**: 依赖 US1（审计终态/issue 链路）与 US2（回补失败
  路径）；在 US1 完成后即可并行推进其测试准备

### Within Each User Story

- 测试必须先写并使它们 FAIL，再开始实现
- 模型/仓储 → 服务 → Flow/Deployment → 集成验证
- 每个故事完成后再推进下一个优先级

### Parallel Opportunities

- 阶段 1 的 T002/T003/T004/T005 均可并行（不同文件）
- 阶段 2 的 T007/T008 可并行；T006 先行（Port 定义）
- 用户故事内的测试任务（T009/T010）可并行；实现任务
  T011/T012/T013 可并行，T014 依赖三者
- 不同用户故事由不同成员推进时，US1 完成前 US3 的测试（T022）可先写

---

## Parallel Example: User Story 1

```bash
# 契约测试先行（先失败后实现）：
Task: "Provider 契约测试 tests/contract/test_index_factor_provider.py"
Task: "Service 契约测试 tests/unit/test_index_factor_service.py"

# 实现可并行启动：
Task: "TushareIndexFactorProvider src/lucking/integrations/tushare/index_factor_provider.py"
Task: "身份仓储 src/lucking/repositories/index_factor_identity.py"
Task: "ClickHouse 仓储 src/lucking/repositories/index_factor_clickhouse.py"

# 汇总：
Task: "IndexFactorService src/lucking/services/index_factor.py（依赖以上三者）"
```

---

## 实施策略

### MVP First（用户故事 1）

1. 完成阶段 1：初始化
2. 完成阶段 2：基础能力（CRITICAL - 阻塞所有故事）
3. 完成阶段 3：用户故事 1（增量同步）
4. **STOP and VALIDATE**: 独立测试用户故事 1（T009~T017 + 契约/集成测试）
5. 上线门禁实测（T030 中增量相关项）后可部署演示

### Incremental Delivery

1. Setup + Foundational → 基础就绪
2. 用户故事 1 → 独立测试 → 部署演示（MVP）
3. 用户故事 2 → 独立测试 → 完成初始化回补
4. 用户故事 3 → 独立测试 → 失败可诊断
5. 阶段 6 → 替换性证明与质量门禁 → 上线

### Parallel Team Strategy

1. 团队共同完成阶段 1、2
2. 阶段 2 完成后：成员 A 推进 US1；成员 B 可先行编写 US3 测试
   （T022）与 US2 测试（T018）
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
