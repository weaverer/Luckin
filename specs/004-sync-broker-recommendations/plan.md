# 实施计划：每月券商金股同步

**分支**：`004-sync-broker-recommendations` | **日期**：2026-07-26 |
**规格**：[spec.md](spec.md)

**输入**：来自 `specs/004-sync-broker-recommendations/spec.md` 的功能规格

## 摘要

实现一条独立的券商金股同步链路，由 Prefect 在 `Asia/Shanghai` 每月 3 日和 4 日
12:00 运行，两次均从原计划时间推导当前自然月。首期真实来源固定为 Tushare
`broker_recommend`，只请求 `month,broker,ts_code,name`。独立 Adapter 将供应商字段
转换为项目 `BrokerRecommendationProvider` 规范模型，Service 负责券商空白规范化、
月份和字段校验、稳定股票身份解析、批内去重与冲突检测。

可信候选批次在一个 MySQL 事务中按
`recommendation_month + broker_name + stock_id` 原子新增或更新，并记录唯一计划周期、
不可变执行尝试和安全质量问题。4 日结果缺少 3 日推荐时不删除、不失效、不触发基线缺失；
空结果、返回触顶、字段或月份异常、未知股票身份、冲突及持久化失败均拒绝整批发布。
Prefect 只负责编排，Tushare Adapter 只对瞬态故障额外重试最多 3 次，Flow 不叠加重试。

## 技术上下文

**语言/版本**：Python 3.12

**主要依赖**：Prefect 3.8+、HTTPX 0.28+、SQLAlchemy 2.0+、Alembic 1.18+、
PyMySQL 1.2+、Pydantic Settings 2.14+；复用现有 `TushareClient`、Provider Registry、
`JsonlLogStore` 和数据库会话组件，不新增 Tushare SDK 或运行依赖

**存储**：MySQL 8.4 保存券商金股、唯一计划周期、执行尝试和质量问题，并引用现有
`stock_current.stock_id`；ClickHouse 与应用 Redis 不参与本功能

**测试**：pytest、HTTPX `MockTransport`、内存 Provider、SQLite 快速测试、
MySQL 并发/排序规则/事务集成测试、Prefect Flow 测试；质量命令为
`uv run ruff check .`、`uv run mypy src`、`uv run pytest`、
`uv run pytest -m mysql`、`uv run alembic upgrade head`

**目标平台**：Windows/WSL2 Ubuntu；应用和 Prefect Process Worker 在 WSL2 本机运行，
MySQL 与 Prefect Server 由现有 Docker Compose 提供

**项目类型**：Python 后台同步工作流与应用内月度金股查询服务；
不新增公共 HTTP API 或用户界面

**性能目标**：正常来源条件下从计划时间到终态不超过 30 分钟；Provider-neutral 链路
完整处理至少 1,000 条月度推荐；重复 30 次和 10 组并发补跑不产生第二权威周期或重复推荐

**约束**：

- 计划 Cron 固定为 `0 12 3,4 * *`，时区 `Asia/Shanghai`，周末、节假日和非交易日不跳过。
- 计划执行从 Prefect runtime `scheduled_start_time` 获取原计划时点；人工补跑必须显式传入
  原计划时点。禁止用实际启动时间推导目标月份。
- 3 日和 4 日是两个独立计划周期，目标均为各自原计划时点所属的当前自然月。
- 唯一真实外部端点是 Tushare `broker_recommend`，参数只有 `month=YYYYMM`，
  字段严格为 `month,broker,ts_code,name`；不得调用行情、财务、预测或其他接口。
- Tushare 官方公开单次上限为 1,000 行，却未公开该接口的分页参数和稳定续取协议；
  首期 0 行或恰好 1,000 行均不得宣称完整，必须失败。仅 1–999 行可继续业务校验。
- Provider-neutral 内存替身必须证明链路本身可处理 1,000 条；若生产数据触顶，
  上线必须阻断，直至供应商确认可验证续取协议或切换兼容 Provider。
- `ts_code` 只存在于 Adapter DTO 和现有股票 Provider 映射解析过程；
  不成为推荐业务主键、数据库公开字段或下游消费字段。
- `.SH/.SZ/.BJ` 映射为 `XSHG/XSHE/XBSE`；未知后缀、代码为空或映射冲突均整批失败。
- Service 优先使用 `(provider_code, provider_security_id)` 解析现有股票映射，
  再与 `stock_current(CN-S, venue_code, security_code)` 交叉校验；
  不存在或不唯一时产生 `UNKNOWN_STOCK_IDENTITY`/`IDENTITY_CONFLICT`，不得顺手创建股票主数据。
- 券商名称只去首尾空白并把连续 Unicode 空白折叠为一个 ASCII 空格；
  不做 NFKC、大小写、标点或别名归一。MySQL 唯一键必须使用区分字符的排序规则。
- 推荐月份保存为当月第一日；唯一键为月份、规范券商名称和项目 `stock_id`。
- 完全相同重复可去重并计数；同一业务键字段冲突、月份错配、无效字段和未知股票身份整批失败。
- 可信批次只新增、更新和确认本批出现的推荐；永不扫描删除、失效或改写缺席推荐。
- 所有候选先在内存校验，成功后单个 MySQL 事务发布；失败批次对推荐表零修改。
- 唯一 `run_key` 由计划 slug、原计划 UTC 时点、目标月份和范围指纹生成；
  MySQL 唯一约束是幂等最终保障，Prefect 并发限制只用于资源保护。
- 一个权威计划周期可以有多个不可变执行尝试；`SUCCEEDED` 周期不可重开，
  失败补跑复用原周期并新增 attempt。
- Tushare Adapter 仅对网络/超时、HTTP 429、明确短期限流和 5xx
  进行初次调用后的最多 3 次重试；退避 30/120/300 秒并受 25 分钟整体截止时间约束。
- 认证、权限、额度、参数、载荷、空结果、触顶、月份/身份/冲突和数据库错误不自动重试；
  Flow `retries=0`，防止重试层数相乘。
- 事件瞬间以 UTC 保存；业务月由原计划时点转换到 `Asia/Shanghai` 后确定。
- Token、数据库连接串、完整请求/响应、供应商原始消息和原始行不得进入日志、错误摘要或业务表。
- 查询仅供项目内部已授权调用方使用；调用入口承担认证、授权和数据访问控制。

**规模/范围**：第一版维护中国 A 股券商月度金股，单月至少支持 1,000 条规范候选；
每月两个计划周期。明确排除推荐删除、整月替换、推荐理由、券商排名、行情补充、绩效计算、
历史回测、公共 API、管理页面、ClickHouse、Redis 缓存和实时推送。

## 宪章检查

### 研究前门禁

- **规格与追溯：通过**。调度和目标月对应 FR-001/002；推荐唯一键与追加更新对应
  FR-003 至 FR-007；run/attempt 和原子发布对应 FR-008 至 FR-012；
  Provider 隔离对应 ED-001 至 ED-006。
- **架构与数据边界：通过**。MySQL 拥有低量、强一致的推荐事实和运行审计；
  `stock_current` 继续拥有稳定股票身份；Prefect 只编排，候选只在进程内存；
  ClickHouse、应用 Redis、FastAPI 和前端均不适用。
- **第三方数据源可替换性：通过**。计划定义独立 `BrokerRecommendationProvider`、
  规范 DTO、统一异常、Tushare Adapter、显式 Registry、内存替身、契约测试和影子迁移；
  Service/Repository 不接触端点名、Tushare 字段或专有错误码。
- **测试与质量门禁：通过**。契约、单元、SQLite、真实 MySQL、Flow 和端到端层级覆盖
  范围审计、触顶、身份、并发认领、原子回滚、无删除和重试边界，并明确 Ruff/mypy/pytest/Alembic。
- **安全与最小暴露：通过**。复用 `SecretStr` 延迟取 Token；只请求四字段；
  不保存原始 payload；不新增网络入口；日志和问题表采用字段白名单与哈希。
- **可观测与运维：通过**。权威 run、不可变 attempt、issue、Prefect 和 JSONL
  形成可关联诊断链；quickstart 覆盖部署、补跑、五分钟排障和及时性统计。
- **简洁性：通过**。复用现有包、Client、Registry、日志、MySQL 和 Prefect；
  新增独立垂直切片及四表是满足推荐事实、周期、逐次补跑审计和问题追踪的最小结构。

### 设计后复核

- **规格与追溯：通过**。`data-model.md`、四份契约和 quickstart 覆盖全部功能需求、
  外部依赖和成功标准；不含范围外字段或行为。
- **架构与数据边界：通过**。Provider 映射和股票主数据由现有股票列表领域拥有；
  金股领域只引用 `stock_id` 并保存推荐时规范字段。成功事务只写 MySQL，
  不存在跨存储一致性。
- **第三方数据源可替换性：通过**。Provider 契约只表达月份、券商、规范股票和覆盖证据；
  Tushare 契约独占 `broker_recommend`、字段、后缀、触顶和错误映射；
  替代 Provider 通过相同 golden cases 后仅改配置。
- **测试与质量门禁：通过**。设计明确 Memory/Tushare 一致性契约、真实 MySQL
  精确排序规则与首次并发认领、事务回滚、1,000 条容量、30 次重复和 10 组并发补跑。
- **安全与最小暴露：通过**。推荐表无 Token、`ts_code` 或原始载荷；
  issue 只保存哈希和安全摘要；内部查询不暴露 Provider 字段。
- **可观测与运维：通过**。run 保持单一权威状态，attempt 保存每次补跑计数和终态，
  issue 保存有限的脱敏问题样本；日志关联 flow/run/attempt 并记录 30 分钟及时性。
- **简洁性：通过**。不新增框架、服务、队列、缓存、快照或公共接口；四表分离是
  FR-008 “每次补跑分别可追踪”与 FR-012 “一个权威结果”同时成立所必需。

## 项目结构

### 文档（本功能）

```text
specs/004-sync-broker-recommendations/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── broker-recommendation-provider.md
│   ├── broker-recommendation-service.md
│   ├── prefect-flow.md
│   └── tushare-broker-recommend.md
└── tasks.md
```

### 源代码（仓库根目录）

```text
prefect.yaml

migrations/
└── versions/
    └── 003_create_broker_recommendation_tables.py

src/
└── lucking/
    ├── config.py
    ├── logging.py
    ├── ports/
    │   └── broker_recommendation_provider.py
    ├── integrations/
    │   ├── registry.py
    │   └── tushare/
    │       ├── client.py
    │       └── broker_recommendation_provider.py
    ├── models/
    │   └── broker_recommendation.py
    ├── repositories/
    │   └── broker_recommendation.py
    ├── services/
    │   └── broker_recommendation.py
    └── flows/
        └── broker_recommendation.py

tests/
├── contract/
│   ├── test_broker_recommendation_provider.py
│   └── test_tushare_broker_recommend.py
├── integration/
│   ├── test_broker_recommendation_repository.py
│   ├── test_broker_recommendation_mysql.py
│   ├── test_broker_recommendation_flow.py
│   └── test_broker_recommendation_capacity.py
└── unit/
    ├── test_broker_recommendation_config.py
    ├── test_broker_recommendation_identity.py
    ├── test_broker_recommendation_service.py
    └── test_broker_recommendation_logging.py
```

**结构决策**：继续使用 `src/lucking` 单体包并为金股建立独立垂直切片。
Port 拥有供应商无关契约；Tushare Adapter 独占端点和专有映射；Service 负责业务校验；
Repository 负责股票身份读取、并发认领和 MySQL 原子发布；Flow 仅组装与记录运行。
不把金股逻辑塞入 `stock_list`，因为股票列表采用全量基线完整性，而金股明确采用缺席不删除。

## 实施阶段

### 阶段 1：数据库、配置与并发认领

1. 增加金股 Provider、时区、日志、25 分钟截止时间和 1,000 行来源上限配置，
   共享现有 Tushare Token 与 URL。
2. 创建推荐、权威 run、不可变 attempt 和 issue 四表；迁移同时验证空库升级和 `002 → 003`。
3. 修正 `migrations/env.py` 的模型加载，使 Alembic metadata 可发现现有和新增全部模型。
4. 先实现真实 MySQL 的原子 claim、唯一冲突重读、attempt 追加、成功不可重开和过期运行保护。

### 阶段 2：Provider 契约与 Tushare Adapter

1. 定义 `BrokerRecommendationProvider`、规范 DTO、覆盖证据和本域统一异常。
2. 实现 Memory Provider 一致性套件，再实现只调用 `broker_recommend` 的 Tushare Adapter。
3. 严格审计 `month` 参数、四字段、月份一致、代码后缀、0/999/1000 行和响应字段。
4. 复用通用 Client 错误分类，并补充确定性权限码映射；Adapter 封装最多 3 次瞬态重试。
5. Adapter 可构造后再注册到独立 Provider Registry，禁止 Service 依赖 Tushare 模块。

### 阶段 3：领域校验、原子发布与内部查询

1. 实现目标月推导、券商空白规范化、必填字段、股票映射交叉校验、重复和冲突判断。
2. 实现全批校验后单事务新增/更新/确认；保留 `first_seen`，刷新 `last_confirmed`，
   不读取基线来删除或拒绝缺席推荐。
3. 失败事务回滚后独立保存 attempt 计数、run 终态和有限的安全 issue 样本。
4. 提供按月份、券商和股票筛选的内部 Repository/Service 查询，不新增 HTTP API。

### 阶段 4：工作流、调度与运维

1. 新增 `broker-recommendation-sync/default`，Cron `0 12 3,4 * *`，
   时区 `Asia/Shanghai`，并发 1，冲突策略 `ENQUEUE`。
2. Flow 从 Prefect runtime 读取计划时点；人工补跑强制传原计划时点和 slug。
3. 增加独立 JSONL 文件及字段白名单，记录 run/attempt、目标月、计数、retry 和及时性。
4. 更新 README 的配置、部署、人工补跑、触顶阻断、五分钟排障和安全停止说明。

### 阶段 5：验证与上线门禁

1. 完成端点/字段范围、Provider 替换、月份、身份、空白、重复、冲突和错误映射契约测试。
2. 完成 MySQL 精确 collation、首次并发认领、唯一键、事务回滚和迁移测试。
3. 执行 3 日→4 日核心场景，证明新增/更新且缺席不删除；失败批次保持数据库摘要不变。
4. 用 Memory Provider 完成 1,000 条端到端容量；用 Tushare fixture 验证 999 成功、1000 失败。
5. 执行连续 30 次重复、10 组并发补跑、瞬态 3 次重试和确定性错误零重试。
6. 上线前用部署账户验证 `broker_recommend` 权限、频率及实际月度行数；
   若达到 1,000 行，必须获得可验证续取契约或启用兼容替代 Provider，不得绕过完整性门禁。

## 复杂度跟踪

无宪章违反项，不需要复杂度例外。
