# 任务：每月券商金股同步

**输入**：`specs/004-sync-broker-recommendations/` 中的设计文档

**前置条件**：plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

> 任务文档必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**测试要求**：所有契约、数据模型、可观察行为和失败路径必须先编写失败测试，再实现；
真实 MySQL 测试不得用 SQLite 替代排序规则、首次并发认领、行锁和事务回滚门禁。

**第三方边界**：Tushare 只允许存在于独立 Adapter 与通用 Client 中。
领域层仅依赖 `BrokerRecommendationProvider`、规范 DTO 和统一异常；
Memory Provider 必须证明更换来源不修改 Service、Repository 或业务表契约。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**：可与同阶段其他标记任务并行，且修改不同文件
- **[Story]**：对应 `spec.md` 的用户故事
- 每个任务都包含明确文件路径

## 阶段 1：初始化（共享基础设施）

**目的**：建立独立金股垂直切片、测试文件和安全配置入口，不改变现有股票列表行为。

- [ ] T001 创建金股领域包骨架与导出文件 `src/lucking/ports/broker_recommendation_provider.py`、`src/lucking/integrations/tushare/broker_recommendation_provider.py`、`src/lucking/models/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`、`src/lucking/services/broker_recommendation.py`、`src/lucking/flows/broker_recommendation.py`
- [ ] T002 [P] 在 `.env.example` 增加不含秘密的 `BROKER_RECOMMENDATION_*` 配置示例，并复用现有 `TUSHARE_TOKEN`/`TUSHARE_API_URL` 名称
- [ ] T003 [P] 创建金股契约、单元与集成测试模块骨架 `tests/contract/test_broker_recommendation_provider.py`、`tests/contract/test_tushare_broker_recommend.py`、`tests/unit/test_broker_recommendation_config.py`、`tests/unit/test_broker_recommendation_identity.py`、`tests/unit/test_broker_recommendation_service.py`、`tests/unit/test_broker_recommendation_logging.py`、`tests/integration/test_broker_recommendation_repository.py`、`tests/integration/test_broker_recommendation_mysql.py`、`tests/integration/test_broker_recommendation_flow.py`、`tests/integration/test_broker_recommendation_capacity.py`

---

## 阶段 2：基础能力（阻塞性前置条件）

**目的**：先固定供应商无关契约、共享配置和数据库结构；此阶段完成前不得开始任何用户故事实现。

**⚠️ CRITICAL**：T004–T006 必须先写成失败测试，再执行对应实现任务。

- [ ] T004 [P] 为四表字段、外键、唯一键、`utf8mb4_bin` 券商名称语义、revision `002 → 003` 和空库升级编写失败测试 `tests/integration/test_broker_recommendation_mysql.py`
- [ ] T005 [P] 为规范 DTO、统一错误、覆盖证据、1,000 条 Memory Provider 和替代 Provider golden semantics 编写失败契约测试 `tests/contract/test_broker_recommendation_provider.py`
- [ ] T006 [P] 为 Provider、时区、日志、1,500 秒截止时间、30 分钟及时目标、固定 1,000 行上限及 Token 延迟校验编写失败测试 `tests/unit/test_broker_recommendation_config.py`
- [ ] T007 根据 Provider 契约实现 `BrokerRecommendationRequest`、`ProviderBrokerRecommendation`、`RetrievalEvidence`、批次 DTO、统一异常和 `BrokerRecommendationProvider` Protocol `src/lucking/ports/broker_recommendation_provider.py`
- [ ] T008 根据数据模型实现 `BrokerRecommendation`、`BrokerRecommendationSyncRun`、`BrokerRecommendationSyncAttempt`、`BrokerRecommendationSyncIssue` 和本域状态枚举 `src/lucking/models/broker_recommendation.py`
- [ ] T009 创建 revision `003` 的四表迁移、精确排序规则、索引、检查约束和循环 FK，并修复模型发现 `migrations/versions/003_create_broker_recommendation_tables.py`、`migrations/env.py`
- [ ] T010 实现金股 Provider、时区、日志、截止时间、及时性和固定 row cap 配置及校验，同时保持既有配置兼容 `src/lucking/config.py`
- [ ] T011 实现金股独立 Provider Registry、注册/构造 API 和按选择延迟读取 Tushare Token 的工厂入口 `src/lucking/integrations/registry.py`
- [ ] T012 定义 `AttemptClaim`、身份候选/解析结果、发布记录、同步计数、问题 DTO、查询 DTO 和 `BrokerRecommendationRepository` Protocol `src/lucking/repositories/broker_recommendation.py`

**检查点**：四表、Provider-neutral Port、配置和 Repository 契约可用；用户故事可开始实施。

---

## 阶段 3：用户故事 1 - 按月自动保存券商金股（优先级：P1）🎯 MVP

**目标**：北京时间每月 3、4 日 12:00 获取各自计划时点所属当前月份的有效券商金股，
解析为稳定股票身份并保存，支持内部按月份、券商或股票查询。

**独立测试**：用包含多个券商、同券商多股票、同股票多券商的 Memory/Tushare fixture，
分别以 3 日和 4 日原计划时点运行；验证目标月一致、有效推荐全部保存、跨券商同股不覆盖，
且只调用 `broker_recommend` 四字段。

### 用户故事 1 测试（必须先失败）

- [ ] T013 [P] [US1] 编写 Tushare 唯一端点、仅 `month` 参数、精确四字段、月份和 `.SH/.SZ/.BJ` 映射的失败契约测试 `tests/contract/test_tushare_broker_recommend.py`
- [ ] T014 [P] [US1] 编写原计划时间推导当前月、跨年月份、基础字段校验、稳定股票身份与内部查询参数的失败单元测试 `tests/unit/test_broker_recommendation_service.py`
- [ ] T015 [P] [US1] 编写 run/attempt 创建、股票映射解析、基础推荐插入、唯一键和按月/券商/股票查询的失败集成测试 `tests/integration/test_broker_recommendation_repository.py`
- [ ] T016 [P] [US1] 编写 Prefect runtime 计划时点、Cron `0 12 3,4 * *`、`Asia/Shanghai`、3/4 日独立 run 和 P1 端到端的失败测试 `tests/integration/test_broker_recommendation_flow.py`
- [ ] T017 [P] [US1] 编写 Memory Provider 1,000 条完整候选在 30 分钟预算内成功保存的失败容量测试 `tests/integration/test_broker_recommendation_capacity.py`

### 用户故事 1 实现

- [ ] T018 [US1] 实现只调用 `broker_recommend`、只请求 `month,broker,ts_code,name` 并输出规范批次的 Tushare Adapter `src/lucking/integrations/tushare/broker_recommendation_provider.py`
- [ ] T019 [US1] 将 `tushare` 金股工厂注册到独立 Registry，并注入 Client、row cap、deadline 相关依赖 `src/lucking/integrations/registry.py`
- [ ] T020 [US1] 实现数据库原子创建基础 run/attempt、读取股票 Provider 映射与规范键、成功插入推荐及内部查询 `src/lucking/repositories/broker_recommendation.py`
- [ ] T021 [US1] 实现 `BrokerRecommendationSyncCommand/Result`、原计划时间推导目标月、Provider 调用、基础验证、身份解析和成功发布 `src/lucking/services/broker_recommendation.py`
- [ ] T022 [US1] 实现从 Prefect runtime 读取 `scheduled_start_time`、组装 Service 并返回规范结果的 `retries=0` Flow `src/lucking/flows/broker_recommendation.py`
- [ ] T023 [US1] 注册 `broker-recommendation-sync/default`、Cron、时区、slug、并发 1 和 `ENQUEUE` 调度 `prefect.yaml`
- [ ] T024 [US1] 实现 `list_month` 的月份、券商、`stock_id`、venue、代码筛选和稳定分页排序，并确保返回不含 Provider 字段 `src/lucking/services/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`

**检查点**：用户故事 1 可独立部署和验证；系统能够按计划保存当月金股并供内部查询。

---

## 阶段 4：用户故事 2 - 幂等刷新当月推荐（优先级：P2）

**目标**：相同批次、补跑和并发不会产生重复；4 日可新增、更新或确认，
但不得删除 3 日缺席推荐。

**独立测试**：3 日 fixture 保存 A/B/C；4 日 fixture 缺 A、更新 B 简称、保留 C、新增 D；
验证 A 保留、B 单行更新且首次时间不变、C 只刷新确认时间、D 新增。
连续 30 次重复和 10 组并发仍只有一个权威周期和一个业务键记录。

### 用户故事 2 测试（必须先失败）

- [ ] T025 [P] [US2] 编写 Unicode 空白规范化、区分其他字符、完全重复、同键冲突和同股不同券商的失败单元测试 `tests/unit/test_broker_recommendation_identity.py`
- [ ] T026 [P] [US2] 编写 3 日→4 日新增/更新/确认、缺席不删除、`first_seen` 保持和 `last_confirmed` 刷新的失败集成测试 `tests/integration/test_broker_recommendation_repository.py`
- [ ] T027 [P] [US2] 编写 MySQL `utf8mb4_bin`、首次并发 insert-or-read、同周期 10 组竞争和成功周期不可重开的失败测试 `tests/integration/test_broker_recommendation_mysql.py`

### 用户故事 2 实现

- [ ] T028 [US2] 实现券商 Unicode 空白规范化、批内完全重复去重、业务键冲突检测和规范候选摘要 `src/lucking/services/broker_recommendation.py`
- [ ] T029 [US2] 实现单事务推荐 upsert，保留 `first_seen_*`、刷新 `last_confirmed_*`、仅业务变化刷新 `updated_at`，且绝不处理缺席行 `src/lucking/repositories/broker_recommendation.py`
- [ ] T030 [US2] 实现 MySQL 首次并发原子 insert-or-read、唯一冲突重读加锁、相同 `flow_run_id` 重入和成功周期短路 `src/lucking/repositories/broker_recommendation.py`
- [ ] T031 [US2] 实现 added/updated/unchanged/duplicate 计数、候选 digest 与 run 成功 attempt 关联 `src/lucking/services/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`
- [ ] T032 [US2] 执行 30 次重复与 10 组并发幂等验收并将固定自动化场景补齐到 `tests/integration/test_broker_recommendation_mysql.py`

**检查点**：用户故事 1 与 2 均可独立验证；重复、补跑和 4 日刷新不会制造重复或误删。

---

## 阶段 5：用户故事 3 - 识别同步失败和数据质量问题（优先级：P3）

**目标**：限流、超时、权限、空响应、触顶、月份/字段/身份/冲突及持久化失败均有明确终态、
完整计数和脱敏问题；瞬态故障最多额外重试 3 次，已有推荐不受损。

**独立测试**：逐一模拟瞬态和确定性错误、0/999/1,000 行、未知股票、身份冲突和事务失败；
验证重试次数、run/attempt/issue、日志安全、推荐表摘要不变，并能用原计划时点补跑。

### 用户故事 3 测试（必须先失败）

- [ ] T033 [P] [US3] 编写网络/429/5xx 最多额外 3 次重试、30/120/300 秒退避、25 分钟 deadline、永久错误零重试、权限码映射及既有调用兼容的失败契约测试 `tests/contract/test_tushare_broker_recommend.py`、`tests/contract/test_tushare_client.py`
- [ ] T034 [P] [US3] 编写 0 行失败、999 行成功、1,000 行触顶失败、月份错配、无效字段、未知身份和推荐冲突的失败单元测试 `tests/unit/test_broker_recommendation_service.py`
- [ ] T035 [P] [US3] 编写失败计数、不可变 attempt、issue 脱敏、事务回滚、过期 `RUNNING → ABANDONED` 和显式补跑的失败集成测试 `tests/integration/test_broker_recommendation_repository.py`
- [ ] T036 [P] [US3] 编写开始/尝试/验证/终态日志白名单、Token/原始 payload 禁止、人工补跑原时点和 30 分钟及时性的失败测试 `tests/unit/test_broker_recommendation_logging.py`、`tests/integration/test_broker_recommendation_flow.py`
- [ ] T037 [P] [US3] 编写真实 MySQL 发布中途异常整体回滚及失败独立落盘的失败测试 `tests/integration/test_broker_recommendation_mysql.py`

### 用户故事 3 实现

- [ ] T038 [US3] 实现 Tushare 0/1–999/1,000 行完整性门禁、瞬态错误映射、最多 3 次 Adapter 重试和 deadline 保护 `src/lucking/integrations/tushare/broker_recommendation_provider.py`
- [ ] T039 [US3] 增加确定性权限码、安全业务错误摘要和 `Retry-After` 可选读取，同时保持现有交易日历与股票列表兼容 `src/lucking/integrations/tushare/client.py`
- [ ] T040 [US3] 实现无效/冲突分类、所有失败计数、每个未保存输入的可判断原因以及失败批次零发布 `src/lucking/services/broker_recommendation.py`
- [ ] T041 [US3] 实现失败 attempt/run、脱敏 issue、发布回滚后独立失败事务、租约过期 `ABANDONED` 和显式补跑 `src/lucking/repositories/broker_recommendation.py`
- [ ] T042 [US3] 实现结构化日志、timeliness、人工补跑参数校验、安全失败返回和 Flow 终态关联 `src/lucking/flows/broker_recommendation.py`
- [ ] T043 [US3] 扩展 JSONL 字段白名单与本域独立日志文件，确保错误和异常序列化不泄露秘密 `src/lucking/logging.py`

**检查点**：三个用户故事全部可独立验证；失败安全、重试、审计和五分钟排障能力完整。

---

## 阶段 6：完善与横切关注点

**目的**：完成文档、范围审计、迁移、真实来源上线门禁和全量质量验证。

- [ ] T044 [P] 更新配置、部署、人工补跑、缺席不删除、触顶阻断、五分钟排障和安全停止说明 `README.md`
- [ ] T045 [P] 在请求审计中证明没有调用 `broker_recommend` 之外端点、没有额外字段且 Memory Provider 可替换 Tushare `tests/contract/test_tushare_broker_recommend.py`、`tests/contract/test_broker_recommendation_provider.py`
- [ ] T046 验证空库与 revision `002` 两条迁移路径、ORM metadata 完整性和 downgrade 开发回滚 `migrations/env.py`、`migrations/versions/003_create_broker_recommendation_tables.py`
- [ ] T047 执行 `uv run ruff check .`、`uv run mypy src`、`uv run pytest`、`uv run pytest -m mysql`、`uv run alembic upgrade head` 并把证据写入 `specs/004-sync-broker-recommendations/verification.md`
- [ ] T048 按 `specs/004-sync-broker-recommendations/quickstart.md` 完成 3 日→4 日、失败保护、补跑、内部查询和五分钟排障验证并记录结果 `specs/004-sync-broker-recommendations/verification.md`
- [ ] T049 使用部署账户执行不打印 Token/响应的 `broker_recommend` 权限、频率和实际行数探测；触顶时阻断上线并记录替代 Provider/续取决策 `specs/004-sync-broker-recommendations/verification.md`
- [ ] T050 完成 FR/NFR/ED/SC 到测试与实现的追溯审计、确认无公共 API/前端/ClickHouse/Redis 范围扩张并签署完成状态 `specs/004-sync-broker-recommendations/verification.md`

---

## 依赖与执行顺序

### 阶段依赖

- **阶段 1 初始化**：无依赖，可立即开始。
- **阶段 2 基础能力**：依赖阶段 1；阻塞全部用户故事。
- **阶段 3 用户故事 1**：依赖阶段 2，是建议 MVP。
- **阶段 4 用户故事 2**：依赖阶段 2 的模型与契约，并复用 US1 的同步链路；
  可在 US1 基础同步稳定后合并。
- **阶段 5 用户故事 3**：依赖阶段 2 的错误/审计契约，并复用 US1 的 Adapter/Flow；
  可与 US2 并行开发。
- **阶段 6 完善**：依赖计划交付范围内的所有目标用户故事。

### 用户故事依赖图

```text
Setup → Foundation → US1 (MVP)
                    ├──→ US2
                    └──→ US3
US1 + US2 + US3 → Polish
```

- **US1（P1）**：基础能力完成后可开始；提供计划同步、保存和内部查询。
- **US2（P2）**：业务上扩展 US1 的写入路径，但测试数据和验收场景独立。
- **US3（P3）**：业务上扩展 US1 的失败路径，可与 US2 并行；不得依赖 US2 的缺席逻辑才能测试。

### 每个用户故事内部顺序

1. 先提交本阶段全部测试并确认因缺少目标行为而失败。
2. 实现模型/Adapter/Repository 等较低层能力。
3. 实现 Service。
4. 实现 Flow/Deployment 或查询入口。
5. 运行本故事独立测试并在检查点停止验证。

## 并行机会

- 阶段 1 的 T002、T003 可并行。
- 阶段 2 的 T004、T005、T006 可并行编写失败测试。
- US1 的 T013–T017 修改不同测试文件，可并行。
- 基础契约完成后，T018 Adapter 与 T020 Repository 可并行，再由 T021 Service 集成。
- US2 的 T025–T027 可并行；US3 的 T033–T037 可并行。
- US1 稳定后，US2 和 US3 可由不同开发者并行推进。
- 阶段 6 的 T044 与 T045 可并行。

## 并行执行示例

### 用户故事 1

```text
并行：
- T013：Tushare 请求契约测试
- T014：目标月与 Service 单元测试
- T015：Repository 集成测试
- T016：Prefect Flow/调度测试
- T017：1,000 条容量测试

契约完成后并行：
- T018：Tushare Adapter
- T020：Repository 基础发布与查询
```

### 用户故事 2

```text
并行：
- T025：空白、重复与冲突单元测试
- T026：3 日→4 日无删除集成测试
- T027：MySQL collation 与首次并发测试
```

### 用户故事 3

```text
并行：
- T033：Provider 重试契约
- T034：数据质量失败单元测试
- T035：失败审计 Repository 测试
- T036：日志与 Flow 测试
- T037：MySQL 原子回滚测试
```

## 实施策略

### MVP First（仅用户故事 1）

1. 完成阶段 1。
2. 完成阶段 2。
3. 完成阶段 3 的 T013–T024。
4. 停止并独立验证：3 日/4 日均查询当前月、合法数据入库、跨券商同股不覆盖、
   只调用 `broker_recommend` 四字段。
5. MVP 仅用于受控环境；生产上线仍需 US3 的失败保护与阶段 6 的真实来源门禁。

### 增量交付

1. Setup + Foundation：供应商无关契约与数据库就绪。
2. US1：按月采集、保存和查询。
3. US2：幂等、并发、3 日→4 日新增更新且不删除。
4. US3：重试、失败原子性、逐次审计和运维。
5. Polish：文档、完整门禁、真实来源探测与最终追溯。

### 并行团队策略

基础能力完成后：

- 开发者 A：US1 Adapter/Flow。
- 开发者 B：US1 Repository，随后进入 US2。
- 开发者 C：US3 错误、日志和失败审计。

所有合并都必须保持测试先于实现，并在每个故事检查点运行其独立测试集。

## 备注

- `[P]` 只用于没有未完成依赖且修改不同文件的任务。
- `[US1]/[US2]/[US3]` 与规格用户故事一一对应。
- 现有股票列表拥有 `stock_current` 与 `stock_provider_mapping`；金股任务不得修改其业务语义。
- 恰好 1,000 行的 Tushare 响应必须失败；不得通过调高 row cap 绕过。
- 任务实施中若需要范围变化，必须先更新 spec.md、plan.md 和相关契约。
