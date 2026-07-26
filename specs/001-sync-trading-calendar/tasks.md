# 任务：定时同步交易日历

**输入**：`/specs/001-sync-trading-calendar/` 中的设计文档

**前置条件**：plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

**测试规则**：每个用户故事必须先编写并运行对应测试，确认测试在实现前按预期失败；
第三方数据集成必须保持通用 Tushare 协议层、交易日历 Provider Adapter 和领域服务边界。

## 阶段 1：初始化（共享基础设施）

**目的**：建立 Python 包、测试目录和本功能所需工程配置。

- [X] T001 创建 `src/lucking/__init__.py`、`src/lucking/ports/__init__.py`、`src/lucking/integrations/__init__.py`、`src/lucking/integrations/tushare/__init__.py`、`src/lucking/models/__init__.py`、`src/lucking/repositories/__init__.py`、`src/lucking/services/__init__.py` 和 `src/lucking/flows/__init__.py` 包结构
- [X] T002 配置 pytest、Ruff、mypy、`src` 包发现和开发依赖到 `pyproject.toml` 并更新 `uv.lock`
- [X] T003 [P] 增加日志目录和 Python 构建产物忽略规则到 `.gitignore`
- [X] T004 [P] 增加 `TRADING_CALENDAR_PROVIDER`、Tushare、日志目录和时区的无秘密示例配置到 `.env.example`

---

## 阶段 2：基础能力（阻塞性前置条件）

**目的**：完成所有用户故事共用的配置、数据库、领域契约、数据模型和测试替身。

**关键要求**：本阶段完成前不得开始用户故事实现。

- [X] T005 实现数据库 URL、Provider 选择、可选 Tushare 配置、日志目录和时区的 Pydantic Settings 到 `src/lucking/config.py`
- [X] T006 实现依赖 T005 配置的 SQLAlchemy Engine、Session 工厂、Declarative Base 和 UTC 时间转换到 `src/lucking/db.py`
- [X] T007 [P] 定义 `MarketCode`、`SyncMode`、`ProviderCalendarDay`、`TradingCalendarProvider` Protocol 及含 `ProviderQuotaExceededError` 的供应商无关异常到 `src/lucking/ports/trading_calendar_provider.py`
- [X] T008 定义依赖 T006 Base 的 `TradingCalendar` ORM 模型、复合主键、来源字段、`sync_mode`、不可变 `created_at` 和 `updated_at` 到 `src/lucking/models/trading_calendar.py`
- [X] T009 初始化 Alembic 配置并接入项目 metadata 到 `alembic.ini`、`migrations/env.py` 和 `migrations/script.py.mako`
- [X] T010 创建仅含 `trading_calendar` 当前值表且不创建同步历史表的迁移到 `migrations/versions/001_create_trading_calendar.py`
- [X] T011 定义 `TradingCalendarRepository` Protocol、含覆盖水位的 `SyncResult` 和查询结果类型到 `src/lucking/repositories/trading_calendar.py` 和 `src/lucking/services/trading_calendar.py`
- [X] T012 建立 MySQL Session、HTTPX MockTransport、固定 UTC 时钟和固定 `as_of_date` 测试夹具到 `tests/conftest.py`
- [X] T013 实现依赖 T007 Port 且不依赖 Tushare 的内存 Provider 测试替身到 `tests/contract/test_trading_calendar_provider.py`

**检查点**：配置、数据库模式、Provider Port、标准模型和替代 Provider 已可供用户故事复用。

---

## 阶段 3：用户故事 1——自动获得最新交易日历（优先级：P1）🎯 MVP

**目标**：按月度和年末计划获取 `CN-S` 日历，完整范围返回 `COMPLETE`；仅缺少尚未公布的
连续未来尾部时返回 `FUTURE_PARTIAL`，并将已验证前缀幂等保存到 MySQL。

**独立测试**：使用固定时钟和 MockTransport 分别运行月度、年末 Flow；验证窗口、
`CN-S`→`SSE`、开休市、完整与未来部分覆盖、幂等字段规则、计划到终态耗时及重复运行。

### 用户故事 1 测试（先编写并确认失败）

- [X] T014 [P] [US1] 编写月度当月至年末、年末下一自然年、市场代码和 `as_of_date` 单元测试到 `tests/unit/test_sync_window.py`
- [X] T015 [US1] 扩展 T013 测试替身并编写 Provider 一致性契约，验证标准模型、连续未来前缀和供应商类型隔离到 `tests/contract/test_trading_calendar_provider.py`
- [X] T016 [P] [US1] 编写通用 Tushare Client 成功契约，使用 `trade_cal` 和虚构第二 API 验证无接口硬编码、字段乱序和只读行到 `tests/contract/test_tushare_client.py`
- [X] T017 [P] [US1] 编写 `trade_cal` Adapter 的 `CN-S`→`SSE`、开休市、日期、标准 DTO 和不填充未来尾部契约测试到 `tests/contract/test_tushare_trade_cal.py`
- [X] T018 [P] [US1] 编写迁移、复合主键、幂等 upsert、`created_at` 不变及 `sync_mode/updated_at` 覆盖的 MySQL 测试到 `tests/integration/test_trading_calendar_repository.py`
- [X] T019 [P] [US1] 编写 Service 的完整区间、连续未来尾部、`coverage_end`、`missing_future_count`、`COMPLETE/FUTURE_PARTIAL` 和 `UNKNOWN` 测试到 `tests/unit/test_trading_calendar_service.py`
- [X] T020 [US1] 在 Provider 契约测试中先覆盖默认/未知 Provider、Tushare 配置按需校验、无自动回退及不泄漏供应商配置到 `tests/contract/test_trading_calendar_provider.py`
- [X] T021 [P] [US1] 编写 Prefect 月度/年末参数、Provider 注入、覆盖状态、计划时间到终态公式、600000 毫秒边界及人工运行排除测试到 `tests/integration/test_trading_calendar_flow.py`

### 用户故事 1 实现

- [X] T022 [P] [US1] 实现 `monthly/year_end` 窗口、市场代码、市场时区业务日期和可注入 `as_of_date` 到 `src/lucking/flows/trading_calendar.py`
- [X] T023 [P] [US1] 实现可复用于任意 `api_name/params/fields` 的同步 `TushareClient`、通用信封和 `TushareTable` 到 `src/lucking/integrations/tushare/client.py`
- [X] T024 [US1] 实现依赖 T023 Client、只转换 `trade_cal` 字段且不泄漏 Tushare 类型的 Adapter 到 `src/lucking/integrations/tushare/trading_calendar_provider.py`
- [X] T025 [US1] 在 T020 测试约束下实现显式 Provider Registry、配置选择和组合根构造到 `src/lucking/integrations/registry.py`
- [X] T026 [P] [US1] 实现 MySQL 单事务批量 upsert，显式排除 `created_at` 更新并覆盖 `sync_mode/updated_at` 到 `src/lucking/repositories/trading_calendar.py`
- [X] T027 [US1] 实现依赖注入 Provider/Repository 的同步服务、统一完整性算法和含覆盖水位的 `SyncResult` 到 `src/lucking/services/trading_calendar.py`
- [X] T028 [P] [US1] 实现 JSONL 基础格式、计划及时性三个耗时计算、`timeliness_met` 和最近 20 次按 `schedule_slug` 统计到 `src/lucking/logging.py`
- [X] T029 [US1] 集成获取、校验、前缀写入、`COMPLETE/FUTURE_PARTIAL`、及时性上下文和结果日志到 `src/lucking/flows/trading_calendar.py`
- [X] T030 [US1] 配置单个 Deployment、两个 Cron、`Asia/Shanghai`、ENQUEUE 和模式参数到 `prefect.yaml`

**检查点**：P1 可独立验证自动同步、未来未公布日期降级、幂等当前值和计划及时性口径。

---

## 阶段 4：用户故事 2——从同步失败中安全恢复（优先级：P2）

**目标**：短时限流和暂时不可用有限重试；额度耗尽及永久错误立即失败；任何失败不部分写入。

**独立测试**：模拟超时、429、短时频率限制、5xx、额度/积分/当日配额耗尽、无效 Token、
历史/当日缺口、未来内部断点、空批次和数据库失败；验证重试分类、回滚和脱敏日志。

### 用户故事 2 测试（先编写并确认失败）

- [X] T031 [P] [US2] 扩展通用 Client 契约测试覆盖网络、429、5xx、短时频率限制、`QUOTA_EXHAUSTED`、鉴权、非法信封和 Token 脱敏到 `tests/contract/test_tushare_client.py`
- [X] T032 [P] [US2] 扩展 Adapter 契约测试覆盖额度异常映射、空数据、非 SSE、重复日期、缺字段和非法日期/`is_open` 到 `tests/contract/test_tushare_trade_cal.py`
- [X] T033 [P] [US2] 编写历史/当日缺口、未来内部断点、越界日期、空批次和事务异常不改变既有数据的测试到 `tests/unit/test_trading_calendar_service.py` 和 `tests/integration/test_trading_calendar_repository.py`
- [X] T034 [P] [US2] 编写短时限流/暂时不可用重试、额度/凭据/校验错误不重试、退避序列和 Failed 状态测试到 `tests/integration/test_trading_calendar_flow.py`
- [X] T035 [P] [US2] 编写 JSONL 白名单、Flow Run ID、错误类别、轮转及 Token/连接串脱敏测试到 `tests/unit/test_structured_logging.py`

### 用户故事 2 实现

- [X] T036 [US2] 在通用 Client 中实现 Tushare 协议级错误分类、短时限流与额度耗尽区分及脱敏内部异常到 `src/lucking/integrations/tushare/client.py`
- [X] T037 [US2] 在 Adapter 中将 Tushare 内部错误映射为含 `ProviderQuotaExceededError` 的供应商无关异常到 `src/lucking/integrations/tushare/trading_calendar_provider.py`
- [X] T038 [P] [US2] 完成 Service 的非法缺口、空批次、跨记录一致性和 `CalendarPersistenceError` 回滚语义到 `src/lucking/services/trading_calendar.py`
- [X] T039 [P] [US2] 完成 JSONL 字段白名单、10 MiB/5 文件轮转、错误分类和秘密脱敏到 `src/lucking/logging.py`
- [X] T040 [US2] 在 Flow 中仅对 `ProviderRateLimitedError/ProviderUnavailableError` 配置 3 次与 30/120/300 秒退避，并记录所有失败终态及时性到 `src/lucking/flows/trading_calendar.py`

**检查点**：P2 可独立证明暂时故障受控恢复、额度耗尽不重试且失败批次不破坏已有数据。

---

## 阶段 5：用户故事 3——查看状态并补充指定范围（优先级：P3）

**目标**：支持最长十年的人工补数，并按市场和日期查询 `OPEN/CLOSED/UNKNOWN`。

**独立测试**：对含缺口的显式范围运行 `manual` Flow 后查询开市、休市和未公布日期；
验证仅处理授权范围、人工运行不计计划及时性，无效市场和反向/超长范围在外部调用前拒绝。

### 用户故事 3 测试（先编写并确认失败）

- [X] T041 [P] [US3] 编写单日 `OPEN/CLOSED/UNKNOWN`、未来尾部 `UNKNOWN`、范围升序、`sync_mode` 和非法市场测试到 `tests/unit/test_trading_calendar_service.py`
- [X] T042 [P] [US3] 编写 `manual` 必填日期、反向/十年以上范围、授权范围写入、日志及及时性统计排除测试到 `tests/integration/test_trading_calendar_flow.py`

### 用户故事 3 实现

- [X] T043 [US3] 实现 Repository 的单日和范围查询到 `src/lucking/repositories/trading_calendar.py`
- [X] T044 [US3] 实现 Service 的 `get_status/list_range` 及 `OPEN/CLOSED/UNKNOWN` 语义到 `src/lucking/services/trading_calendar.py`
- [X] T045 [US3] 实现 Flow 的 `manual` 参数模型、最长十年校验、显式范围补数和非计划运行日志到 `src/lucking/flows/trading_calendar.py`

**检查点**：三个用户故事均可独立验证，人工补数与计划同步复用同一完整性和数据安全规则。

---

## 阶段 6：完善与横切关注点

**目的**：完成性能、运行文档、端到端演练和质量门禁。

- [X] T046 [P] 执行十年范围和单日查询性能基准，并将 p95、样本量、环境及计划最近 20 次暂定/正式达标率记录到 `specs/001-sync-trading-calendar/verification.md`
- [X] T047 [P] 更新安装、环境变量、迁移、Worker、Deployment、完整性降级、额度错误、及时性统计和安全停止说明到 `README.md`
- [X] T048 按 `specs/001-sync-trading-calendar/quickstart.md` 完成人工补数、幂等、完整性边界、失败保护、及时性、Provider 替换和日志脱敏演练，并运行 `uv run ruff check .`、`uv run mypy src`、`uv run pytest`，将结果记录到 `specs/001-sync-trading-calendar/verification.md`

---

## 依赖与执行顺序

### 阶段依赖

- 阶段 1 无依赖，可立即开始。
- 阶段 2 依赖阶段 1，且阻塞全部用户故事。
- 阶段 3 依赖阶段 2，是可独立交付的 MVP。
- 阶段 4 复用 US1 的 Client、Adapter、Service、日志和 Flow，实现顺序依赖阶段 3。
- 阶段 5 复用 US1 的 Repository、Service 和 Flow；其测试可在阶段 2 后先行编写。
- 阶段 6 在选定交付范围完成后执行；完整交付时依赖阶段 3～5。

### 明确任务依赖

- T006 依赖 T005；T008 依赖 T006；T009 依赖 T008；T010 依赖 T009。
- T013 依赖 T007；T015 依赖 T013；T020 依赖 T015。
- T024 依赖 T023；T025 依赖 T020、T024，且必须在 Registry 测试失败后实施；
  T027 依赖 T007、T011、T026。
- T029 依赖 T022、T024～T028；T030 依赖 T029。
- T037 依赖 T036；T040 依赖 T037～T039。
- T044 依赖 T043；T045 依赖 T044。

### 用户故事依赖图

```text
阶段 1 初始化
    ↓
阶段 2 基础能力
    ↓
US1 自动同步与未来降级（MVP）
    ├──→ US2 失败恢复与错误分类
    └──→ US3 状态查询与人工补数
              ↓
        阶段 6 完善与门禁
```

## 并行执行机会

### 用户故事 1

基础能力完成后，T014、T016～T019、T021 可并行；T015→T020 使用同一契约测试文件。
实现时 T022、T023、T026、T028 可并行，随后汇合到 Adapter、Registry、Service 和 Flow。

```text
并行测试：T014、T016、T017、T018、T019、T021
串行测试：T013 → T015 → T020
并行实现：T022、T023、T026、T028
汇合路径：T023 → T024；T020 → T025；T024～T028 → T029 → T030
```

### 用户故事 2

T031～T035 修改不同测试文件，可并行；实现分为 Client/Adapter、Service、日志三条路径，
最终在 Flow 重试编排汇合。

```text
并行测试：T031、T032、T033、T034、T035
实现分线：T036 → T037 | T038 | T039
汇合路径：T037、T038、T039 → T040
```

### 用户故事 3

T041 与 T042 可并行；查询路径按 Repository→Service，人工补数在查询语义稳定后集成。

```text
并行测试：T041、T042
实现路径：T043 → T044 → T045
```

## 实施策略

### MVP 优先

1. 完成阶段 1 和阶段 2。
2. 完成阶段 3 的 US1。
3. 独立验证 `COMPLETE/FUTURE_PARTIAL`、开休市、幂等字段、Provider 替换和及时性公式。
4. MVP 通过后再加入失败恢复与人工运维能力。

### 增量交付

1. US1 交付自动同步、当前值表、未来连续尾部降级和计划及时性日志。
2. US2 增加短时限流/额度耗尽分类、有限重试、事务保护和脱敏失败日志。
3. US3 增加状态查询和指定范围人工补数。
4. 最后完成性能记录、运行文档、端到端演练和全部质量门禁。

## 说明

- `[P]` 仅表示任务修改不同文件且不依赖同阶段未完成任务。
- `[US1]`、`[US2]`、`[US3]` 分别追溯到规格中的三个用户故事。
- 不引入 Tushare SDK、pandas、同步执行表、历史表、管理页面或写操作 REST API。
- 通用 `TushareClient` 不得出现 `trade_cal`、`SSE` 或交易日历字段映射。
- `FUTURE_PARTIAL` 只允许连续未来尾部缺失；不得掩盖历史/当日缺口或内部断点。
- `created_at` 必须保留且 upsert 时不可变；幂等比较允许 `updated_at` 推进和
  `sync_mode` 反映最后一次成功执行。
