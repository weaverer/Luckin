# 研究：指数技术因子同步（006-sync-index-factors）

> 本文件为 `/speckit-plan` Phase 0 输出，记录设计决策、理由与备选方案。
> 依据：spec.md（含 Clarifications 2026-08-02 三条确认）、项目宪章 1.2.0、
> Tushare `idx_factor_pro` 文档（doc_id=358）、仓库既有实现调研（005/003/004）。

## 决策 1：指数身份映射——新建指数主数据并自举注册

**决策**：仿照 003 股票身份先例，新建 MySQL 表 `index_current`（规范指数标识）与
`index_provider_mapping`（`(provider_code, provider_security_id) → index_id`）；
本功能同步流程首次见到某 `ts_code` 时自动注册（自举），无需预先维护指数清单。
身份解析规则：来源 `ts_code` 后缀必须属于部署账户实测全集
`.SH/.SZ/.CSI/.SI/.CI/.NH/.BJ/.CNI`（2026-08-02 实测，20260731 全量 3146 行
分布：CNI 969、CSI 593、SI 439、CI 433、SZ 351、SH 264、NH 95、BJ 2）且代码非空，
否则视为无效记录（`invalid_count` + 脱敏 issue，跳过）；后缀合法即注册/复用
既有 `index_id`。

**理由**：spec 假设“指数身份映射由本功能维护，具体形式在规划阶段确定”；
仓库不存在任何指数代码（调研确认 `stock_current`/`stock_provider_mapping`
不涵盖指数）。来源无独立“指数列表”接口，而 `ts_code` 本身是稳定规范代码，
自举注册使功能开箱可用；未知即跳过会使首次同步全量失败（spec ED-004：
跳过后无任何有效数据则该交易日失败），与上线目标矛盾。`index_provider_mapping`
单值映射（一个 provider 标识不得映射多个 index_id）沿用 003 语义。

**备选方案**：
- 预先人工维护指数清单：被拒，首期范围确认包含全部指数（约千余只），
  人工清单成本高且来源新增指数时静默丢失；自举与幂等注册成本更低。
- 不建主数据、直接用 ts_code 作为业务键：被拒，宪章 II 要求规范化数据模型与
  供应商标识隔离，业务层不得依赖供应商专有字段；`index_id` 提供稳定身份。

## 决策 2：分析数据存储——ClickHouse 单表 `index_factor`

**决策**：新建 ClickHouse 表 `index_factor`，引擎 `ReplacingMergeTree(updated_at)`，
`ORDER BY (trade_date, index_id)`，`PARTITION BY toYYYYMM(trade_date)` 按月分区，
`updated_at DateTime64(3)` 应用写入版本列；身份列为 `trade_date Date`、
`index_id FixedString(36)`、`index_code String`（来源规范代码，含后缀）；
数据列为基础行情 9 列 + 技术因子 78 列（全部去掉来源 `_bfq` 后缀后的规范名，
`Nullable(Decimal)` 保存，缺失与数值本身可区分）；不设 TTL，按分区显式清理。

**理由**：spec FR-008 要求保存全部技术因子字段（澄清确认），单表每因子一列
符合 005 `daily_basic` 的宽表先例；`ReplacingMergeTree` + `updated_at` 版本列
实现同键替换幂等（FR-009/SC-003）；按月分区支持治理性清理（NFR-009）；
因子类数值精度统一 `Decimal(12,4)`，量额用 `Decimal(24,2)`（沿用 005 约定）。

**备选方案**：
- 按因子类别拆多表：被拒，一次接口返回即完整记录，拆表徒增连接复杂度，
  消费端按交易日全量取用为主，宽表单次读取更简单。
- ClickHouse 不进、存 MySQL：被拒，宪章 II 明确分析型数据由 ClickHouse 承担。

## 决策 3：审计与幂等——复用 005 三张审计表，新增 `data_kind=INDEX_FACTOR`

**决策**：复用 `market_data_sync_run`/`market_data_sync_attempt`/`market_data_sync_issue`
三张 MySQL 审计表，通过新数据类值 `INDEX_FACTOR` 区分；`run_key` 沿用既有
`scheduled_run_key`/`backfill_run_key`（已带 `data_kind` 参数）；状态机、
租约（固定 2100 秒 > 提取 deadline）、计数全集与 21 个问题类别原样复用。
不新建 MySQL 审计表，因此无结构性 DDL 变更；本功能新增的 MySQL 表仅为
`index_current`/`index_provider_mapping`。

**理由**：005 审计模型按 `data_kind` 参数化设计的目的正是“一次实现、多类复用”；
复用保证审计语义、排障体验与质量问题分类跨功能一致（spec NFR-004 可关联性），
且避免第四套并行审计表的维护成本（宪章 V 简洁性）。

**备选方案**：
- 新建 `index_factor_sync_run/attempt/issue` 三表：被拒，与 005 语义完全同构，
  独立演进无收益，且重复实现宪章 VI 治理成本。
- 复用 005 run 表但沿用“单表含全部计数”旧模式（003 风格）：被拒，
  无 attempt 层无法支撑租约与重试追溯（FR-011）。

## 决策 4：限流 30 次/分钟——Adapter 内进程级最小间隔节流

**决策**：在 Tushare Adapter 内实现进程内节流器：每次真实 HTTP 请求前按
最小间隔 ≥ 2 秒（60s/30 ≈ 2s）节流，保证任意 60 秒窗口请求数 ≤ 30；
`monotonic`/`sleep` 可注入以便测试；被来源限流拒绝仍映射
`PROVIDER_RATE_LIMITED`（可重试）+ 退避 30/120/300 秒 ≤ 3 次，受整体 deadline
约束；Flow 保持 `retries=0`，不叠加重试层。

**理由**：spec FR-005/NFR-004 要求全程请求频率 ≤ 30 次/分钟，SC-006 用演练
验收；仓库源码现无限流器（调研确认），增量同步虽每日 1 次请求，但回补
约 610 个交易日 + 触顶分页时请求密度会接近阈值，显式节流是唯一可证明方式；
放在 Adapter 层使增量与回补两条链路同时受保护（与既有“重试层数不相乘”
原则一致）；进程内实现避免引入分布式限流复杂度（宪章 V：单 worker 首期
足够，Redis 不参与应用链路）。

**备选方案**：
- 回补 Flow 循环内逐日 sleep：被拒，增量链路不受保护，且节流逻辑散落于
  编排层，无法单测。
- Redis 分布式限流器：被拒，调研显示仅残留实验 pyc 从未提交；首期单 worker
  场景进程内节流足够，分布式留给未来多 worker 需求（在复杂度跟踪中记录）。

## 决策 5：提取模式——按交易日单请求全量，8,000 行上限为完整性门禁

**决策**：增量与回补均按交易日调用 `idx_factor_pro`（`trade_date` 参数，
不传 `ts_code`），单次请求覆盖该日全部指数；若返回行数达到单次上限 8,000
且未验证续取方式，判定不完整（`ProviderResponseCappedError`）并失败，
不得猜测续取参数（spec FR-006/ED-003/ED-008）。

**理由**：spec FR-004 明确按交易日提取、不得按指数循环；来源输入参数仅有
`ts_code/start_date/end_date/trade_date`，无分页参数，触顶时无合法续取手段，
安全失败是唯一正确行为（ED-003）。指数总数（调研估计约千余只）远低于
8,000 行上限，正常情况单请求即完整；触顶概率由上线门禁实测确认
（见部署前待验证项 1）。

**备选方案**：
- 触顶后按 ts_code 前缀猜测过滤：被拒，ED-008 禁止猜测参数绕过完整性门禁。
- 回补用 `start_date/end_date` 区间请求：被拒，逐日请求与既有幂等/审计
  模式一致（每日独立终态），区间请求无法逐日归属问题。

## 决策 6：调度与回补——复用 005 双 Flow 模式

**决策**：新增参数化 Flow `index-factor-sync`（Cron `0 17 * * 1-5`、
Asia/Shanghai、`concurrency_limit 1` + `ENQUEUE`、`retries=0`）与人工回补 Flow
`index-factor-backfill`（参数 `start_date/end_date/backfill_batch_id`，
回补起点硬编码 `2024-01-01`，拒绝未来日期与反向区间）；回补流程沿用
005 `backfill` 模式：区间整体校验 → 交易日历逐日展开 → 逐日幂等
（`backfill_batch_id + data_kind + target_trade_date`）→ 逐日独立终态。
目标交易日以 `prefect.runtime.flow_run.scheduled_start_time` 为准
（直接调用必须显式提供 `scheduled_at`）。

**理由**：spec FR-002/FR-003/FR-018 与 005 已验证模式一一对应；
交易日 17:00 为用户显式指定（Clarifications 已确认），非交易日经交易日历
判断后直接 SKIPPED（FR-001）。

**备选方案**：
- 单 Flow 带模式参数：被拒，增量与回补生命周期、参数和幂等键不同，
  分离更符合 005 已建立的可观测与排障体验。

## 决策 7：字段规范化——去 `_bfq` 后缀、保留来源语义、白名单映射

**决策**：规范字段名 = 来源字段名去掉 `_bfq` 后缀（如 `ma_bfq_5` → `ma_5`、
`boll_lower_bfq` → `boll_lower`），基础行情保留来源名
（`pct_chg`、`vol`、`amount`）；Adapter 内白名单 `INDEX_FACTOR_FIELDS`
严格校验（`set(row) != set(fields)` 即整批失败，防供应商字段泄漏）；
未进入规范模型的来源新字段不得进入业务表（spec ED-005/SC-007）。

**理由**：接口输出“均不复权”，`_bfq` 后缀无信息量，保留会造成 80+ 列冗余
命名噪音；规范名稳定不随来源改名漂移；白名单校验沿用 005 Adapter 先例。

**备选方案**：
- 原样保留 `_bfq` 后缀：被拒，语义冗余且消费端每个因子都要写两次后缀。
- 自定义中文/缩写名：被拒，因子名本身即领域术语，保留来源语义最可追溯。

## 实现验证补充（2026-08-02）

- **替代实现/测试替身证明（ED-006/ED-007）**：Service 契约测试（T010/T018/T022，
  见 `tests/unit/test_index_factor_service.py`、`test_index_factor_backfill.py`、
  `test_index_factor_failure.py`）全部基于 `tests/contract/index_factor_memory.py`
  的 Memory 替身运行，与 Tushare Adapter（T011）零耦合；替换 Provider 实现后
  1~8 行为不变由替身重跑同一验收集证明。质量门禁（T027）通过：
  `ruff check` 0 错误、`mypy --strict` 0 错误、`pytest` 全量 224 通过。

## 来源

- Tushare `idx_factor_pro` 接口文档：https://tushare.pro/document/2?doc_id=358
- 项目文档：`specs/005-a-share-trend-data/{research,plan,data-model,contracts,tasks}.md`、
  `specs/003-stock-list-sync/data-model.md`、`specs/004-sync-broker-recommendations/data-model.md`、
  `src/lucking/`（config、clickhouse、ports、integrations、models、repositories、
  services、flows）、`migrations/versions/`、`prefect.yaml`、`pyproject.toml`

## 部署前待验证项（上线门禁）

1. 用部署账户实测 `idx_factor_pro` 按 `trade_date` 单次全量请求的返回行数，
   确认远低于 8,000 上限；若某日触顶，必须判定为不兼容并重新设计提取策略。
2. 实测返回字段全集与文档一致性（约 78 个技术因子 + 10 个基础行情字段），
   确认无复权价格字段、无文档外新字段；确认 `_bfq` 后缀规律。
3. 实测 30 次/分钟限流的实际行为（连续请求频率的拒绝形态与错误码），
   校准最小间隔节流参数与 `PROVIDER_RATE_LIMITED` 错误映射。
4. 实测 17:00 时当日数据是否已完整更新（数据更新时点文档未明确，
   以用户指定的 17:00 为准，必要时调整）。
5. ✅ 已实测（2026-08-02）：指数 `ts_code` 后缀全集为
   `.SH/.SZ/.CSI/.SI/.CI/.NH/.BJ/.CNI` 共 8 种；身份注册白名单已按此校准
   （`INDEX_CODE_SUFFIXES` 与迁移/ORM 约束同步更新）。
6. 实测 2024-01-01 起的回补在逐日请求模式下总请求量级与耗时
   （约 610 交易日，节流后约 20~30 分钟完成），确认在回补窗口内可行。
