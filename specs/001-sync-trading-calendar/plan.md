# 实施计划：定时同步交易日历

**分支**：`001-sync-trading-calendar` | **日期**：2026-07-26 |
**规格**：[spec.md](spec.md)

**输入**：来自 `specs/001-sync-trading-calendar/spec.md` 的功能规格

## 摘要

实现一个由 Prefect 编排的交易日历同步流程。领域层依赖供应商无关的
`TradingCalendarProvider`，首期适配器通过通用 `TushareClient` 调用 Tushare
`trade_cal`，以 `SSE` 数据映射 `CN-S` 市场。流程在写入前完成统一完整性校验：
历史和当日缺口、内部断点及非法数据整批拒绝；只缺少尚未公布的连续未来尾部时，
保存来源已返回的连续前缀，并将尾部保持为 `UNKNOWN`。随后在单个 MySQL 事务中
按“市场代码 + 日期”批量 upsert，日历表只保留当前值。
表中保留首次创建时间 `created_at`，并保存最近成功写入的 `sync_mode` 和 `updated_at`。

同一个 Prefect Deployment 配置两个带参数的计划：

- 月度计划：每月 1 日 02:00（`Asia/Shanghai`），同步当月首日至当年末。
- 年末计划：每年 12 月 20 日 02:30（`Asia/Shanghai`），同步下一自然年全年。

人工补数通过同一 Flow 的显式日期参数执行。同步过程不创建执行记录表，运行状态和统计
写入结构化 JSONL 日志文件，并同时关联 Prefect Flow Run ID。

## 技术上下文

**语言/版本**：Python 3.12

**主要依赖**：FastAPI 0.140+、Prefect 3.8+、HTTPX 0.28+、SQLAlchemy 2.0+、
Alembic 1.18+、PyMySQL 1.2+、Pydantic Settings 2.14+

**存储**：MySQL 8.4；只新增 `trading_calendar` 表。ClickHouse、Redis 不参与本功能数据存储。

**测试**：pytest、HTTPX `MockTransport`、MySQL 集成测试、Prefect Flow/Task 测试；
质量命令为 `uv run pytest`、`uv run ruff check .` 和 `uv run mypy src`

**目标平台**：Windows/WSL2 Ubuntu；应用与 Prefect Process Worker 在 WSL2 本机运行，
MySQL 和 Prefect Server 由现有 Docker Compose 提供

**项目类型**：Python Web 服务与后台工作流

**性能目标**：月度或年末计划从预定执行时间到 Flow 终态的 p95 小于等于 10 分钟，
其中排队、外部调用重试和数据库写入均计入；十年范围同步从实际开始到完成的 p95
小于等于 10 分钟；单日历状态查询的数据库操作目标为 p95 小于 100 毫秒

**约束**：

- 首期只启用 `CN-S`，默认 Provider 为 `tushare`，其 Adapter 映射到 `SSE`
- Flow 和领域服务不得依赖 Tushare 请求、响应或错误类型；供应商由
  `TRADING_CALENDAR_PROVIDER` 配置选择
- 通用 `TushareClient` 不包含交易日历字段映射，必须能复用于其他 Tushare API
- 市场代码格式为 `^[A-Z]{2}-S$`
- 月度范围为当月首日至当年 12 月 31 日，年末范围为下一自然年全年
- Tushare Token 只能通过秘密配置注入，不得进入日志或异常文本
- 空批次、历史/当日缺口、返回结果内部断点、关键字段缺失或来源交易所非 `SSE`
  时整批拒绝；请求范围末端仅缺少 `as_of_date` 之后的连续未来尾部时允许成功写入
  已返回前缀，并记录 `FUTURE_PARTIAL`
- 短时频率限制和上游暂时不可用可重试；账户额度、积分或当日配额耗尽不得重试
- 不保存历史版本，不创建应用级同步执行记录表
- `created_at` 在 upsert 时保持不变；`sync_mode` 只记录最近成功写入模式
- 任务日志必须为结构化 JSONL，并支持按 Flow Run ID 关联；计划运行还必须记录
  预定时间、实际开始时间、完成时间和从预定时间到完成的总耗时

**规模/范围**：首期每年约 365/366 条 `CN-S` 记录；数据模型预留 `HK-S`、`JP-S`、
`US-S`、`KR-S`，即使五个市场保留十年数据也少于 20,000 条

## 宪章检查

### 研究前门禁

- **规格与追溯：通过**。设计项对应 FR-001 至 FR-018、NFR-001 至 NFR-006
  和 ED-001 至 ED-003，用户故事分别覆盖自动同步、失败恢复和人工补数。
- **架构与数据边界：通过**。MySQL 持有低量、强一致的当前日历；Prefect 负责编排；
  Provider Port 定义领域输入，Tushare Client 处理通用协议，Endpoint Adapter 处理字段映射；
  领域服务负责校验和同步，未滥用 ClickHouse 或 Redis。
- **测试与质量门禁：通过**。计划包含通用 Tushare Client、Provider 一致性契约、
  替换适配器测试、日期范围、完整性、幂等、事务回滚、调度参数和日志脱敏测试，
  并要求 pytest、Ruff、mypy 全部通过。
- **安全与最小暴露：通过**。Token 使用环境秘密注入，日志字段采用白名单，
  无新增网络端口；人工同步复用本机 Prefect Deployment。
- **可观测与运维：通过**。结构化日志包含 Flow Run ID、范围、结果、计数、耗时和错误分类；
  计划运行另记录调度延迟、运行耗时和计划到完成耗时；quickstart 覆盖健康检查、
  补数、重跑、及时性统计和故障排查。
- **简洁性：通过**。复用现有 HTTPX、MySQL、Prefect 和 SQLAlchemy；
  不引入 Tushare SDK、pandas、新服务、历史表或同步记录表。

### 设计后复核

- **规格与追溯：通过**。`data-model.md`、`contracts/` 和 `quickstart.md`
  均标明对应需求，未增加首期市场或新用户界面。
- **架构与数据边界：通过**。设计只有一个业务表；同步配置属于版本化 Deployment 配置，
  `sync_mode` 属于当前记录来源上下文；运行日志属于文件和 Prefect 运行上下文，
  不混入业务数据。供应商专有载荷不会进入领域模型或数据库。
- **测试与质量门禁：通过**。契约明确 Provider Port、Tushare 通用信封、
  `trade_cal` 适配、Flow 参数、领域服务返回值和失败语义，可以据此生成替换适配器、
  单元、契约和 MySQL 集成测试。
- **安全与最小暴露：通过**。外部请求中的 Token 不进入模型、返回值或日志；
  quickstart 仅引用环境变量名。
- **可观测与运维：通过**。日志采用固定事件和错误分类，人工补数及计划检查均有可运行步骤。
- **完整性与降级：通过**。领域服务使用同一算法校验所有 Provider；未来连续尾部缺失
  以 `FUTURE_PARTIAL` 显式降级，不伪造休市日，也不删除既有数据。
- **简洁性：通过**。没有需要在复杂度表中登记的宪章例外。

## 项目结构

### 文档（本功能）

```text
specs/001-sync-trading-calendar/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── prefect-flow.md
│   ├── trading-calendar-provider.md
│   ├── trading-calendar-service.md
│   └── tushare-trade-cal.md
└── tasks.md
```

### 源代码（仓库根目录）

```text
alembic.ini
migrations/
├── env.py
└── versions/
    └── <revision>_create_trading_calendar.py

prefect.yaml

src/
└── lucking/
    ├── __init__.py
    ├── config.py
    ├── db.py
    ├── logging.py
    ├── ports/
    │   └── trading_calendar_provider.py
    ├── integrations/
    │   ├── registry.py
    │   └── tushare/
    │       ├── client.py
    │       └── trading_calendar_provider.py
    ├── models/
    │   └── trading_calendar.py
    ├── repositories/
    │   └── trading_calendar.py
    ├── services/
    │   └── trading_calendar.py
    └── flows/
        └── trading_calendar.py

tests/
├── contract/
│   ├── test_trading_calendar_provider.py
│   ├── test_tushare_client.py
│   └── test_tushare_trade_cal.py
├── integration/
│   ├── test_trading_calendar_repository.py
│   └── test_trading_calendar_flow.py
└── unit/
    ├── test_sync_window.py
    ├── test_trading_calendar_service.py
    └── test_structured_logging.py
```

**结构决策**：采用 `src/lucking` 单体包。`ports` 定义项目拥有的供应商无关契约；
`integrations/tushare/client.py` 处理可供多个 Tushare API 复用的通用协议；
`trading_calendar_provider.py` 只负责 `trade_cal` 与标准日历模型的转换；
业务校验、数据库写入和 Prefect 编排保持独立。当前 `main.py` 不承载同步逻辑。

## 实施阶段

### 阶段 1：基础设施与模式

1. 配置 `src` 包布局、测试与静态检查工具。
2. 增加 `TRADING_CALENDAR_PROVIDER`、Tushare Token/API URL、日志目录和同步时区配置。
3. 初始化 Alembic，并创建同时包含 `created_at`、`updated_at` 和 `sync_mode` 的
   `trading_calendar` 表迁移。

### 阶段 2：来源契约与领域服务

1. 定义 `TradingCalendarProvider`、标准日历 DTO 和供应商无关错误。
2. 实现可复用于任意 Tushare API 的通用 Client 与信封模型。
3. 实现 Tushare `trade_cal` Provider Adapter 和显式 Provider Registry。
4. 实现月度、年末、人工三种日期窗口解析。
5. 在领域服务中完成对所有 Provider 一致适用的整批字段、来源、范围、唯一性和
   日期连续性校验，并区分非法缺口与可接受的未来连续尾部缺失。
6. 实现 MySQL 原子批量 upsert 与单日状态查询。

### 阶段 3：工作流、调度与日志

1. 将来源调用、校验和写入编排为 Prefect Task/Flow。
2. 配置短时频率限制和暂时不可用最多 3 次重试，退避为 30、120、300 秒；
   凭据、账户额度/积分/当日配额耗尽、参数和数据校验错误不重试。
3. 配置月度和年末两个 Prefect Schedule，并将模式作为计划参数。
4. 输出 JSONL 结构化日志并配置 10 MiB、保留 5 个文件的轮转；计划运行记录
   `scheduled_at/started_at/completed_at`、调度延迟、运行耗时、计划到完成耗时
   和是否达到 10 分钟目标。

### 阶段 4：验证与运行指引

1. 完成单元、通用 Tushare Client、Provider 一致性、替换适配器和 MySQL 集成测试。
2. 验证重复运行、`sync_mode` 覆盖、批次失败回滚、未来连续尾部降级、内部缺口拒绝、
   空结果和并发重叠范围。
3. 验证短时限流会重试而额度耗尽不重试，并分别产生可区分日志。
4. 按 `quickstart.md` 完成人工补数、计划检查、及时性统计和日志排障演练。

## 复杂度跟踪

无宪章违反项，不需要复杂度例外。
