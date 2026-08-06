# 研究：股票技术面因子同步（007-sync-stock-factors）

> 本文件为 `/speckit-plan` Phase 0 输出，记录设计决策、理由与备选方案。
> 依据：spec.md（含 Clarifications 2026-08-04 一条确认：保存全部字段含
> _bfq/_qfq/_hfq 全部复权变体）、项目宪章 1.2.0、
> Tushare `stk_factor_pro` 文档（doc_id=328）、仓库既有实现调研
> （003 股票身份 / 005 审计与行情 / 006 指数因子垂直切片）。

## 决策 1：股票身份解析——复用 003 股票主数据，不自建身份表

**决策**：股票身份直接复用 003 已建立的 `stock_current`/`stock_provider_mapping`
（`SqlAlchemyStockListRepository.provider_mappings(provider_code)` 返回
`{provider_security_id: stock_id}`）；本功能按 `provider_security_id`（tushare
ts_code）解析规范 `stock_id`，映射缺失即视为无效记录
（`invalid_count` + 脱敏 issue，类别 `UNKNOWN_STOCK_IDENTITY`，跳过该条）。
不新建任何 MySQL 身份表，也不做身份自举注册。

**理由**：spec 假设“股票标识以来源返回的 ts_code 为基础映射为规范股票标识，
必要时由本功能维护”；调研确认 003 主数据已覆盖三所
（venue_code 约束 `XSHG/XSHE/XBSE`，含北交所）且 `provider_mappings`
即现成的 ts_code→stock_id 解析入口——复用成本最低且与 005 行情功能
（`_to_canonical` 同用该解析）保持一致；未知 ts_code 属正常业务结果
（新股未入列表、来源返回了列表外的标识），隔离跳过不影响同交易日
其他有效数据（spec ED-004），`UNKNOWN_STOCK_IDENTITY` 类别已在 005
问题类别全集内，无需新增。

**备选方案**：
- 仿照 006 自建 `stock_factor_current`/`stock_factor_provider_mapping`：
  被拒，与 003 语义完全重复，造成同一股票两套身份并行的治理与追溯混乱
  （宪章 II 数据所有权），且 003 覆盖三所，无缺失场景需要自举。
- 后缀白名单先行过滤（.SH/.SZ/.BJ）：被拒，股票身份以 003 主数据为权威，
  后缀判断是弱信号（新股/退市/新市场后缀均以主数据为准），
  白名单过滤反而引入第二套事实来源；未知标识由 issue 类别可见
  （spec 边界情况“支持白名单扩展”由 003 同步链路承担）。

## 决策 2：分析数据存储——ClickHouse 单表 `stock_factor`，保留复权变体

**决策**：新建 ClickHouse 表 `stock_factor`，引擎 `ReplacingMergeTree(updated_at)`，
`ORDER BY (trade_date, stock_id)`，`PARTITION BY toYYYYMM(trade_date)` 按月分区，
`updated_at DateTime64(3)` 应用写入版本列；身份列 `trade_date Date`、
`stock_id FixedString(36)`、`stock_code String`（来源 ts_code）。数据列按
spec FR-008 与 Clarifications 确认保存来源文档约定的**全部字段及全部复权变体**：
行情（开/高/低/收及 _bfq/_qfq/_hfq 变体、昨收、涨跌额、涨跌幅、成交量、
成交额、换手率、量比、复权因子）、估值（PE/PB/PS/股息率/市值/股本）、
技术指标（均线、MACD、KDJ、RSI、BOLL 等及其复权变体）、连涨/连跌/区间高低
天数；因子与估值类 `Nullable(Decimal)`，天数类 `Nullable(UInt16)`。
规范字段名 = 来源字段名**原样保留**（含 `_bfq/_qfq/_hfq` 后缀），不设 TTL，
按分区显式清理。

**理由**：Clarifications 2026-08-04 确认保存全部字段含全部复权变体
（来源计算的复权指标消费方无法自行还原）；复权变体必须保留后缀以区分
（与 006 去掉 `_bfq` 后缀的决策相反——006 接口“均不复权”后缀无信息量，
本接口同指标存在多个变体，后缀是语义必要组成部分）；`ReplacingMergeTree
(updated_at)` + 版本列实现同键替换幂等，且**天然支持复权字段回溯更新**
（同一交易日重复同步返回更新的 qfq/hfq 值时 `updated_at` 递增 → 保留新值，
spec FR-010/ED-009）；宽表单次读取满足按交易日全量消费模式。

**备选方案**：
- 按 006 模式去后缀只存不复权：被拒，Clarifications 明确保存全部变体。
- 按因子类别拆多表：被拒，一次接口返回即完整记录，拆表徒增连接复杂度。
- 与 005 行情/基本面表合并（同交易日同股票多源竞争）：被拒，005 各表由
  独立接口契约驱动，合并要求跨功能统一多源冲突仲裁，超出本功能范围；
  首期独立保存，消费方按数据源选择（宪章 V 简单性，边界记录于本决策）。

## 决策 3：审计与幂等——复用 005 三张审计表，新增 `data_kind=STOCK_FACTOR`

**决策**：复用 `market_data_sync_run/attempt/issue` 三张 MySQL 审计表，
新增数据类取值 `STOCK_FACTOR`（`DataKind` 枚举新成员）；`run_key` 沿用
`scheduled_run_key`/`backfill_run_key`（已带 `data_kind` 参数）；状态机、
租约、计数全集与问题类别全集（含 `UNKNOWN_STOCK_IDENTITY`）原样复用。
本功能**不新建任何 MySQL 表、不做任何结构性 DDL 变更**（身份表复用 003、
审计表复用 005，均无列变更），因此无 Alembic 迁移。

**理由**：005 审计模型按 `data_kind` 参数化设计的目的正是“一次实现、多类
复用”（006 已以 `INDEX_FACTOR` 验证）；复用保证审计语义、排障体验与质量
问题分类跨功能一致（spec NFR-005），避免第三套并行审计表（宪章 V）；
无 DDL 变更使宪章 VI 检查在本功能直接适用“无新建/无结构变更”结论。

**备选方案**：
- 新建 `stock_factor_sync_run/attempt/issue` 三表：被拒，与 005 语义同构，
  独立演进无收益。
- 复用 003 的 `stock_list_sync_run/issue` 表：被拒，003 表无 attempt 层、
  无租约与提取计数语义（spec FR-011 需要），且与行情审计分离会造成
  同类运行两处可观测。

## 决策 4：限流 30 次/分钟——提升 006 节流器为共享 `RateLimiter`

**决策**：复用 006 已实现的进程级节流器（`IndexRateLimiter`），将其
**泛化为共享模块** `src/lucking/integrations/tushare/rate_limiter.py`
（类名 `RateLimiter`，保留 `IndexRateLimiter = RateLimiter` 兼容别名供 006
既有 import 使用；由 006 既有测试覆盖回归）：任意 60 秒窗口真实 HTTP 请求
≤ 30 次、最小间隔 ≥ 2 秒、`monotonic`/`sleep` 可注入；被来源限流拒绝仍映射
`PROVIDER_RATE_LIMITED`（可重试）+ 退避 30/120/300 秒 ≤ 3 次，受整体
deadline 约束；Flow `retries=0` 不叠加重试层。

**理由**：spec FR-005/NFR-004/SC-006 要求全程请求频率 ≤ 30 次/分钟；
006 已实现并通过全部测试的滑窗节流器与本功能需求完全一致（30/分钟、
最小间隔 2 秒、可注入），复用避免重复实现；仅文件名与类名带 index 前缀，
泛化重命名为低风险重构（测试兜底），避免股票功能 import 一个
“index”命名的模块造成认知混淆（宪章 V 可维护性）。

**备选方案**：
- 原样 import `IndexRateLimiter`：被拒，stock 功能依赖 index 命名模块，
  语义误导且后续 004 类功能复用会继续扩散错误命名。
- 回补 Flow 内逐日 sleep：被拒，增量链路不受保护、不可单测（006 已拒）。
- Redis 分布式限流器：被拒，首期单 worker 进程内足够（006 已论证）。

## 决策 5：提取模式——按交易日单请求全量，10,000 行上限为完整性门禁

**决策**：增量与回补均按交易日调用 `stk_factor_pro`（`trade_date` 参数，
不传 `ts_code/start_date/end_date`），单次请求覆盖该日全部 A 股；若返回
行数达到单次上限 **10,000**（独立配置 `stock_factor_page_limit=10000`，
不复用 006 的 8,000）且未验证续取手段，判定不完整
（`ProviderResponseCappedError`/`PROVIDER_RESPONSE_CAPPED`）并失败，
不得猜测 `ts_code` 过滤参数绕过门禁（spec FR-006/ED-003/ED-008）。

**理由**：spec FR-004 明确按交易日整体提取、不得按股票循环；来源输入参数
仅有 `ts_code/trade_date/start_date/end_date`，无分页参数，触顶时无合法
续取手段，安全失败是唯一正确行为；A 股总数约 5,400 只，预期单日行数
远低于 10,000 上限，正常情况单请求即完整；触顶概率由上线门禁实测确认
（部署前待验证项 1）。文档要求“按日期循环取更多数据”与逐日请求模式一致。

**备选方案**：
- 触顶后按 `ts_code` 前缀猜测过滤：被拒，ED-008 禁止猜测参数。
- 回补用 `start_date/end_date` 区间请求：被拒，逐日请求与既有幂等/审计
  模式一致（每日独立终态），区间请求无法逐日归属问题（006 已论证）。

## 决策 6：调度与回补——中文流程名 + 复用 005 双 Flow 模式

**决策**：新增参数化 Flow **`股票技术面因子交易日同步`**（Cron
`0 17 * * 1-5`、Asia/Shanghai、`concurrency_limit 1` + `ENQUEUE`、
`retries=0`）与人工回补 Flow **`股票技术面因子历史回补`**（参数
`start_date/end_date/backfill_batch_id`，回补起点硬编码 `2024-01-01`，
拒绝未来日期与反向区间）；Flow 名称、Deployment 名称使用简体中文且语义
符合业务场景（spec FR-019，用户显式要求）；**内部 `schedule_slug` 保持
ASCII**（如 `stock-factor-sync`）作为幂等键输入与审计标识——流程名称是
用户可见语义标识，schedule_slug 是稳定性契约，二者职责分离。回补流程
沿用 005 `backfill` 模式：区间整体校验 → 交易日历逐日展开 → 逐日幂等
（`backfill_batch_id + data_kind + target_trade_date`）→ 逐日独立终态。
目标交易日以 `prefect.runtime.flow_run.scheduled_start_time` 为准
（直接调用必须显式提供 `scheduled_at`）。

**理由**：spec FR-002/FR-003/FR-018/FR-019 与 005 已验证模式一一对应；
交易日 17:00 为用户显式指定，非交易日经交易日历判断后直接 SKIPPED
（FR-001）；中文流程名为用户显式约定（FR-019），Deployment 支持 Unicode
名称，`prefect deployment run "股票技术面因子交易日同步/股票技术面因子交易日同步"` 可正常
调用（shell 引号包裹）。

**备选方案**：
- 沿用英文 kebab-case 流程名：被拒，违反 FR-019 用户显式约定。
- 中文流程名 + 中文 schedule_slug：被拒，schedule_slug 参与 run_key 与
  审计存储，ASCII 保证与既有 005/006 审计标识体系同构、避免编码与
  输入歧义；用户可见性由流程名承担。
- 单 Flow 带模式参数：被拒，增量与回补生命周期、参数和幂等键不同
  （006 已论证）。

## 决策 7：字段规范化——原样保留后缀、可修订/稳定字段分级、白名单实测校准

**决策**：规范字段名 = 来源字段名**原样保留**（`ma_bfq_5`、`ma_qfq_5`、
`ma_hfq_5`、`close_qfq`、`adj_factor` 等，含后缀）；Adapter 内白名单
`STOCK_FACTOR_FIELDS` 以部署账户实测字段全集为准（文档分组清单作基线，
上线门禁校准，部署前待验证项 2），严格校验（`set(row) != set(fields)`
即整批失败，防供应商字段泄漏，spec ED-005/SC-007）。
**字段分级**（spec FR-010/ED-009 落实）：
- **可修订字段** = 字段名含 `_qfq`/`_hfq` 后缀者 + `adj_factor`（累计复权
  因子会回溯更新）——值随后续除权除息重算，同一交易日重复同步按来源
  最新值更新并计 `updated_count`，不视为冲突；
- **稳定字段** = 其余全部（不复权行情、估值、天数）——同键值变化即
  `RECORD_CONFLICT` 整批失败，不得任意覆盖（沿用 006 语义）。

**理由**：Clarifications 确认保存全部变体，后缀是区分变体的语义必要部分
（与 006 去后缀决策相反，决策 2）；文档注明 pct_chg 为除权后涨跌幅、
pre_close 与 close_qfq 可能因复权因子时点差异不一致——复权变体的值天然
不稳定，将“来源最新值更新”固化为业务规则（ED-009），同时稳定字段保留
006 的冲突保护（FR-010）；白名单以实测校准避免按文档猜测字段全集
（ED-008 精神：未经验证不得猜测）。

**备选方案**：
- 去后缀只存不复权变体：被拒，Clarifications 明确保存全部变体。
- 所有字段一律“最新值更新”不设冲突：被拒，不复权行情/估值字段的变化
  无业务理由，静默覆盖会掩盖来源数据质量问题（FR-010 要求识别冲突）。
- 白名单按文档清单固定：被拒，文档分组清单未逐字段标注变体全集，
  必须实测校准（上线门禁）。

## 实现验证补充（2026-08-05 实施完成）

- **替代实现/测试替身证明（ED-006/ED-007）**：Service 契约测试
  （`tests/unit/test_stock_factor_service.py` 10 用例、
  `test_stock_factor_backfill.py` 6 用例、`test_stock_factor_failure.py`
  4 用例）全部基于 `tests/contract/stock_factor_memory.py` 的 Memory 替身
  运行，与 Tushare Adapter（`test_stock_factor_provider.py` 11 用例）零耦合；
  替换 Provider 实现后行为不变由替身重跑同一验收集证明（006 已验证该模式）。
- **质量门禁（2026-08-05）**：`ruff check` 0 错误、`mypy --strict`（61 源文件）
  0 错误、`pytest` 全量 268 通过 / 11 跳过（mysql 标记需 TEST_DATABASE_URL，
  与 006 基线一致）；本功能 47 个新用例全部通过（含 8 个 -m mysql 标记的
  真实 ClickHouse 集成用例，已直跑验证）。
- **RateLimiter 泛化回归**：`tests/unit/test_index_rate_limiter.py` 5 用例
  全量通过，证明 `index_rate_limiter.py` → `rate_limiter.py` 重命名无回归
  （006 既有 import 兼容别名生效）。
- **实跑修复（2026-08-05，回补首次实跑发现）**：
  ① `ma_mass` 命名 bug（见待验证项 2 注）——修复 `period.isdigit()` 判定，
     白名单 261 字段与真实响应逐名一致（`scripts/probe_stock_factor_fields.py`）；
  ② 股本/市值 5 列 `Decimal(12,4)` 溢出（工商银行级 `total_mv`=119,545,716.605
     万元，报 "Decimal value is too big"）——改为 `Decimal(24,4)`（与 005
     `daily_basic` 一致），DDL/线上表/测试三方对齐
     （`scripts/align_stock_factor_columns.py`、`scripts/verify_wide_decimal.py`）。
     两项均补充了回归断言。
- **质量门禁**：与 005/006 相同工具链——`uv run ruff check .` 0 错误、
  `uv run mypy --strict`（按 006 口径 `src`）0 错误、`uv run pytest`
  全量通过；节流器泛化重构由 006 既有测试覆盖回归。

## 来源

- Tushare `stk_factor_pro` 接口文档：https://tushare.pro/document/2?doc_id=328
- 项目文档：`specs/006-sync-index-factors/{research,plan,data-model,contracts}.md`
  （参考流程）、`specs/005-a-share-trend-data/`、`specs/003-stock-list-sync/`、
  `src/lucking/`（models/market_data.py 的 DataKind 与 run_key、repositories/
  stock_list.py 的 provider_mappings、integrations/tushare/index_rate_limiter.py、
  services/index_factor.py、flows/index_factor.py、clickhouse.py、config.py）、
  `prefect.yaml`、`pyproject.toml`

## 部署前待验证项（上线门禁）

1. 用部署账户实测 `stk_factor_pro` 按 `trade_date` 单次全量请求的返回行数，
   确认远低于 10,000 上限（预期约 5,400 行 = A 股总数）；若触顶，必须判定
   为不兼容并重新设计提取策略。
2. ✅ 已实测（2026-08-04，trade_date=20260803 全量 5,529 行）：返回字段全集
   261 个（含 ts_code/trade_date，数据字段 259 个）——
   - 技术指标 74 基名 × `_bfq/_qfq/_hfq` 三变体全部返回（共 222），
     命名形态与预期一致（ma/ema/rsi 前缀式周期 `ma_bfq_5`，其余后缀式
     `kdj_bfq`）；
   - **价格字段仅 `_qfq/_hfq` 两复权变体，无 `_bfq` 变体**（原值即不复权），
     白名单按此校准（价格列由 12 变体改为 8）；
   - 估值 12 字段、行情 9 字段（含 `adj_factor`）、天数 4 字段全部返回；
   - `STOCK_FACTOR_FIELDS` 校准为 258 个数据字段（可修订 121 = 8 价格变体
     + 222 指标变体中的 qfq/hfq 148 + adj_factor…… 口径以代码常量为准）。
   - ⚠️ 2026-08-05 实测发现并修复命名生成 bug：`ma_mass` 的 period 段为
     "mass"（非数字），被 `_indicator_field` 误判为 ma 前缀周期组，生成了
     不存在的 `ma_bfq_mass` 等 3 名（来源真实命名为后缀式 `ma_mass_bfq`）；
     修复为 `period.isdigit()` 判定后白名单 261 字段与显式 fields 请求的
     响应**逐名完全一致**（`scripts/probe_stock_factor_fields.py` 验证），
     ClickHouse 表列同步对齐（`scripts/align_stock_factor_columns.py`）。
   （本项为 US1 契约测试前的**阻塞门禁**：已按实测结果修订 data-model.md
   §3.4 与 tushare-stock-factor.md §3、校准 `STOCK_FACTOR_FIELDS`，
   完成于 2026-08-04，对应 tasks.md 阶段 2 T008。）
3. 实测 30 次/分钟限流的实际行为（连续请求频率的拒绝形态与错误码），
   校准最小间隔节流参数与 `PROVIDER_RATE_LIMITED` 错误映射
   （006 已实测同档位，预期可复用结论）。
4. 实测 17:00 时当日数据是否已完整更新（数据更新时点文档未明确，
   以用户指定的 17:00 为准，必要时调整）。
5. ✅ 已实测（2026-08-05）：003 `stock_provider_mapping`（tushare，5,874 条）
   对 `stk_factor_pro` 返回的 5,529 个 ts_code **完全覆盖（未覆盖 0）**；
   后缀分布 .SZ 2,889 / .SH 2,308 / .BJ 332（北交所确认在列）——
   `UNKNOWN_STOCK_IDENTITY` 隔离在正常同步中预期不触发。
   （实测脚本：`scripts/measure_stock_factor_coverage.py`）
6. 实测 2024-01-01 起的回补在逐日请求模式下总请求量级与耗时
   （约 630 交易日，节流后约 20~30 分钟完成），确认在回补窗口内可行。
7. 实测复权字段回溯更新形态：对某交易日重复同步（期间发生新除权事件），
   确认 `_qfq/_hfq`/`adj_factor` 值变化按最新值更新且不触发冲突。
