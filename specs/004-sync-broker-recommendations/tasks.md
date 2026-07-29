# 任务：每月券商金股同步

**输入**：`specs/004-sync-broker-recommendations/` 中的设计文档

**前置条件**：plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

> 任务文档必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**测试要求**：所有契约、数据模型、可观察行为和失败路径必须先编写失败测试，再实现；
真实 MySQL 测试不得用 SQLite 替代物理主键、数据库时间默认值、中文注释、固定租约、
数据库 UTC 过期判断、排序规则、首次并发认领、行锁和事务回滚门禁。

**第三方边界**：Tushare 只允许存在于独立 Adapter 与通用 Client 中。
领域层仅依赖 `BrokerRecommendationProvider`、规范 DTO 和统一异常；
Memory Provider 必须证明更换来源不修改 Service、Repository 或业务表契约。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**：可与同阶段其他标记任务并行，且修改不同文件
- **[Story]**：对应 `spec.md` 的用户故事
- 每个任务都包含明确文件路径

## 阶段 1：初始化（共享基础设施）

**目的**：建立独立金股垂直切片、测试文件和安全配置入口，不改变现有股票列表行为。

- [X] T001 创建金股领域包骨架与导出文件 `src/lucking/ports/broker_recommendation_provider.py`、`src/lucking/integrations/tushare/broker_recommendation_provider.py`、`src/lucking/models/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`、`src/lucking/services/broker_recommendation.py`、`src/lucking/flows/broker_recommendation.py`
- [X] T002 [P] 在 `.env.example` 增加不含秘密的 Provider、页面上限、最大页数、Tushare 分页门禁及历史补跑配置示例，并复用现有 `TUSHARE_TOKEN`/`TUSHARE_API_URL` 名称
- [X] T003 [P] 创建金股契约、单元与集成测试模块骨架 `tests/contract/test_broker_recommendation_provider.py`、`tests/contract/test_tushare_broker_recommend.py`、`tests/unit/test_broker_recommendation_config.py`、`tests/unit/test_broker_recommendation_identity.py`、`tests/unit/test_broker_recommendation_service.py`、`tests/unit/test_broker_recommendation_logging.py`、`tests/integration/test_broker_recommendation_repository.py`、`tests/integration/test_broker_recommendation_mysql.py`、`tests/integration/test_broker_recommendation_flow.py`、`tests/integration/test_broker_recommendation_capacity.py`

---

## 阶段 2：基础能力（阻塞性前置条件）

**目的**：先固定供应商无关契约、共享配置和四表物理结构；此阶段完成前不得开始用户故事实现。

**⚠️ CRITICAL**：T004–T008 必须先写成失败测试，再执行 T009–T014。

- [X] T004 为四张真实 MySQL 表编写失败模式测试，逐表断言 `id BIGINT AUTO_INCREMENT` 物理主键、UUID `UNIQUE` 业务标识、业务唯一键、外键、`created_at/updated_at` 数据库默认值与 `ON UPDATE`，并断言 attempt 的非空 `lease_expires_at` 后比较 ORM metadata、迁移声明和 `SHOW CREATE TABLE` `tests/integration/test_broker_recommendation_mysql.py`
- [X] T005 为四张表的中文表注释和每列准确非空中文注释编写失败测试，通过 `information_schema.tables/columns` 与 `data-model.md` 期望清单逐项比对，覆盖 `id` 的“主键ID”及四个固定表注释 `tests/integration/test_broker_recommendation_mysql.py`
- [X] T006 为空库 `base → head`、既有 revision `002 → 003`、重复 upgrade 和开发 downgrade 编写失败迁移测试，确认迁移前后既有股票表数据与约束不受损 `tests/integration/test_broker_recommendation_mysql.py`
- [X] T007 [P] 为规范 DTO、统一错误、分页覆盖证据、2,500 条 Memory Provider 和替代 Provider golden semantics 编写失败契约测试 `tests/contract/test_broker_recommendation_provider.py`
- [X] T008 [P] 为 Provider、时区、日志、1,500 秒截止时间、固定 2,100 秒且必须大于 Provider deadline 的运行租约、30 分钟及时目标、固定 1,000 页面上限、最大页数、分页默认关闭及 Token 延迟校验编写失败测试 `tests/unit/test_broker_recommendation_config.py`
- [X] T009 根据 Provider 契约实现 `BrokerRecommendationRequest`、`ProviderBrokerRecommendation`、`RetrievalEvidence`、批次 DTO、统一异常和 `BrokerRecommendationProvider` Protocol `src/lucking/ports/broker_recommendation_provider.py`
- [X] T010 根据数据模型实现四个 ORM 模型，以 `id` 作为 BIGINT 自增物理主键、UUID 作为唯一业务标识，补齐数据库维护的 `created_at/updated_at`、attempt 的非空 `lease_expires_at`、业务唯一键、外键、检查约束和逐表逐字段中文注释 `src/lucking/models/broker_recommendation.py`
- [X] T011 创建 revision `003`，精确实现四表物理主键、UUID 唯一键、数据库维护时间、attempt 固定租约字段、中文表/列注释、`utf8mb4_bin`、运行身份检查约束、分页计数、索引及循环外键，并修复模型发现 `migrations/versions/003_create_broker_recommendation_tables.py`、`migrations/env.py`
- [X] T012 [P] 实现金股 Provider、时区、日志、1,500 秒截止时间、固定 2,100 秒且大于 Provider deadline 的不可续租运行租约、及时性、固定 page limit、max pages 和 Tushare 分页门禁配置及校验，同时保持既有配置兼容 `src/lucking/config.py`
- [X] T013 [P] 实现金股独立 Provider Registry、注册/构造 API 和按选择延迟读取 Tushare Token 的工厂入口 `src/lucking/integrations/registry.py`
- [X] T014 定义 `AttemptClaim`、包含 `active_attempt_lease_expires_at/active_attempt_lease_expired` 的补跑运行状态、月份解析结果、身份候选/解析结果、发布记录、同步计数、问题 DTO、查询 DTO 和 `BrokerRecommendationRepository` Protocol `src/lucking/repositories/broker_recommendation.py`

**检查点**：四表迁移及真实 DDL、中文元数据、Provider-neutral Port、配置和 Repository 契约均可验证。

---

## 阶段 3：用户故事 1 - 按月自动保存券商金股（优先级：P1）🎯 MVP

**目标**：北京时间每月 3、4 日 12:00 获取各自计划时点所属当前月份的有效券商金股，
解析为稳定股票身份并保存，支持内部按月份、券商或股票查询。

**独立测试**：用包含多个券商、同券商多股票、同股票多券商的 Memory/Tushare fixture，
分别以 3 日和 4 日原计划时点运行；验证目标月一致、分页有效推荐全部保存、跨券商同股不覆盖，
且只调用 `broker_recommend` 四字段和经验证的技术分页参数。

### 用户故事 1 测试（必须先失败）

- [X] T015 [P] [US1] 编写 Tushare 唯一端点、分页关闭时仅 `month`、启用时精确 `month/limit/offset`、四字段、月份和 `.SH/.SZ/.BJ` 映射的失败契约测试 `tests/contract/test_tushare_broker_recommend.py`
- [X] T016 [P] [US1] 编写原计划时间推导当前月、跨年月份、基础字段校验、稳定股票身份与内部查询参数的失败单元测试 `tests/unit/test_broker_recommendation_service.py`
- [X] T017 [P] [US1] 编写 run/attempt 创建、股票映射解析、基础推荐插入、业务唯一键和按月/券商/股票查询的失败集成测试 `tests/integration/test_broker_recommendation_repository.py`
- [X] T018 [P] [US1] 编写 Prefect runtime 计划时点、Cron `0 12 3,4 * *`、`Asia/Shanghai`、3/4 日独立 run 和 P1 端到端的失败测试 `tests/integration/test_broker_recommendation_flow.py`
- [X] T019 [P] [US1] 编写 Memory Provider 2,500 条完整候选及 `1,000/1,000/500` 三页数据在 30 分钟预算内成功保存的失败容量测试 `tests/integration/test_broker_recommendation_capacity.py`

### 用户故事 1 实现

- [X] T020 [US1] 实现只调用 `broker_recommend`、只请求四字段、分页启用后按 `limit/offset` 满页继续短页结束并输出分页覆盖证据的 Tushare Adapter `src/lucking/integrations/tushare/broker_recommendation_provider.py`
- [X] T021 [US1] 将 `tushare` 金股工厂注册到独立 Registry，并注入 Client、page limit、max pages、分页门禁和 deadline 相关依赖 `src/lucking/integrations/registry.py`
- [X] T022 [US1] 实现数据库原子创建基础 run/attempt、读取股票 Provider 映射与规范键、成功插入推荐及内部查询 `src/lucking/repositories/broker_recommendation.py`
- [X] T023 [US1] 实现 `ScheduledBrokerRecommendationSyncCommand`、规范结果、原计划时间推导目标月、Provider 调用、基础验证、身份解析和成功发布 `src/lucking/services/broker_recommendation.py`
- [X] T024 [US1] 实现从 Prefect runtime 读取 `scheduled_start_time`、组装 Service 并返回规范结果的 `retries=0` Flow `src/lucking/flows/broker_recommendation.py`
- [X] T025 [US1] 注册 `broker-recommendation-sync/default`、Cron、时区、slug、并发 1 和 `ENQUEUE` 调度 `prefect.yaml`
- [X] T026 [US1] 实现 `list_month` 的月份、券商、`stock_id`、venue、代码筛选和稳定分页排序，并确保返回不含 Provider 字段和 BIGINT 物理主键 `src/lucking/services/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`

**检查点**：用户故事 1 可独立部署和验证；系统能够按计划保存当月金股并供内部查询。

---

## 阶段 4：用户故事 2 - 幂等刷新与历史补跑（优先级：P2）

**目标**：稳定 run key、重复与并发不得制造重复；历史区间按月补齐，
同批次失败月份转换为原 run 的 Retry，120 月接受而 121 月原子拒绝；
计划与补跑并发处理同月时分别审计，且相同业务唯一键不产生重复记录。

**独立测试**：3 日保存 A/B/C，4 日缺 A、更新 B、保留 C、新增 D；
固定批次补跑 24 个月后重放成功、失败、有效运行及过期运行月份；
验证 120/121 月边界，并执行 10 组计划/补跑同月并发，确认两个 run 可追踪、
相同 `recommendation_month + broker_name + stock_id` 不重复；
股票代码保持稳定，不比较股票简称等其他属性的跨 run 最终版本。

### 用户故事 2 测试（必须先失败）

- [X] T027 [P] [US2] 编写 Unicode 空白规范化、区分其他字符、完全重复、同键冲突和同股不同券商的失败单元测试 `tests/unit/test_broker_recommendation_identity.py`
- [X] T028 [P] [US2] 编写 3 日→4 日新增/更新/确认、缺席不删除、`first_seen` 保持和 `last_confirmed` 刷新的失败集成测试 `tests/integration/test_broker_recommendation_repository.py`
- [X] T029 [P] [US2] 编写稳定 run key 失败测试：计划键只含类型、slug、原计划 UTC 和目标月，补跑键只含类型、批次键和目标月；Provider、配置、`scope_fingerprint`、实际开始时间变化不得改变身份 `tests/unit/test_broker_recommendation_service.py`
- [X] T030 [US2] 编写同批次月份状态矩阵失败测试：attempt 认领时由数据库 UTC 设置固定 35 分钟 `lease_expires_at`，到期前 `RUNNING` 保持 `IN_PROGRESS`，到期后在数据库事务内再次确认并先 `ABANDONED` 再对原 `run_id` Retry；同时覆盖不存在转 Backfill、`SUCCEEDED` 跳过、`FAILED` Retry、Worker 时钟偏移不影响判断且不得创建第二个 BACKFILL run `tests/integration/test_broker_recommendation_flow.py`、`tests/integration/test_broker_recommendation_mysql.py`
- [X] T031 [US2] 编写首尾均计入的 1/120/121 月、未来月、反向及空范围失败测试，断言 120 月逐月解析而 121 月在调用 Service 和创建任何 run 前整体拒绝 `tests/integration/test_broker_recommendation_flow.py`
- [X] T032 [US2] 编写真实 MySQL 跨运行类型并发失败测试，执行 10 组计划 run 与补跑 run 同月并发命中相同推荐，断言两个 run 分别可追踪且 `recommendation_month + broker_name + stock_id` 无重复；股票代码保持稳定，不比较股票简称等其他属性的最终版本 `tests/integration/test_broker_recommendation_mysql.py`
- [X] T033 [US2] 编写相同运行身份 30 次重复、同批次 10 组首次并发、24 月补跑重放、新批次同月刷新及成功运行不可重开的失败测试 `tests/integration/test_broker_recommendation_mysql.py`

### 用户故事 2 实现

- [X] T034 [US2] 实现券商 Unicode 空白规范化、批内完全重复去重、业务键冲突检测和规范候选摘要 `src/lucking/services/broker_recommendation.py`
- [X] T035 [US2] 实现单事务推荐 upsert，保留 `first_seen_*`、刷新 `last_confirmed_*`、由数据库维护 `updated_at`，且绝不处理缺席行 `src/lucking/repositories/broker_recommendation.py`
- [X] T036 [US2] 实现两类稳定 run key、运行类型字段互斥、MySQL 首次并发原子 insert-or-read、唯一冲突重读加锁、相同 `flow_run_id` 重入和成功运行短路 `src/lucking/services/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`
- [X] T037 [US2] 实现 `BackfillBrokerRecommendationMonthCommand`、`RetryBrokerRecommendationSyncCommand` 和 `resolve_backfill_month` 状态矩阵；认领时用数据库 UTC 写入固定 35 分钟租约，不续租，查询返回数据库计算的过期标志，Retry 时锁定原 run/attempt 并原子复核过期后执行 `ABANDONED`、issue 和新 attempt `src/lucking/services/broker_recommendation.py`、`src/lucking/repositories/broker_recommendation.py`
- [X] T038 [US2] 实现首尾均计入且先整体校验的历史月份展开函数，严格接受 1–120 月并原子拒绝 121 月、未来月、反向或空范围 `src/lucking/flows/broker_recommendation.py`
- [X] T039 [US2] 实现逐月隔离执行和汇总的补跑 Flow，按解析结果分派 Backfill/Skip/Retry/In-progress，并注册无 Cron 的 `broker-recommendation-backfill/manual` `src/lucking/flows/broker_recommendation.py`、`prefect.yaml`
- [X] T040 [US2] 加固真实 MySQL 推荐发布的锁定与 upsert，使计划和补跑不同 run 同月并发时保留两个审计结果，并以 `recommendation_month + broker_name + stock_id` 唯一约束只保留一条推荐；校验股票代码稳定，股票简称等其他属性允许按事务提交顺序落值且不实现跨 run 版本比较 `src/lucking/repositories/broker_recommendation.py`

**检查点**：用户故事 2 可独立验证；稳定身份、固定租约失败月恢复、120/121 边界及跨类型并发唯一性全部通过。

---

## 阶段 5：用户故事 3 - 识别同步失败和数据质量问题（优先级：P3）

**目标**：限流、超时、权限、空响应、触顶、月份/字段/身份/冲突及持久化失败均有明确终态、
完整分页计数和脱敏问题；瞬态故障最多额外重试 3 次，已有推荐不受损。

**独立测试**：逐一模拟瞬态和确定性错误、空首屏、满页续取、空终止页、重复页、
offset 未前进、最大页数、未知股票、身份冲突和事务失败；验证重试次数、
run/attempt/issue、日志安全、推荐表摘要不变，并能在数据库 UTC 租约到期后引用原 `run_id` 重试。

### 用户故事 3 测试（必须先失败）

- [X] T041 [P] [US3] 编写整个月份跨页共享最多额外 3 次重试、30/120/300 秒退避、25 分钟 deadline、永久错误零重试、权限码映射及既有调用兼容的失败契约测试 `tests/contract/test_tushare_broker_recommend.py`、`tests/contract/test_tushare_client.py`
- [X] T042 [P] [US3] 编写 0 行首屏失败、未验证分页的 1,000 行触顶失败、已验证分页满页继续/短页或空页结束、月份错配、无效字段、未知身份和推荐冲突的失败单元测试 `tests/unit/test_broker_recommendation_service.py`
- [X] T043 [P] [US3] 编写失败计数、终态 attempt 不可变、issue 脱敏、事务回滚及显式重试的失败集成测试 `tests/integration/test_broker_recommendation_repository.py`
- [X] T044 [P] [US3] 编写开始/尝试/分页/验证/终态日志白名单、Token/原始 payload/BIGINT 物理主键禁止、原 `run_id` 重试和计划运行 30 分钟及时性的失败测试 `tests/unit/test_broker_recommendation_logging.py`、`tests/integration/test_broker_recommendation_flow.py`
- [X] T045 [P] [US3] 编写真实 MySQL 发布中途异常整体回滚及失败独立落盘的失败测试 `tests/integration/test_broker_recommendation_mysql.py`

### 用户故事 3 实现

- [X] T046 [US3] 实现 Tushare 空首屏、分页未验证触顶、满页继续、短页/空页结束、重复整页、offset 未前进、max pages 和中途失败门禁，以及瞬态错误映射、共享 3 次额外重试预算和 deadline 保护 `src/lucking/integrations/tushare/broker_recommendation_provider.py`
- [X] T047 [US3] 增加确定性权限码、安全业务错误摘要和 `Retry-After` 可选读取，同时保持现有交易日历与股票列表兼容 `src/lucking/integrations/tushare/client.py`
- [X] T048 [US3] 实现无效/冲突分类、所有失败计数、每个未保存输入的可判断原因以及失败批次零发布 `src/lucking/services/broker_recommendation.py`
- [X] T049 [US3] 实现失败 attempt/run、脱敏 issue、发布回滚后独立失败事务、终态不可重开，以及仅在数据库 UTC 固定租约过期或运行已失败时按原 `run_id` 显式重试 `src/lucking/repositories/broker_recommendation.py`
- [X] T050 [US3] 实现包含运行类型与分页证据的结构化日志、计划 timeliness、按原 `run_id` 的失败重试 Flow、安全失败返回和终态关联，并注册无 Cron 的 `broker-recommendation-retry/manual` `src/lucking/flows/broker_recommendation.py`、`prefect.yaml`
- [X] T051 [US3] 扩展 JSONL 字段白名单与本域独立日志文件，确保错误和异常序列化不泄露秘密、原始数据或 BIGINT 物理主键 `src/lucking/logging.py`

**检查点**：三个用户故事全部可独立验证；失败安全、重试、审计和五分钟排障能力完整。

---

## 阶段 6：完善与横切关注点

**目的**：完成文档、真实 DDL、迁移、真实来源上线门禁和全量质量验证。

- [X] T052 [P] 更新配置、固定 35 分钟数据库 UTC 运行租约、部署、历史区间补跑、同批次失败月 Retry、120/121 月边界、跨运行类型并发唯一性、缺席不删除、分页门禁、五分钟排障和安全停止说明 `README.md`
- [X] T053 [P] 在请求审计中证明没有调用 `broker_recommend` 之外端点、没有额外字段且 Memory Provider 可替换 Tushare `tests/contract/test_tushare_broker_recommend.py`、`tests/contract/test_broker_recommendation_provider.py`
- [X] T054 执行空库与 revision `002` 两条迁移路径及重复 upgrade/downgrade 验证，并核对四表 ORM、revision `003` 和真实 `SHOW CREATE TABLE` 的 BIGINT 主键、UUID 唯一键、数据库时间默认值、`ON UPDATE`、attempt 非空 `lease_expires_at`、外键、检查约束、索引和排序规则 `tests/integration/test_broker_recommendation_mysql.py`、`migrations/versions/003_create_broker_recommendation_tables.py`
- [X] T055 查询真实 MySQL `information_schema.tables/columns`，逐表逐列验证四个中文表注释及所有非空中文字段注释与 `data-model.md` 完全一致，并将 DDL 与注释证据写入 `specs/004-sync-broker-recommendations/verification.md`
- [X] T056 执行 `uv run ruff check .`、`uv run mypy src`、`uv run pytest`、`uv run pytest -m mysql`、`uv run alembic upgrade head` 并把结果写入 `specs/004-sync-broker-recommendations/verification.md`
- [X] T057 按 `specs/004-sync-broker-recommendations/quickstart.md` 完成 3 日→4 日、24 月初始化补跑、数据库 UTC 固定 35 分钟租约到期前 `IN_PROGRESS` 与到期后原 run Retry、120 月接受、121 月零 run 拒绝、10 组计划/补跑同月并发业务键无重复且不比较其他属性版本、新批次刷新、失败保护、内部查询和五分钟排障验证 `specs/004-sync-broker-recommendations/verification.md`
- [X] T058 使用部署账户或供应商沙箱执行不打印 Token/响应的 `broker_recommend` 权限、频率和 `limit/offset` 前进/短页终止/重复探测；未通过时保持分页关闭并记录阻断或替代 Provider 决策 `specs/004-sync-broker-recommendations/verification.md`
- [X] T059 完成 FR/NFR/ED/SC、宪章 VI 到测试与实现的追溯审计，确认无公共 API/前端/ClickHouse/Redis 范围扩张并签署完成状态 `specs/004-sync-broker-recommendations/verification.md`

---

## 依赖与执行顺序

### 阶段依赖

- **阶段 1 初始化**：无依赖，可立即开始。
- **阶段 2 基础能力**：依赖阶段 1；阻塞全部用户故事。
- **阶段 3 用户故事 1**：依赖阶段 2，是建议 MVP。
- **阶段 4 用户故事 2**：依赖阶段 2 的模型与契约，并复用 US1 的同步发布链路。
- **阶段 5 用户故事 3**：依赖阶段 2 的错误/审计契约，并复用 US1 的 Adapter/Flow；可与 US2 并行开发。
- **阶段 6 完善**：依赖计划交付范围内的全部用户故事。

### 用户故事依赖图

```text
Setup → Foundation → US1 (MVP)
                    ├──→ US2（生产必需）
                    └──→ US3
US1 + US2 + US3 → Polish
```

- **US1（P1）**：基础能力完成后可开始；提供计划同步、保存和内部查询。
- **US2（P2）**：扩展 US1 的写入路径，但其 run key、补跑状态机、边界与并发测试可独立验收。
- **US3（P3）**：扩展 US1 的失败路径，可与 US2 并行；不得依赖 US2 才能验证失败安全。

### 每个用户故事内部顺序

1. 先提交本阶段全部测试并确认因缺少目标行为而失败。
2. 实现模型、Adapter、Repository 等较低层能力。
3. 实现 Service。
4. 实现 Flow/Deployment 或查询入口。
5. 运行本故事独立测试并在检查点停止验证。

## 并行机会

- 阶段 1 的 T002、T003 可并行。
- 阶段 2 中 T007、T008 可并行编写；T012、T013 可并行实现。
- US1 的 T015–T019 修改不同测试文件，可并行；T020 Adapter 与 T022 Repository 可并行。
- US2 的 T027–T029 修改不同测试文件可并行；T034 与 T036 可并行后再集成 T037–T040。
- US3 的 T041–T045 可并行；US1 稳定后 US2 和 US3 可由不同开发者并行推进。
- 阶段 6 的 T052、T053 可并行。

## 并行执行示例

### 用户故事 1

```text
并行：
- T015：Tushare 请求契约测试
- T016：目标月与 Service 单元测试
- T017：Repository 集成测试
- T018：Prefect Flow/调度测试
- T019：2,500 条多页容量测试

契约完成后并行：
- T020：Tushare Adapter
- T022：Repository 基础发布与查询
```

### 用户故事 2

```text
并行：
- T027：空白、重复与冲突单元测试
- T028：3 日→4 日无删除集成测试
- T029：稳定 run key 单元测试

顺序高风险链：
- T030 → T037：固定租约有效/过期月份解析与原 run Retry
- T031 → T038/T039：120/121 月整体校验与补跑 Flow
- T032 → T040：计划/补跑跨运行类型并发
```

### 用户故事 3

```text
并行：
- T041：Provider 重试契约
- T042：数据质量失败单元测试
- T043：失败审计 Repository 测试
- T044：日志与 Flow 测试
- T045：MySQL 原子回滚测试
```

## 实施策略

### MVP First（仅用户故事 1）

1. 完成阶段 1。
2. 完成阶段 2，特别是四表迁移、真实 DDL 和中文注释失败测试。
3. 完成阶段 3 的 T015–T026。
4. 停止并独立验证：3 日/4 日均查询当前月、合法数据入库、跨券商同股不覆盖、
   只调用 `broker_recommend` 四字段和已验证的技术分页参数。
5. MVP 仅用于受控开发验证；生产上线必须继续完成 US2、US3 和阶段 6。

### 增量交付

1. Setup + Foundation：供应商无关契约、四表迁移和数据库治理就绪。
2. US1：按月采集、保存和查询。
3. US2：稳定 run key、固定租约恢复、幂等、120/121 边界及跨类型并发唯一性。
4. US3：重试、失败原子性、逐次审计和运维。
5. Polish：真实 DDL/注释、完整门禁、真实来源探测与最终追溯。

### 并行团队策略

基础能力完成后：

- 开发者 A：US1 Adapter/Flow。
- 开发者 B：US1 Repository，随后进入 US2。
- 开发者 C：US3 错误、日志和失败审计。

所有合并都必须保持测试先于实现，并在每个故事检查点运行其独立测试集。

## 备注

- `[P]` 只用于没有未完成依赖且修改不同文件的任务。
- `[US1]/[US2]/[US3]` 与规格用户故事一一对应。
- 四张新表均无宪章 VI 例外；ORM、迁移和真实 DDL 必须一致。
- 现有股票列表拥有 `stock_current` 与 `stock_provider_mapping`；金股任务不得修改其业务语义。
- Tushare 分页未通过真实续取门禁时，恰好 1,000 行必须失败；门禁通过后必须满页继续、
  短页结束，并保留重复页、未前进和最大页数保护。
- 任务实施中若需要范围变化，必须先更新 spec.md、plan.md 和相关契约。
