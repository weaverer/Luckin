---

description: "每日股票列表同步的依赖有序实施任务"
---

# 任务：每日股票列表同步

**输入**：`/specs/003-stock-list-sync/` 中的设计文档

**前置条件**：plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

**范围约束**：真实数据源只允许调用 Tushare `stock_basic`；首期 `CN-S` 固定覆盖
上海、深圳和北京三个交易场所，不允许配置 venue 子集，并按
`SSE/SZSE/BSE × L/D/P/G` 获取 12 个分区，且每次只请求
`ts_code,symbol,name,exchange,curr_type,list_status,list_date,delist_date`。
不实现行情、成交、财务、公司、交易日历、其他证券品种、快照历史、公共 API 或前端。

**测试原则**：所有可观察行为、公共契约、数据模型及失败路径均先写失败测试，再实现；
第三方集成必须覆盖供应商无关端口、Tushare Adapter、配置选择、契约测试和 Memory Provider
替代实现。

## 阶段 1：初始化（共享基础设施）

**目的**：建立股票列表功能的文件边界和无秘密示例配置。

- [X] T001 [P] 创建股票列表模块骨架并补齐包导出文件 `src/lucking/ports/__init__.py`、`src/lucking/models/__init__.py`、`src/lucking/repositories/__init__.py`、`src/lucking/services/__init__.py`、`src/lucking/flows/__init__.py`、`src/lucking/integrations/tushare/__init__.py`
- [X] T002 [P] 在 `.env.example` 增加不含真实 Token 的 `STOCK_LIST_PROVIDER`、固定 `CN-S`、时区、日志文件、及时性阈值和完整性配置示例，且不提供 venue 子集配置

---

## 阶段 2：基础能力（阻塞性前置条件）

**目的**：完成所有用户故事共用的规范模型、存储模式、配置选择和兼容基础设施。

**⚠️ 关键门禁**：本阶段完成并通过测试前，不开始任何用户故事实现。

### 基础能力测试（先写并确认失败）

- [X] T003 [P] 为固定 `CN-S`、拒绝其他范围/venue 子集、未知 Provider 和仅在选中 Tushare 时读取 Token 编写失败测试 `tests/unit/test_stock_list_config.py`
- [X] T004 [P] 为仅含 `scope_code` 的供应商无关请求、固定三 venue 覆盖证明、规范 DTO、统一异常和 Memory Provider 一致性编写失败契约测试 `tests/contract/test_stock_list_provider.py`
- [X] T005 [P] 为 `TushareClient.call(..., allow_empty=False)` 的默认兼容行为及显式允许空表编写失败回归测试 `tests/contract/test_tushare_client.py`
- [X] T006 [P] 为四张股票列表表、唯一键、外键、状态约束、UTC 时间字段和索引编写失败数据库测试 `tests/integration/test_stock_list_repository.py`
- [X] T007 [P] 为可配置 JSONL 文件、字段白名单、10 MiB/5 个归档、脱敏和可配置及时性阈值编写失败测试 `tests/unit/test_stock_list_logging.py`

### 基础能力实现

- [X] T008 定义只接受固定 `CN-S` 且不暴露 `venue_codes` 的请求、`ScopeCode`、`VenueCode`、`ListingStatus`、规范 DTO、覆盖证明、`StockListProvider` Protocol 和统一 Provider 异常 `src/lucking/ports/stock_list_provider.py`
- [X] T009 定义 `stock_current`、`stock_provider_mapping`、`stock_list_sync_run`、`stock_list_sync_issue` 的 SQLAlchemy 模型、枚举和约束 `src/lucking/models/stock_list.py`
- [X] T010 创建四张股票列表表及其唯一键、外键、检查约束和查询索引的 Alembic 迁移 `migrations/versions/002_create_stock_list_tables.py`
- [X] T011 [P] 实现股票列表 Provider、固定 `CN-S`、拒绝 venue 子集、时区、日志、25 分钟获取截止时间、30 分钟及时性和完整性阈值配置校验 `src/lucking/config.py`
- [X] T012 实现不引用具体 Adapter 的通用 `StockListProviderFactory` Registry 与显式 Provider 选择机制，禁止自动回退或混合来源 `src/lucking/integrations/registry.py`
- [X] T013 [P] 兼容性扩展通用 Client 的 `allow_empty` 参数并保持交易日历默认空结果失败语义 `src/lucking/integrations/tushare/client.py`
- [X] T014 [P] 将 JSONL 文件名、允许字段和及时性目标参数化，同时保持现有交易日历日志行为 `src/lucking/logging.py`
- [X] T015 定义 Repository 的周期认领、当前列表查询、成功发布和独立失败记录接口及公共结果类型 `src/lucking/repositories/stock_list.py`

**检查点**：规范模型、数据库模式、配置/依赖注入和公共基础设施可用；业务层无需导入
Tushare SDK、字段、端点或错误码。

---

## 阶段 3：用户故事 1——每日获得最新股票列表（优先级：P1）🎯 MVP

**目标**：每天北京时间 09:00 获取固定覆盖上海、深圳和北京三个交易场所的完整股票列表，
仅保存规格允许的股票身份与列表字段，并向项目内部已完成授权的调用方提供当前列表筛选查询。

**独立测试**：在空数据库中用 HTTPX `MockTransport` 返回覆盖 12 个分区的合法数据，触发
固定计划时点后断言只调用 `stock_basic`、只请求 8 个字段、无 venue 子集参数、同步成功且
当前列表可按交易所、状态、代码和名称稳定分页查询；周末和休市日也执行，且不调用交易日历接口。

### 用户故事 1 测试（先写并确认失败）

- [X] T016 [P] [US1] 编写只允许 `stock_basic`、精确 8 字段、固定三交易所 × 四状态的 12 个唯一分区、禁止 venue 子集、币种/后缀/日期映射及合法空分区的 Adapter 失败契约测试 `tests/contract/test_tushare_stock_basic.py`
- [X] T017 [P] [US1] 使用 Memory Provider 编写首次同步、固定三 venue、北京时间业务日期、25 分钟 deadline、规范结果、内部授权责任边界及当前列表筛选/排序/分页的失败单元测试 `tests/unit/test_stock_list_service.py`
- [X] T018 [P] [US1] 编写首次批量 upsert、Provider 映射、成功计数、当前列表字段最小化和稳定排序的失败 MySQL 集成测试 `tests/integration/test_stock_list_repository.py`
- [X] T019 [P] [US1] 编写每日 09:00 `Asia/Shanghai` 调度、固定 `CN-S` 且无 venue 参数、周末执行、成功返回、无交易日历调用和计划到终态计时的失败 Flow 测试 `tests/integration/test_stock_list_flow.py`

### 用户故事 1 实现

- [X] T020 [P] [US1] 实现固定顺序的 12 分区 `TushareStockListProvider`、精确字段/参数请求、严格规范映射和覆盖证明，并在 Adapter 可构造后注册 `tushare` 工厂 `src/lucking/integrations/tushare/stock_list_provider.py`、`src/lucking/integrations/registry.py`
- [X] T021 [P] [US1] 实现首次同步所需的批量当前值/Provider 映射 upsert、成功计数提交和只返回允许字段的筛选查询 `src/lucking/repositories/stock_list.py`
- [X] T022 [US1] 实现固定 `CN-S`、供应商无关的同步命令、run_key、首次完整候选校验、身份创建、原子发布和仅供内部已授权调用方使用的当前列表查询 `src/lucking/services/stock_list.py`
- [X] T023 [US1] 实现计划/人工参数解析、Service 编排、成功终态和结构化计时日志的 Prefect Flow `src/lucking/flows/stock_list.py`
- [X] T024 [P] [US1] 配置 `stock-list-sync/股票列表同步`、`daily-stock-list`、`0 9 * * *`、`Asia/Shanghai`、仅含固定 `scope_code=CN-S` 且无 venue 子集的入口参数和并发限制 `prefect.yaml`

**检查点**：US1 可在干净数据库中独立运行并验证，是可交付 MVP。

---

## 阶段 4：用户故事 2——安全更新股票列表（优先级：P2）

**目标**：重复运行保持幂等，可信新增/属性变更原子发布；空批、截断、冲突、任一既有身份消失
或数据库失败时保持上一成功列表不变。

**独立测试**：以一批成功基线开始，分别执行完全重复、新增、字段变化、单分区空、
聚合空、5,999/6,000 行、精确重复、身份冲突、基线身份缺失和事务异常场景；断言只有完整
可信批次改变当前列表，同一计划周期不重复获取，显式补跑复用同一 run_key。

### 用户故事 2 测试（先写并确认失败）

- [X] T025 [P] [US2] 编写 Provider 映射优先、规范键匹配、新 UUID、完全重复去重、双键冲突、代码变化不按名称合并和任一基线身份缺失即整批失败的零容忍测试 `tests/unit/test_stock_list_identity.py`
- [X] T026 [P] [US2] 编写覆盖证明、聚合空、触及 6,000 行、字段/日期/枚举错误、重复计数、属性变更及失败不发布的 Service 测试 `tests/unit/test_stock_list_service.py`
- [X] T027 [P] [US2] 编写 run_key 唯一、行锁/租约、批量原子事务、失败回滚、无删除、同一成功周期短路及失败补跑计数的 MySQL 测试 `tests/integration/test_stock_list_repository.py`
- [X] T028 [P] [US2] 编写连续 30 次相同计划周期重复触发/补跑、成功运行不再调用 Provider、始终只有一个权威结果且无重复股票、失败后人工补跑和 Flow 不叠加外部重试的集成测试 `tests/integration/test_stock_list_flow.py`

### 用户故事 2 实现

- [X] T029 [P] [US2] 实现全批规范校验、精确重复去重、候选冲突检测、历史 Provider 身份缺失零容忍和确定性身份解析 `src/lucking/services/stock_list.py`
- [X] T030 [P] [US2] 实现 run_key/租约认领、Provider 映射解析、当前值与映射批量原子发布、失败全回滚和缺席旧记录不处理 `src/lucking/repositories/stock_list.py`
- [X] T031 [US2] 实现已成功周期短路、失败周期显式补跑、相同 flow_run_id 幂等和无二层整批重试编排 `src/lucking/flows/stock_list.py`

**检查点**：US1 与 US2 均通过；任何不完整或不可信批次都不能污染当前股票列表。

---

## 阶段 5：用户故事 3——识别来源和数据质量异常（优先级：P3）

**目标**：将可重试的短时来源错误与不可重试的鉴权、额度、载荷、完整性和冲突错误明确
区分，安全记录同步终态与质量问题，并证明可替换 Provider 不改变领域逻辑。

**独立测试**：注入限流、网络、5xx、认证、额度、非法载荷、deadline、身份冲突和数据库
故障，断言只有当前失败分区按 30/120/300 秒有界重试，失败结果、问题记录、Prefect 状态
和 JSONL 日志可关联且不含秘密/原始行；用 Memory Provider 跑同一 golden cases。

### 用户故事 3 测试（先写并确认失败）

- [X] T032 [P] [US3] 编写网络/429/5xx 当前分区重试、30/120/300 秒退避、25 分钟 deadline、非重试错误分类和 Token/响应脱敏契约测试 `tests/contract/test_tushare_stock_basic.py`
- [X] T033 [P] [US3] 编写失败终态、质量问题哈希标识、允许日志字段、六类关键事件、单次 30 分钟判断、最近 30 次计划运行及时率且排除人工运行及日志轮转的失败测试 `tests/unit/test_stock_list_logging.py`
- [X] T034 [P] [US3] 让 Memory Provider 与 Tushare Adapter 对固定 golden cases 执行同一规范语义和统一异常测试，证明替换不修改 Service `tests/contract/test_stock_list_provider.py`

### 用户故事 3 实现

- [X] T035 [P] [US3] 实现仅针对当前失败分区的有界重试、deadline 检查、统一错误映射和安全摘要 `src/lucking/integrations/tushare/stock_list_provider.py`
- [X] T036 [P] [US3] 实现当前值事务回滚后的独立 `FAILED` 终态、计数、质量问题和 `ABANDONED` 租约问题持久化 `src/lucking/repositories/stock_list.py`
- [X] T037 [P] [US3] 实现股票列表日志事件、严格字段白名单、标识哈希、脱敏摘要、轮转、1,800,000 ms 单次及时性及最近 30 次计划运行统计并排除人工运行 `src/lucking/logging.py`
- [X] T038 [US3] 串联 Provider/校验/持久化失败记录，写安全终态日志并重新抛出异常使 Prefect Flow Run 失败 `src/lucking/flows/stock_list.py`

**检查点**：三个用户故事均可验证；来源替换、异常诊断和失败保护满足项目宪章。

---

## 阶段 6：完善与横切关注点

**目的**：完成容量、范围、安全、迁移、文档和全部质量门禁验证。

- [X] T039 [P] 增加 10,000 条候选记录的全批校验/批量发布测试，并在 10,000 条当前记录上预热一次后连续执行 100 次无筛选、代码、venue、名称和状态查询且验证至少 95 次在 1 秒内返回 `tests/integration/test_stock_list_performance.py`
- [X] T040 [P] 增加外部请求范围审计，断言 `api_name` 只出现 `stock_basic`、固定覆盖三个交易所且不存在 venue 子集入口，并确认请求/持久化模型不存在禁止字段 `tests/contract/test_stock_list_scope.py`
- [X] T041 [P] 补充固定 `CN-S`/三 venue、内部调用入口授权责任、配置、迁移、Deployment、Worker、人工补跑、故障排查、日志、数据安全和安全停止运行指引 `README.md`
- [X] T042 在空库与已有交易日历数据的 MySQL 上验证 `uv run alembic upgrade head`，并将迁移/回滚保护证据记录到 `specs/003-stock-list-sync/verification.md`
- [X] T043 运行 `uv run ruff check .`、`uv run mypy src` 和 `uv run pytest`，修复受影响问题并把最终门禁结果记录到 `specs/003-stock-list-sync/verification.md`
- [X] T044 按固定三 venue、正常同步、最近 30 次计划及时率、连续 30 次重复/补跑、完整性失败、来源失败、查询性能和 5 分钟运维排障场景执行快速验收并记录结果 `specs/003-stock-list-sync/verification.md`

---

## 依赖与执行顺序

### 阶段依赖

- **初始化（阶段 1）**：无依赖，可立即开始。
- **基础能力（阶段 2）**：依赖阶段 1；阻塞所有用户故事。
- **US1（阶段 3）**：依赖阶段 2；不依赖其他用户故事，是 MVP。
- **US2（阶段 4）**：依赖 US1 的首次发布链路，但其失败保护场景可独立测试。
- **US3（阶段 5）**：依赖 US1 的同步链路和 US2 的失败保护；Provider 一致性契约可在阶段 2 后提前开展。
- **完善（阶段 6）**：依赖计划交付的所有用户故事。

### 用户故事依赖图

```text
阶段 1 初始化
    ↓
阶段 2 基础能力
    ↓
US1 每日最新列表（MVP）
    ↓
US2 安全更新
    ↓
US3 异常识别与可替换性
    ↓
阶段 6 完善与门禁
```

### 每个用户故事内部顺序

1. 先完成该故事的测试任务，并确认测试因缺少行为而失败。
2. 再实现 Adapter/Repository 等可并行组件。
3. Service 只依赖供应商无关端口，随后串联领域行为。
4. 最后完成 Flow/Deployment 集成并执行独立测试。
5. 通过检查点后再进入下一优先级。

## 并行机会

- 阶段 1 的 T001、T002 可并行。
- 阶段 2 的五类失败测试 T003–T007 可并行；T011、T013、T014 在各自测试完成后可并行实现；T012 只建立通用 Registry。
- US1 的 T016–T019 可并行编写；T020 在 T012 后完成 Adapter 与工厂注册，T021、T024 可并行。
- US2 的 T025–T028 可并行编写；T029 与 T030 可并行实现，T031 等待二者完成。
- US3 的 T032–T034 可并行编写；T035–T037 可并行实现，T038 等待三者完成。
- T039–T041 分别修改测试与文档，可在三个用户故事完成后并行。

## 并行示例

### 用户故事 1

```text
并行：T016 Tushare 契约测试
并行：T017 Service 首次同步测试
并行：T018 Repository 首次发布测试
并行：T019 Flow 调度测试

测试失败后并行：T020 Adapter 与工厂注册、T021 Repository、T024 Deployment
串行收口：T022 Service → T023 Flow
```

### 用户故事 2

```text
并行：T025 身份测试、T026 完整性测试、T027 原子事务测试、T028 幂等 Flow 测试
测试失败后并行：T029 Service 校验、T030 Repository 原子发布
串行收口：T031 Flow 补跑与幂等
```

### 用户故事 3

```text
并行：T032 Adapter 错误契约、T033 日志/问题测试、T034 替代 Provider 一致性测试
测试失败后并行：T035 Adapter 重试、T036 失败持久化、T037 安全日志
串行收口：T038 Flow 失败编排
```

## 实施策略

### MVP First（仅用户故事 1）

1. 完成阶段 1 初始化。
2. 完成阶段 2 基础能力。
3. 完成阶段 3 的 US1。
4. 在干净数据库中独立验证每日调度、唯一端点/字段、首次原子发布与当前列表查询。
5. 停止并评审范围；不提前加入 US2/US3 以外的新数据或入口。

### 增量交付

1. **基础能力**：规范端口、模式、配置选择和兼容设施通过测试。
2. **US1 / MVP**：每日获得并查询最新股票列表。
3. **US2**：增加幂等、完整性、身份和原子失败保护。
4. **US3**：增加有界重试、诊断、日志安全和 Provider 可替换性证明。
5. **完善**：容量、范围审计、文档、迁移和全部质量门禁。

## 说明

- `[P]` 仅表示文件互不冲突且不依赖未完成任务，可并行执行。
- `[US1]`、`[US2]`、`[US3]` 与 spec.md 用户故事一一对应。
- 任务描述中的路径均为仓库根目录相对路径。
- 不得把 Tushare SDK、`stock_basic` 传输字段或专有错误码泄漏到 Service、Repository 或 Flow。
- 不得因上一成功 Provider 身份缺失而删除或推断退市；必须拒绝整批并保留旧值。
- 每个任务或逻辑任务组完成后应提交变更，并及时勾选本文件中的任务。
