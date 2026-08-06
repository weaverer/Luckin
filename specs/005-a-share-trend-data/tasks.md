# 任务：A股行情数据交易日同步

**输入**：`/specs/005-a-share-trend-data/` 中的设计文档

**前置条件**：plan.md（必需）、spec.md（用户故事必需）、research.md、data-model.md、contracts/

> 任务文档必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**Tests**: Tests are REQUIRED for changes to observable behavior, public contracts, data
models, or failure handling. Defect fixes require a failing regression test first. Include
contract/integration tests for interface changes and focused end-to-end tests for critical flows.

**第三方数据集成**：本功能通过 Tushare 四个接口获取数据，任务必须分别覆盖供应商无关
端口与规范化模型、四个独立供应商适配器、配置或依赖注入（Registry）、契约测试，
以及至少一个替代适配器或测试替身（Memory Provider）。业务层任务不得直接引用
第三方 SDK、传输模型或供应商专有字段。

**MySQL 表结构**：本功能新建三张 MySQL 审计表，任务必须覆盖迁移和数据库模式验证：
使用 `BIGINT AUTO_INCREMENT` 主键、数据库维护的 `created_at/updated_at`、
业务唯一约束（UUID UNIQUE、run_key UNIQUE），以及每张表和每个字段的中文注释。
五张 ClickHouse 业务表按 data-model.md §2 的引擎豁免设计实现与验证任务。

**组织方式**：按用户故事分组，使每个故事都能独立实现和测试。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## 路径约定

- **Single project**: `src/`、`tests/` at repository root
- 本功能代码位于 `src/lucking/`，测试位于 `tests/`

---

## 阶段 1：初始化（共享基础设施）

**Purpose**: 项目配置、ClickHouse 会话与日志基础设施

- [X] T001 扩展 `src/lucking/config.py`：四个 Provider 选择、MARKET_DATA_TIMEZONE、
  FETCH_DEADLINE_SECONDS、RUN_LEASE_SECONDS、PAGE_LIMIT、MAX_PAGES、日志路径，
  以及 CLICKHOUSE_HOST/PORT/DATABASE 连接配置；新增 `test_market_data_config.py` 验证默认值
- [X] T002 [P] 新增 `src/lucking/clickhouse.py`：ClickHouse 会话组件（连接池、批量插入、
  超时、错误分类映射为统一异常，禁止连接串进入日志）
- [X] T003 [P] 扩展 `src/lucking/logging.py`：market-data JSONL 日志（data_kind、run/attempt、
  目标交易日、批次键、提取计数、retry、窗口及时性字段白名单；10 MiB 轮转保留 5 个归档）

**Checkpoint**: 基础设施就绪

---

## 阶段 2：基础能力（阻塞性前置条件）

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 创建 MySQL 审计三表迁移 `migrations/versions/004_create_market_data_audit_tables.py`：
  `market_data_sync_run` / `market_data_sync_attempt` / `market_data_sync_issue`，
  宪章 VI 统一治理（BIGINT 自增主键、UUID UNIQUE、数据库维护 created_at/updated_at、
  中文表注释与每列非空中文注释），验证空库升级与 `003 → 004`
- [X] T005 创建 ClickHouse 五张业务表迁移（`src/lucking/clickhouse.py` 的 migrate 命令）：
  `daily_quote` / `adj_factor` / `daily_basic` / `weekly_kline` / `monthly_kline`，
  引擎 `ReplacingMergeTree(updated_at)`、ORDER BY `(trade_date, stock_id)`、
  `PARTITION BY toYYYYMM(trade_date)`、引擎列注释与表注释
- [X] T006 修正 `migrations/env.py` 的模型加载，使 Alembic metadata 可发现全部既有与新增模型
- [X] T007 [P] 定义统一异常与问题类别（PROVIDER_RATE_LIMITED、RESPONSE_CAPPED、
  TRADE_DATE_MISMATCH、PERIOD_MISMATCH、UNKNOWN_STOCK_IDENTITY、IDENTITY_CONFLICT、
  DUPLICATE、RECORD_CONFLICT、ABANDONED、PERSISTENCE_ERROR 等）在
  `src/lucking/repositories/market_data.py`（与金股模式一致，不新建 errors.py）
- [X] T008 定义审计 ORM 模型（run/attempt/issue）与 `run_key` 生成（data_kind 参数化、
  SCHEDULED/BACKFILL 两类输入）在 `src/lucking/models/market_data.py`
- [X] T009 实现 MySQL 审计 Repository：原子 claim（数据库 UTC 时钟固定租约）、attempt 追加、
  `SUCCEEDED` 不可重开、`FAILED → Retry` 复用原 run、租约过期 `ABANDONED` 原子转换、
  回补交易日解析在 `src/lucking/repositories/market_data.py`
- [X] T010 实现交易日判断辅助（复用 `trading_calendar` CN-S，非交易日跳过）在
  `src/lucking/services/market_data.py`
- [X] T011 实现 MySQL schema 验证测试 `tests/integration/test_market_data_mysql.py`：
  主键、UUID 唯一键、run_key 唯一、created_at/updated_at 默认值与 ON UPDATE、
  中文表/字段注释，ORM metadata、迁移 DDL 与实际 `SHOW CREATE TABLE` 三方一致；
  并发认领与租约过期边界
- [X] T012 实现 ClickHouse 五表 schema 验证测试 `tests/integration/test_market_data_mysql.py`
  或独立文件：引擎、排序键、分区表达式、列类型与注释与迁移文件一致；
  同键替换幂等（重复写入同一 `(trade_date, stock_id)` 只保留最新版本）

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## 阶段 3：用户故事 1 - 交易日日线行情与复权因子同步（优先级：P1）🎯 MVP

**Goal**: 每个交易日 9:00 同步全市场复权因子、17:00 同步全市场未复权日线行情，
分别写入 ClickHouse `adj_factor` 与 `daily_quote` 表，停牌无记录、重复同步幂等

**Independent Test**: 以最近交易日为验证目标：触发 ADJ_FACTOR 与 DAILY_QUOTE 计划同步，
`adj_factor`/`daily_quote` 表出现全市场记录；再次触发不产生重复且业务字段不变；
非交易日触发返回跳过；停牌股票无记录且同步成功

### 用户故事 1 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T013 [P] [US1] DailyQuoteProvider 契约测试 `tests/contract/test_daily_quote_provider.py`：
  输入交易日全量转换、字段与单位、交易日一致、空响应、触顶不完整、停牌无记录、
  供应商字段泄漏失败；含 Memory Provider 一致性套件
- [X] T014 [P] [US1] AdjFactorProvider 契约测试 `tests/contract/test_adj_factor_provider.py`：
  因子大于 0、ts_code 后缀映射、空响应、触顶；含 Memory Provider 一致性套件
- [X] T015 [P] [US1] Tushare daily/adj_factor Adapter 契约测试
  `tests/contract/test_tushare_market_data.py`（HTTPX MockTransport）：trade_date 请求参数、
  全市场返回、权限码与限流映射、瞬态 3 次重试与确定性零重试、空值与错误信封
- [X] T016 [US1] Service 计划同步单元测试 `tests/unit/test_market_data_service.py`：
  目标交易日推导、非交易日跳过、run_key 幂等、覆盖证据校验、身份解析
- [X] T017 [US1] 集成测试 `tests/integration/test_market_data_flow.py`（US1 场景）：
  全市场约 5,400 行写入两表、停牌无记录、重复同步不产生重复、
  ClickHouse 同键替换与单 block 原子可见

### 用户故事 1 实现

- [X] T018 [P] [US1] 定义 DailyQuoteProvider 端口（请求/规范记录/覆盖证据/批次）
  在 `src/lucking/ports/daily_quote_provider.py`
- [X] T019 [P] [US1] 定义 AdjFactorProvider 端口在 `src/lucking/ports/adj_factor_provider.py`
- [X] T020 [P] [US1] 定义规范模型（DailyQuote、AdjFactor + 共享 RetrievalEvidence）
  在 `src/lucking/models/market_data.py`
- [X] T021 [P] [US1] 实现 Tushare DailyQuote Adapter（只调用 `daily`、trade_date 全市场、
  字段白名单、`.SH/.SZ/.BJ` 后缀映射）在 `src/lucking/integrations/tushare/daily_quote_provider.py`
- [X] T022 [P] [US1] 实现 Tushare AdjFactor Adapter（只调用 `adj_factor`）
  在 `src/lucking/integrations/tushare/adj_factor_provider.py`
- [X] T023 [US1] 注册四个 Provider 到 Registry（配置或依赖注入选择，业务层不得直接引用
  Tushare 模块）在 `src/lucking/integrations/registry.py`
- [X] T024 [US1] 实现 ClickHouse Repository 写入与查询（单 block 批量 INSERT、
  同键替换语义、按 data_kind 路由五表、按交易日/股票筛选的分页查询）
  在 `src/lucking/repositories/market_data_clickhouse.py`
- [X] T025 [US1] 实现 MarketDataService 计划同步（data_kind 参数化命令；
  发布语义：ClickHouse 写入成功后同一 MySQL 事务写 attempt/run 成功终态与全部计数）
  在 `src/lucking/services/market_data.py`
- [X] T026 [US1] 实现 `market-data-sync` Flow 与 `复权因子同步`（Cron `0 9 * * 1-5`）、
  `日线行情同步`（Cron `0 17 * * 1-5`）Deployment（Asia/Shanghai、并发 1、ENQUEUE、
  retries=0、计划时点来自 Prefect runtime）在 `src/lucking/flows/market_data.py` 与 `prefect.yaml`

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## 阶段 4：用户故事 2 - 交易日基本面与周/月线同步（优先级：P2）

**Goal**: 每个交易日 17:45 同步全市场基本面指标、18:30 同步全市场周线与月线，
分别写入 ClickHouse `daily_basic` / `weekly_kline` / `monthly_kline` 表；
亏损公司空字段正常保存、周月线各自独立模型、同一周期幂等更新

**Independent Test**: 以最近交易日为验证目标：触发 DAILY_BASIC、WEEKLY_KLINE、
MONTHLY_KLINE 计划同步，三表出现最新数据；亏损公司 PE/PB 为 NULL 且同步成功；
同一周期重复同步只保留一行；周线与月线互不串扰

### 用户故事 2 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T027 [P] [US2] DailyBasicProvider 契约测试 `tests/contract/test_daily_basic_provider.py`：
  16 个规范字段（不含 close）、亏损空值保持 None、交易日一致；含 Memory Provider 一致性套件
- [X] T028 [P] [US2] WeeklyMonthlyKlineProvider 契约测试
  `tests/contract/test_weekly_monthly_kline_provider.py`：freq 分派两个模型、
  未复权价格完整、周期最后交易日归属、同周期多日返回相同 trade_date；含 Memory Provider
- [X] T029 [P] [US2] Tushare daily_basic/stk_week_month_adj Adapter 契约测试
  `tests/contract/test_tushare_market_data.py`：freq 参数、周期语义、权限码与限流映射、
  未复权价格缺失隔离
- [X] T030 [US2] 集成测试 `tests/integration/test_market_data_flow.py`（US2 场景）：
  亏损公司空值、周月线两表互不串扰、同一周期重复同步一行、非交易日跳过

### 用户故事 2 实现

- [X] T031 [P] [US2] 定义 DailyBasicProvider 端口在 `src/lucking/ports/daily_basic_provider.py`
- [X] T032 [P] [US2] 定义 WeeklyMonthlyKlineProvider 端口在
  `src/lucking/ports/weekly_monthly_kline_provider.py`
- [X] T033 [P] [US2] 定义规范模型（DailyBasic、WeeklyKline、MonthlyKline，
  周线与月线独立模型）在 `src/lucking/models/market_data.py`
- [X] T034 [P] [US2] 实现 Tushare DailyBasic Adapter（只调用 `daily_basic`、不请求 `close`）
  在 `src/lucking/integrations/tushare/daily_basic_provider.py`
- [X] T035 [P] [US2] 实现 Tushare 周月线 Adapter（只调用 `stk_week_month_adj`，
  按 `freq` 分派到两个独立规范模型）在
  `src/lucking/integrations/tushare/weekly_monthly_kline_provider.py`
- [X] T036 [US2] 扩展 MarketDataService 支持 DAILY_BASIC / WEEKLY_KLINE / MONTHLY_KLINE
  命令（复用 T025 的发布语义）在 `src/lucking/services/market_data.py`
- [X] T037 [US2] 新增 `每日基本面同步`（Cron `45 17 * * 1-5`）、`周K线同步`、
  `月K线同步`（Cron `30 18 * * 1-5`）Deployment 在
  `src/lucking/flows/market_data.py` 与 `prefect.yaml`

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## 阶段 5：用户故事 3 - 识别同步失败和数据质量问题（优先级：P3）

**Goal**: 限流/超时/空响应/冲突时安全失败并保留已有数据；失败重试复用原运行
新增尝试；支持 2024-01-01 起交易日区间回补；5 分钟内可排障

**Independent Test**: 模拟限流、超时、空响应、冲突与 ClickHouse 不可达，
验证运行终态、计数、issue 类别与错误摘要准确，已有数据不被破坏、查询不可见半批；
重复提交回补批次不重复处理成功日期

### 用户故事 3 测试（行为或契约变化时必需）⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T038 [US3] 重试与租约边界测试 `tests/integration/test_market_data_repository.py`：
  瞬态 3 次重试上限、确定性错误零重试、租约有效/过期边界、ABANDONED → Retry 原子转换
- [X] T039 [US3] 失败演练测试 `tests/integration/test_market_data_mysql.py`：
  限流/超时/空响应/冲突/ClickHouse 不可达均不破坏已有数据；
  失败批次查询不可见半批、重试后行集与成功执行一致；
  验证 NFR-009——使日线同步失败时，复权因子/基本面/周月线同步仍独立成功
  （互不阻塞、互不回滚）
- [X] T040 [US3] 回补 Flow 测试 `tests/integration/test_market_data_flow.py`：
  区间整体校验（2024-01-01 之前、未来、反向、空区间）、逐日幂等、
  失败日期复用原运行重试、新批次键主动刷新
- [X] T041 [US3] 容量与回补测试 `tests/integration/test_market_data_capacity.py`：
  单日全市场约 5,400 行、连续 30 次重复同步无重复记录、代表性交易日集合回补、
  周月线容量独立验证

### 用户故事 3 实现

- [X] T042 [US3] 实现 issue 记录与安全摘要（字段白名单、provider_security_id 哈希、
  payload 哈希、问题类别全覆盖）在 `src/lucking/repositories/market_data.py`
- [X] T043 [US3] 实现回补 Flow `market-data-backfill`（data_kind + start_date/end_date +
  backfill_batch_id；区间校验、按交易日历逐日展开、run 状态解析）
  在 `src/lucking/flows/market_data.py` 与 `prefect.yaml`
- [X] T044 [US3] 实现运行/尝试/问题查询辅助（五分钟排障：按 data_kind、目标交易日、
  状态、错误类别筛选）在 `src/lucking/repositories/market_data.py`
- [X] T045 [US3] 验证 JSONL 日志字段白名单与窗口及时性（9:00 开盘前、17:00/17:45/18:30
  当日终态）在 `src/lucking/logging.py` 与 `tests/unit/test_market_data_logging.py`

**Checkpoint**: All user stories should now be independently functional

---

## 阶段 6：完善与横切关注点

**Purpose**: Improvements that affect multiple user stories

- [X] T046 [P] 更新 `README.md`：行情同步配置、五个 Deployment 部署、2024-01-01 回补、
  失败重试、完整性门禁、五分钟排障与安全停止说明
- [X] T047 [P] 运行 `specs/005-a-share-trend-data/quickstart.md` 端到端验证：
  四接口单日同步、周月线两表、回补幂等、非交易日跳过、失败恢复、排障；
  验证 NFR-008 数据生命周期——清理必须按交易日/周期显式执行（含 ClickHouse
  按月分区 `ALTER TABLE ... DROP PARTITION` 路径），不存在自动清理逻辑
- [X] T048 运行质量门禁：`uv run ruff check .`、`uv run mypy src`、`uv run pytest`、
  `uv run pytest -m mysql`、`uv run alembic upgrade head`、
  ClickHouse migrate 与 schema 校验
- [X] T049 [P] 上线门禁：用部署账户或供应商沙箱验证四个接口的权限、积分门槛、
  频率限制与按 trade_date/周期全市场返回行为；验证失败时不得启用对应数据类，
  必须切换兼容 Provider 或阻断上线；验证 `stk_week_month_adj` 是否支持
  trade_date 全市场提取（不支持则按 research.md 待验证项处理）
- [X] T050 需求追溯复核：spec/plan/tasks 三方一致性（FR/NFR/ED/SC 全覆盖，
  无范围外任务），确认 spec.md 的 Clarifications 与 plan.md 决策一致；
  显式完成 SC-006 范围审计——核验五个 Adapter 的字段白名单与契约测试
  （供应商字段泄漏即失败）覆盖全部规范字段，且无未授权字段进入 ClickHouse 业务表

---

## 依赖与执行顺序

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) -
  Reuses US1 的 Service 发布核心（T025）与 Registry（T023），但具备独立测试入口
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) -
  回补 Flow（T043）依赖全部 data_kind 的 Service 命令（T025/T036），
  失败演练依赖 US1/US2 的可观测行为

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services; services before flows; core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T002/T003、T007/T008 等 Setup/Foundational 任务可并行
- 各用户故事的契约测试任务（T013/T014/T015、T027/T028/T029）可并行
- 端口/Adapter/模型任务（T018~T022、T031~T035）可并行
- 不同用户故事可在基础完成后由不同成员并行

---

## Parallel Example: User Story 1

```bash
# 并行执行用户故事 1 的契约测试：
Task: "DailyQuoteProvider 契约测试 in tests/contract/test_daily_quote_provider.py"
Task: "AdjFactorProvider 契约测试 in tests/contract/test_adj_factor_provider.py"
Task: "Tushare Adapter 契约测试 in tests/contract/test_tushare_market_data.py"

# 并行启动用户故事 1 的端口与 Adapter：
Task: "定义 DailyQuoteProvider 端口 in src/lucking/ports/daily_quote_provider.py"
Task: "定义 AdjFactorProvider 端口 in src/lucking/ports/adj_factor_provider.py"
Task: "实现 Tushare DailyQuote Adapter in src/lucking/integrations/tushare/daily_quote_provider.py"
Task: "实现 Tushare AdjFactor Adapter in src/lucking/integrations/tushare/adj_factor_provider.py"
```

---

## 实施策略

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1（日线 + 复权因子）
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Test independently → Deploy/Demo
4. Add User Story 3 → Test independently → Deploy/Demo
5. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 3
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence

---

## 阶段 7：收敛

- [ ] T051 用部署账户实测周/月线在非周期边界日（周一~周四、月初~月末）请求时
  `stk_week_month_adj` 返回的周期最后交易日行为，确认与契约"不晚于请求交易日"
  校验一致（不一致则按实测调整周期归属口径）per ED-008（partial）
- [ ] T052 用部署账户或供应商沙箱实测四个接口的续取参数（limit/offset：位置前进、
  满页续取、短页终止）有效性，验证通过后方可启用
  `MARKET_DATA_TUSHARE_PAGINATION_ENABLED=true` per FR-017 / SC-002 / plan 决策 4（partial）
