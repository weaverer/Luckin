# 研究：股东数据交易日同步（008-sync-shareholder-data）

> 本文件为 `/speckit-plan` Phase 0 输出，记录设计决策、理由与备选方案。
> 依据：spec.md、项目宪章 1.2.0、Tushare `top10_holders`（doc_id=61）、
> `top10_floatholders`（doc_id=62）、`stk_holdernumber`（doc_id=166）文档、
> **2026-08-05 部署账户实测**（`scripts/probe_shareholder_api{1,2,3,4}.py`，
> 按用户要求以 limit=1 起步探测真实返回）、仓库既有实现调研
> （003 股票身份 / 005 审计与行情 / 006 指数因子 / 007 股票因子垂直切片）。

## 实测摘要（2026-08-05，部署账户，api.tushare.pro）

| 接口 | 返回字段（与文档逐名一致） | 单次上限 | 分页 | 无 ts_code 全市场查询 |
|------|---------------------------|----------|------|----------------------|
| `top10_holders`（前十大股东） | 9 字段：ts_code/ann_date/end_date/holder_name/hold_amount/hold_ratio/hold_float_ratio/hold_change/holder_type | **6,000 行**（实测触顶） | `has_more: True` + `offset` 翻页**实测有效** | ✅ 支持（文档标注 ts_code 必填，实测可省略） |
| `top10_floatholders`（前十大流通股东） | 同 9 字段（结构一致） | **6,000 行**（实测触顶） | 同上 | ✅ 支持 |
| `stk_holdernumber`（股东人数） | 4 字段：ts_code/ann_date/end_date/holder_num | 文档"单次最大 3,000"**已过时**：实测 3 日窗口 5,286 行完整返回（`has_more: False`） | 支持（offset 实测一致） | ✅ 支持 |

- **limit 参数生效**：`params: {"limit": 1}` 实测仅返回 1 行（用户指示的探测方式）。
- **昨日（2026-08-04）无新公告**：三个接口按 ann_date 查询均返回 0 行——
  股东数据按披露节奏发布（报告期一般为每季度最后一天），非交易日生成，
  属正常业务结果，不是错误（spec FR-014 语义）。
- **样例行**（600000.SH 2026Q1）：`holder_name='上海国际集团有限公司'`、
  `hold_amount=7086834641.0`（股）、`hold_ratio=21.2781`（%）、
  `hold_float_ratio=21.2781`、`hold_change=0.0`、`holder_type='一般企业'`；
  股东人数样例 `holder_num=98777`（300199.SZ，end_date=20260331，
  ann_date=20260429）。`holder_type` 实测取值含：一般企业、自然人、
  保险投资组合、开放式投资基金、证金等。
- **参数语义差异（实测确认）**：`top10_*` 的 `start_date/end_date` 为
  **报告期**范围（按 end_date 过滤），另有 `ann_date` 单日公告过滤参数；
  `stk_holdernumber` 的 `start_date/end_date` 为**公告日期**范围，
  另有 `enddate`（截止日期）参数。增量推进必须按公告日期，
  不能混用报告期参数。
- **报告期全市场量级**：20260331 报告期 top10_holders 全市场约 5.4 万行
  （6,000/页 × 9 页），披露高峰日（20260430）单公告日亦触顶 6,000 行——
  增量与回补都必须分页。

## 决策 1：提取模式——全市场按公告日/报告期提取 + `has_more/offset` 分页，不按股票循环

**决策**：三个接口均**不传 ts_code**，按日期窗口整体提取（实测验证）：
- **增量同步**：按交易日运行，窗口 =（本接口水位，+1 天）→ 目标日前
  一自然日，逐日提取：`top10_*` 用 `ann_date=YYYYMMDD`（单日），
  `stk_holdernumber` 用 `start_date=end_date=YYYYMMDD`；每页 6,000 行，
  `offset` 递增翻页直至 `has_more=False`。
- **水位按接口分别计算**（3 Flow 拆分后的必要正确性要求，
  `shareholder-data-service.md` §4-3）：`TOP10_HOLDERS` 取
  `shareholder_holding WHERE holder_kind='TOP10'` 的 `max(ann_date)`，
  `TOP10_FLOAT_HOLDERS` 取 `holder_kind='TOP10_FLOAT'` 的
  `max(ann_date)`，`HOLDER_COUNT` 取 `shareholder_count` 的
  `max(ann_date)`；两 top10 接口写入同一张表，**不得用表级水位**
  （先运行的接口会把后运行接口的当日公告一并跳过）。
- **初始化回补**：`top10_*` 按**报告期**逐季度提取
  （`start_date/end_date` = 季度末，2024-01-01 起约 10 期 × ~9 页/期
  ≈ 90 次请求/接口）；`stk_holdernumber` 按**公告日**逐日提取
  （约 630 日 ≈ 630 次请求）。合计约 800 次请求 @400/分钟 ≈ 2~3 分钟。

**理由**：实测证明 ts_code 可省略（文档标注"必填"不准确），全市场按日期
提取把请求量从"股票数 × 期间数"（5,874 股 × 10 期 ≈ 5.9 万次）降为
"日期/期间数"（约 800 次），且与 007"按交易日整体提取、不得按股票循环"
的提取哲学一致（spec FR-004 精神）；`has_more/offset` 分页实测有效，
完整性可证明（spec FR-006/ED-003）。按公告日推进与数据披露节奏一致
（spec 边界情况："增量同步以公告日期为推进依据，不按报告期简单截断"）。

**备选方案**：
- 按 ts_code 逐股循环（文档标注必填的保守做法）：被拒，实测证明无需
  ts_code；逐股 5,874 次/轮在 400/min 限流下约 15 分钟/轮，且回补
  5.9 万次 ≈ 2.5 小时，量级不可接受。
- 增量用报告期区间参数（`start_date/end_date`）：被拒，`top10_*` 的
  start/end 语义是报告期而非公告日，会反复重取已同步报告期数据，
  且无法表达"仅新公告"语义；公告日水位与披露节奏对齐。

## 决策 2：业务身份与存储——ClickHouse 两张表（`shareholder_holding` 含
`holder_kind` 判别 + `shareholder_count`），`ReplacingMergeTree(updated_at)`

**决策**：
- **`shareholder_holding`**（前十大股东 + 前十大流通股东统一存储）：
  引擎 `ReplacingMergeTree(updated_at)`，`ORDER BY (end_date, stock_id,
  holder_kind, holder_name)`，`PARTITION BY toYYYYMM(end_date)`；
  判别列 `holder_kind`（`TOP10` / `TOP10_FLOAT`，Enum）；数据列
  `ann_date`、`hold_amount`（Decimal(24,2)，实测最大 70 亿股）、
  `hold_ratio`/`hold_float_ratio`（Decimal(12,4)，%）、
  `hold_change`（Decimal(24,2)，可负）、`holder_type`（Nullable(String)）。
- **`shareholder_count`**（股东人数）：引擎同上，
  `ORDER BY (end_date, stock_id)`，`PARTITION BY toYYYYMM(end_date)`；
  数据列 `ann_date`、`holder_num`（Nullable(UInt32)）。

**理由**：业务身份（spec FR-007）——持仓类 = "股票标识 + 报告期 +
股东类型列表 + 股东名称"，股东人数 = "股票标识 + 截止日期"；
`ReplacingMergeTree(updated_at)` 同键替换天然实现**更正公告修订语义**
（同一身份出现新公告时按最新 `updated_at` 收敛，spec FR-010/ED-010，
无需显式 UPDATE）；按月分区便于按披露期清理（NFR-009）。
两个接口字段结构完全一致，一张表 + `holder_kind` 判别最简单
（宪章 V）；股东人数字段结构不同，独立成表（spec 边界情况）。

**备选方案**：
- 前十大股东与前十大流通股东分两表：被拒，字段与处理逻辑完全同构，
  两张同构表增加迁移与消费复杂度，无收益（宪章 V）。
- 三表合一宽表（股东人数并入，空列填充）：被拒，两类记录字段集不重叠，
  宽表空列浪费且类型混杂。
- MySQL 存储分析数据：被拒，分析型数据归 ClickHouse（宪章 II，沿用
  005/006/007 归属）。

## 决策 3：审计与幂等——复用 005 三张审计表，新增三个接口级 `data_kind` 取值

**决策**：复用 `market_data_sync_run/attempt/issue` 三张 MySQL 审计表，
新增数据类取值 **`TOP10_HOLDERS` / `TOP10_FLOAT_HOLDERS` /
`HOLDER_COUNT`**（`DataKind` 枚举三个新成员；与 005 每接口一
`data_kind` 的模式一致——`DAILY_QUOTE`/`ADJ_FACTOR`/`DAILY_BASIC`/
`WEEKLY_KLINE`/`MONTHLY_KLINE`，3 Flow 拆分后按接口取值的审计可
直接 `WHERE data_kind=...` 定位）；`run_key` 沿用
`scheduled_run_key`/`backfill_run_key`；状态机、租约、计数全集与
问题类别全集（含 `UNKNOWN_STOCK_IDENTITY`）原样复用。本功能**不新建任何
MySQL 表、不做任何结构性 DDL 变更**（身份表复用 003、审计表复用 005），
因此无 Alembic 迁移。

**理由**：与 007 决策 3 完全一致——005 审计模型按 `data_kind` 参数化设计，
006/007 已两次验证复用；审计语义、排障体验与质量问题分类跨功能一致
（spec NFR-005）；无 DDL 变更使宪章 VI 检查直接适用"无新建/无结构变更"
结论。股票身份复用 003 `provider_mappings`（ts_code → stock_id），
映射缺失即 `invalid_count` + 脱敏 issue（`UNKNOWN_STOCK_IDENTITY`），
跳过该条，不阻断同批其他有效数据（spec ED-005，与 007 决策 1 相同）。

**备选方案**：
- 新建 `shareholder_sync_run/attempt/issue` 三表：被拒，与 005 语义同构，
  独立演进无收益（007 已拒）。
- 自建股东身份表：被拒，003 主数据为权威，两套身份事实来源违反宪章 II
  （007 决策 1 已论证）。

## 决策 4：限流 400 次/分钟——账户级共享预算，Redis 分布式节流器
（2026-08-06 修订：3 Flow 拆分后跨进程共享）

**决策（修订后）**：400 次/分钟是**账户级共享预算**（用户澄清：三个接口的
请求**合计** ≤ 400 次/分钟，不是每接口各 400 次）。实现：
- **跨进程共享**：新建 `RedisRateLimiter`
  （`src/lucking/integrations/tushare/redis_rate_limiter.py`）——Redis ZSET
  滑窗 + Lua 原子判定（清理过期 → 计数 → 判定 → 记录），任意 60 秒窗口内
  三接口所有进程的请求合计 ≤ 400 次、最小间隔 ≥ 150 毫秒；Registry 组装时
  注入 Provider（`limiter` 参数，与进程级 `RateLimiter` 共用 `Throttle`
  契约）；Redis 不可达时**降级为进程级限流**（fail-open：请求仍被本地
  节流、不阻断同步，事件 `shareholder_rate_limiter_degraded` 上报）。
- **进程内**：单 Provider 实例三方法共用同一节流器实例。
- 被来源限流拒绝仍映射 `PROVIDER_RATE_LIMITED`（可重试）+ 退避
  30/120/300 秒 ≤ 3 次，受整体 deadline（1500 秒）约束；Flow
  `retries=0` 不叠加重试层。

**理由（修订）**：初版设计为"单 Flow 处理全部接口"（进程内共享即可）；
3 Flow 拆分（用户显式要求）后，Prefect 每个 flow run 运行于独立子进程，
进程级节流器各自计数会使账户级请求合计超过 400 次/分钟（如回补与增量
同跑、增量三 Flow 重叠）。用户澄清账户级语义后，跨进程共享预算成为
正确性要求而非缓解措施；项目已有 redis 依赖与 compose Redis 服务，
Lua 原子判定避免并发竞态；降级策略把限流基础设施故障与数据同步故障
解耦（宪章 V：可观测、可运维）。

**备选方案**：
- 每接口独立节流器：被拒，三接口共享同一供应商限流账户，独立节流器
  合计必然超过账户预算（spec FR-005 账户级语义）。
- 仅进程级 `RateLimiter` + 错峰/串行约定（修订前方案）：被拒，错峰只
  降低重叠概率、串行是运维约定而非强制，回补与增量同跑时仍可能超限；
  超限虽由重试兜底不失败，但频繁限流重试不可接受。
- 回补 Flow 内逐日 sleep：被拒，增量链路不受保护、不可单测（006/007
  已拒）。
- 固定窗口 INCR+EXPIRE：被拒，窗口边界允许瞬时双倍突发；
  ZSET 滑窗精确保证"任意 60 秒窗口 ≤ 400"（NFR-004 语义）。

## 决策 5：完整性门禁——`has_more` 驱动的 offset 分页（实测验证），
重复页/位置不前进即不完整

**决策**：单次上限按实测 **6,000 行**配置（`shareholder_data_page_limit
=6000`，三个接口统一）；每次响应检查 `has_more`：`True` 则以
`offset=offset+6000` 续取，直至 `has_more=False`；任何一页行数 == 6,000
且 `has_more=True` 但续取位置不前进、页摘要 SHA-256 重复、超过最大页数
（回补 20 页/期、增量 10 页/日）或中途失败 → 判定不完整并失败
（spec FR-006/ED-003）。`stk_holdernumber` 文档"单次最大 3,000"与实测
（5,286 行完整返回）矛盾，**以实测为准**：完整性判定完全依赖
`has_more` 标志，不依赖行数猜测（ED-008 精神：不猜测参数）。

**理由**：分页机制（`has_more`/`offset`）2026-08-05 实测验证有效——
与 007 不同（007 的 `stk_factor_pro` 无分页手段，触顶即失败），本功能
**具备合法续取手段**，完整提取可证明；用户指示的 `limit=1` 探测方式
即为 `limit` 参数可用的证据；峰值日（20260430）单公告日 6,000+ 行，
分页是必要能力而非边缘路径（spec ED-008 要求"通过真实账户验证"，
本决策即验证记录）。

**备选方案**：
- 触顶即失败（沿用 007 语义）：被拒，本接口 `has_more` 分页已实测可用，
  放弃续取将导致披露高峰日（每季度末后 1~2 天）系统性失败。
- 用 `limit=6000` 单请求赌"行数不足 6,000 即完整"：被拒，峰值日行数
  超 6,000，且无法区分"恰好 6,000"与"被截断"；`has_more` 是权威信号。

## 决策 6：调度与回补——3 接口拆分为 3 套独立 Flow（故障隔离），
中文流程名 + ASCII slug，交易日 17:00 错峰（沿用项目惯例）

**决策**：**三个接口各自拥有独立的增量 Flow 与回补 Flow**（用户显式
要求"3 个接口分成 3 个 flow，避免一个失败其他两个也受影响"），
共 6 个 Flow / 6 个 Deployment：

| 接口 | 增量 Flow（Cron，Asia/Shanghai） | `schedule_slug`（ASCII） | 回补 Flow |
|------|----------------------------------|--------------------------|-----------|
| 前十大股东（`top10_holders`） | `前十大股东交易日同步`（`0 17 * * 1-5`） | `top10-holders-sync` | `前十大股东历史回补` |
| 前十大流通股东（`top10_floatholders`） | `前十大流通股东交易日同步`（`5 17 * * 1-5`） | `top10-floatholders-sync` | `前十大流通股东历史回补` |
| 股东人数（`stk_holdernumber`） | `股东人数交易日同步`（`10 17 * * 1-5`） | `holder-count-sync` | `股东人数历史回补` |

- 每个增量 Flow：`concurrency_limit 1` + `ENQUEUE`、`retries=0`；
  目标交易日以 `prefect.runtime.flow_run.scheduled_start_time` 为准
  （直接调用必须显式提供 `scheduled_at`）；窗口下界 = 本接口水位
  （`max(ann_date)`，按接口/kind 分别计算，决策 1），上界 = 目标日前
  一自然日；逐日提取、逐日分页、本接口独立终态。
- 每个回补 Flow：参数 `start_date/end_date/backfill_batch_id`，起点
  硬编码 `2024-01-01`，拒绝未来日期与反向区间（FR-018）。
- **错峰调度**（17:00 / 17:05 / 17:10）：账户级限流已由 Redis 分布式
  节流器强保证（决策 4 修订，跨进程合计 ≤ 400 次/分钟），错峰不再是
  正确性前提，仅作**运维友好**保留——三个增量 Flow 日常基本串行执行，
  运行时间轴可预期；回补为人工触发，运维约定与增量错开。
- 流程名/Deployment 名使用简体中文且语义符合业务场景（spec FR-019），
  **内部 `schedule_slug` 保持 ASCII** 作为幂等键与审计标识
  （007 决策 6 论证）；审计 `data_kind` 按接口取值（决策 3）。

**理由**：spec FR-002/FR-003/FR-018/FR-019 与 005/007 已验证模式一一
对应（005 已按每接口独立 Deployment——复权因子同步/日线行情同步/每日
基本面同步；本决策与项目既有模式一致）；**故障隔离是用户显式要求且
符合 FR-011**——每接口独立的 run/attempt/终态，A 接口来源持续失败时
B/C 接口照常形成成功终态，维护人员可单独重跑失败接口；**账户级限流
由 Redis 分布式节流器跨进程强保证**（决策 4 修订：任意并发场景下
三接口请求合计 ≤ 400 次/分钟）；水位自愈——失败的交易日由下一运行的
自然窗口覆盖（窗口 = 水位+1 → 昨日，无独立水位存储，也不依赖审计
成功记录），回补与增量重叠区间幂等衔接（spec 边界情况，
`ReplacingMergeTree` 兜底）。

**备选方案**：
- 单 Flow 处理全部接口（初版设计）：被拒，用户显式要求拆分——单 Flow
  中任一接口失败会拖累其他两个（整批 FAILED 或需部分成功语义），
  无法独立重跑单接口，审计终态也难区分接口。
- 单 Flow + 接口参数（`holder_kind` 必填参数区分）：被拒，参数化单 Flow
  的调度、run_key、回补区间与审计仍共享一条链路的失败面（如组装错误
  时三个接口同时失败），且 `holder_kind` 进入 run_key 使幂等键复杂化；
  独立 Flow 与 005 每接口一 Deployment 的模式一致。
- 独立水位表/配置记录"上次同步公告日"：被拒，水位可从 ClickHouse
  `max(ann_date)` 按接口直接计算，无状态、自愈、免维护（宪章 V 简单性）。
- 窗口下界用上次成功运行的 target_trade_date：被拒，增量与回补两条链路
  各自记录会造成"回补后首次增量"重复整个回补范围；数据水位天然唯一。
- 沿用英文 kebab-case 流程名：被拒，违反 FR-019 用户显式约定。

## 决策 7：字段规范化——白名单严格校验（实测字段全集与文档逐名一致，无需校准）

**决策**：规范字段名 = 来源字段名**原样保留**；Adapter 内白名单
`SHAREHOLDER_DATA_FIELDS`：
- `top10_holders`/`top10_floatholders`（9 字段）：`ts_code`（仅身份解析）、
  `ann_date`（公告日期，业务列）、`end_date`（报告期，业务列）、
  `holder_name`、`hold_amount`、`hold_ratio`、`hold_float_ratio`、
  `hold_change`、`holder_type`；
- `stk_holdernumber`（4 字段）：`ts_code`（仅身份解析）、`ann_date`、
  `end_date`、`holder_num`。
严格校验（`set(row) != set(fields)` 即整批失败，防供应商字段泄漏，
spec ED-006/SC-007）。**字段全集 2026-08-05 实测与文档逐名一致**
（9+9+4），无需如 007 那样按实测校准白名单；唯一需注意的数值类型：
`hold_amount`/`hold_change` 实测为浮点大数（70 亿级）→
`Decimal(24,2)`；`hold_ratio`/`hold_float_ratio` 为百分比小数 →
`Decimal(12,4)`；`holder_num` 为整数 → `UInt32`。

**理由**：spec FR-008/ED-006/SC-007——只保存来源文档约定的全部规范字段；
实测字段集与文档一致（探测脚本已逐名核对），白名单按文档 + 实测确定，
与 007"以实测校准"同一精神（ED-008：未经验证不得猜测）；三个接口字段
数量少且稳定，白名单即最终事实来源。

**备选方案**：
- 按文档固定白名单不实测：被拒，探测是"分析真实接口返回后再落实数据
  模型"的用户显式要求；实测同时验证了 limit/分页/参数语义等设计前提。
- 动态字段透传：被拒，违反 ED-006/SC-007 白名单严格校验（007 已拒）。

## 实现验证补充（2026-08-06 实施完成）

- **探针补充（2026-08-05）**：探针 5（`scripts/probe_shareholder_api5.py`）确认
  **显式 fields 请求**与响应字段逐名一致（TushareClient 固定发送 fields 参数）、
  `has_more` 键存在且由剩余行数驱动（limit=1 时 has_more=True）——
  Adapter 分页实现前提全部实测成立（research 待验证项 1~3 ✅ 完备）。
- **替代实现/测试替身证明（ED-006/ED-007）**：Service 契约测试
  （`tests/unit/test_shareholder_data_service.py` 12 用例、
  `test_shareholder_data_backfill.py` 4 用例、`test_shareholder_data_failure.py`
  5 用例）全部基于 `tests/contract/shareholder_data_memory.py` 的 Memory 替身
  运行，与 Tushare Adapter（`test_shareholder_data_provider.py` 13 用例）零耦合；
  替换 Provider 实现后行为不变由替身重跑同一验收集证明（007 已验证该模式）。
- **质量门禁（2026-08-06）**：`ruff check` 0 错误、`mypy --strict`（67 源文件）
  0 错误、`pytest` 全量（`-m "not mysql"`）292 通过 / 38 跳过 / 1 既有失败
  （`test_stock_list_flow.py` 断言 stock_list deployment 名为 "default"，
  与仓库在途的 003 中文流程名改动冲突，**与 008 无关的既有失败**）；
  本功能 237 单元/契约 + 12 集成用例（含真实 ClickHouse 发布）全部通过。
- **共享客户端扩展**：`TushareTable` 新增 `has_more` 字段（信封级完整性标志，
  默认 False，向后兼容）；005/006/007 全部既有测试通过，证明无回归。
- **修订 vs 冲突语义的实测校准**：正常增量窗口（水位+1 → 昨日）下入站
  ann_date 恒大于既有水位，值变化均为"新公告修订"（updated）；冲突路径
  （非新公告值变化）在回补重跑同披露期时触发——与 spec FR-010/ED-010
  语义一致，契约测试按此构造（回补命令 + ann 相等场景）。
- **集成测试环境注记**：真实 ClickHouse 存在 007 实跑残留数据
  （`stock_factor` 表 2024-01 各交易日 5,000+ 行），导致 007 自身 3 个
  集成用例失败（数据污染，与 008 无关）；008 集成测试使用测试专属
  stock_id 命名空间并按期清理，不依赖表空置。

## 来源

- Tushare 文档：`top10_holders`（https://tushare.pro/document/2?doc_id=61）、
  `top10_floatholders`（https://tushare.pro/document/2?doc_id=62）、
  `stk_holdernumber`（https://tushare.pro/document/2?doc_id=166）
- 实测脚本：`scripts/probe_shareholder_api{1,2,3,4}.py`（部署账户，
  2026-08-05；探针 1 按用户指示 limit=1 获取昨日数据，探针 2 验证全市场
  查询，探针 3 验证行数量级与 `has_more`，探针 4 验证 offset 翻页）
- 项目文档：`specs/007-sync-stock-factors/{research,plan,data-model,
  contracts,quickstart}.md`（主要参考，含 RateLimiter 泛化与
  UNKNOWN_STOCK_IDENTITY 复用依据）、`specs/006-sync-index-factors/`、
  `specs/005-a-share-trend-data/`、`specs/003-stock-list-sync/`、
  `src/lucking/`（models/market_data.py 的 DataKind 与 run_key、
  repositories/stock_list.py 的 provider_mappings、
  integrations/tushare/rate_limiter.py、clickhouse.py、config.py）、
  `prefect.yaml`、`pyproject.toml`

## 部署前待验证项（上线门禁）

1. ✅ 已实测（2026-08-05）：三个接口**无需 ts_code 即可全市场查询**；
   `limit` 参数生效（limit=1 探测方式）；按公告日过滤可用；
   昨日（20260804）无新公告返回 0 行属正常披露节奏。
2. ✅ 已实测（2026-08-05）：返回字段全集 9+9+4 与文档**逐名一致**；
   数值形态确认（hold_amount 70 亿级浮点、ratio 百分比小数、
   holder_num 整数）；`top10_*` 的 start/end 为报告期语义、
   `stk_holdernumber` 的 start/end 为公告日期语义。
3. ✅ 已实测（2026-08-05）：单次上限 6,000 行 + `has_more` 标志 +
   `offset` 翻页有效（报告期 20260331 全市场 ~5.4 万行 / 9 页；
   披露高峰日 20260430 单日触顶 6,000 行；stk_holdernumber 3 日窗口
   5,286 行完整返回，文档"单次最大 3000"过时）。
4. 实测 400 次/分钟限流档位的拒绝形态与错误码（探测全程未触发限流，
   预期与 006/007 相同错误映射），校准节流参数与
   `PROVIDER_RATE_LIMITED` 重试退避。
5. 实测 003 `stock_provider_mapping`（tushare）对三个接口返回 ts_code
   全集的覆盖度（参考 007 实测：5,874 条映射覆盖 5,529 个 ts_code、
   覆盖 0 缺失；预期本功能 `UNKNOWN_STOCK_IDENTITY` 正常同步中不触发）。
6. 实测各接口回补的请求量与耗时（3 Flow 拆分后按接口独立回补：
   `top10_*` 按报告期 ~10 期 × ~9 页 ≈ 90 次/接口 @400/min ≈ 秒级；
   `stk_holdernumber` 按公告日 ~630 次 @400/min ≈ 2 分钟），
   确认各接口在回补窗口内可行。
7. 实测更正公告重复披露形态：对同一业务身份（股票 + 报告期 + 股东名称）
   存在多个 ann_date 的记录，确认按最新公告值收敛且不触发冲突。
