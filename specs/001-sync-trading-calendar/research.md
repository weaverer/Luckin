# 技术研究：定时同步交易日历

## 决策 1：通用 Tushare Client 与供应商适配器分层

**决策**：复用现有同步 `httpx.Client`，实现通用
`TushareClient.query(api_name, params, fields)`；它只负责 Token 注入、HTTP 调用、
通用响应信封、超时和 Tushare 错误分类。另由 `TushareTradingCalendarProvider`
将 `trade_cal` 行转换为项目标准日历模型。领域服务只依赖
`TradingCalendarProvider` Protocol。

**理由**：

- Tushare 官方同时提供 HTTP POST 协议，所需请求仅包含 API 名称、Token、参数和字段。
- 当前仓库已经依赖 HTTPX；直接解析少量二维数据可以减少运行依赖和内存开销。
- 当前 MySQL 驱动为 PyMySQL，Flow 使用同步 HTTP 与同步 SQLAlchemy Session，
  无需增加异步数据库驱动。
- 通用 Client 接受任意 `api_name/params/fields`，未来行情或财务接口不重复实现鉴权、
  信封解析、重试分类和脱敏。
- `trade_cal` Adapter 单独拥有 `exchange`、`cal_date`、`is_open`、
  `pretrade_date` 映射，Tushare 专有字段不泄漏到领域服务。
- Provider Port 允许未来新增其他数据源适配器；切换只改配置和组合根。

**备选方案**：

- `tushare` SDK：调用更短，但会带入当前功能不需要的 SDK/数据框依赖。
- 为每个 Tushare 接口复制 HTTP 调用：会重复鉴权、错误处理和脱敏。
- 在领域服务直接调用 Tushare：会把业务逻辑绑定到供应商载荷，难以替换。
- 动态插件框架/入口点发现：首期只有一个 Provider，显式 Registry 更简单。

**来源**：

- [Tushare 交易日历接口](https://tushare.pro/document/2?doc_id=26)
- [Tushare HTTP 调用方式](https://tushare.pro/document/1?doc_id=40)

## 决策 2：MySQL 保存当前日历

**决策**：在 MySQL 新建单表 `trading_calendar`，以
`(market_code, calendar_date)` 为联合主键；使用 MySQL
`INSERT ... ON DUPLICATE KEY UPDATE` 在单事务中批量 upsert。表不保存
历史版本，但保留首次创建时间 `created_at`，并保存最近成功写入的
`sync_mode` 和 `updated_at`。

**理由**：

- 日历是低量、面向业务判断的权威参考数据，需要唯一约束和事务性更新，符合 MySQL 职责。
- 联合主键直接实现 FR-005 和 FR-006；整批事务实现 FR-007、FR-013 和 NFR-004。
- SQLAlchemy MySQL 方言原生支持 `Insert.on_duplicate_key_update()`。
- `sync_mode` 提供当前值的最近写入来源，便于区分计划刷新与人工补数；
  该字段会覆盖更新，不承担执行历史或审计职责。

**备选方案**：

- ClickHouse：适合分析型大数据，不适合当前低量、强唯一约束和事务更新。
- 每市场一张表：复制模式和代码，且妨碍 `HK-S` 等未来扩展。
- 快照或历史表：用户已明确只保存当前值。

**来源**：

- [SQLAlchemy MySQL Upsert](https://docs.sqlalchemy.org/en/20/dialects/mysql.html#insert-on-duplicate-key-update-upsert)

## 决策 3：一个 Prefect Deployment，两个计划

**决策**：建立 `trading-calendar-sync/交易日历同步` Deployment，为同一 Flow 配置两个
Cron Schedule，并通过计划参数传入 `mode`：

- `monthly`：`0 2 1 * *`，时区 `Asia/Shanghai`
- `year_end`：`30 2 20 12 *`，时区 `Asia/Shanghai`

**理由**：

- Prefect Deployment 支持多个带独立参数和时区的计划，无需复制 Flow。
- 每月 1 日 02:00 避开盘中任务；12 月 20 日准备下一年度数据，为失败重试和人工补数
  留出窗口。
- 人工补数复用同一 Flow 的 `manual` 模式，减少行为分叉。

**备选方案**：

- 系统 cron：缺少项目已有的 Prefect 状态、重试和参数管理。
- 两个独立 Flow：重复来源调用、校验、写入和日志逻辑。
- RRule：可以表达日历规则，但当前两个固定 Cron 更直观。

**来源**：

- [Prefect 创建计划](https://docs.prefect.io/v3/how-to-guides/deployments/create-schedules)
- [Prefect Schedule 概念](https://docs.prefect.io/v3/concepts/schedules)

## 决策 4：显式窗口与整批校验

**决策**：

- 月度窗口：运行日所在月的 1 日至当年 12 月 31 日。
- 年末窗口：下一自然年 1 月 1 日至 12 月 31 日。
- 人工窗口：调用者显式提供开始与结束日期，最大跨度十年。
- 写入前要求来源为 `SSE`、日期无重复、字段完整且 `pretrade_date < cal_date`
  （非空时）。
- 以 Flow 的 `as_of_date` 作为完整性边界：请求中不晚于该日期的部分必须覆盖每个自然日。
- 对晚于 `as_of_date` 的部分，允许来源只返回从请求开始日延续到某一
  `coverage_end` 的连续前缀；`coverage_end` 之后到请求结束日只能是连续未来尾部，
  状态记为 `FUTURE_PARTIAL`，这些日期不写入并查询为 `UNKNOWN`。
- 返回结果存在内部断点、越界日期、历史/当日缺口时整批拒绝；未来专属范围返回空批次
  仍然拒绝，避免把上游故障误判为“尚未公布”。

**理由**：Tushare 交易日历同时返回开市和休市日期，因此来源已覆盖的前缀中不应存在
自然日缺口；但规格要求尚未公布的未来日期保持 `UNKNOWN`。以 `as_of_date` 区分必须完整
部分与允许降级的未来尾部，既能阻止历史截断数据，又不会把未公布日期伪造成休市。

**备选方案**：

- 强制整个请求范围完整：会把尚未公布的未来日期误判为无效批次。
- 仅校验非空：无法识别历史截断或内部缺口。
- 边获取边写入：失败会留下部分更新。
- 自动删除来源未返回日期：会把“来源尚无数据”误判为应删除。

## 决策 5：区分频率限制与额度耗尽

**决策**：网络超时、连接失败、HTTP 429、明确的短时频率限制和 5xx 最多重试 3 次，
延迟为 30、120、300 秒。账户调用额度、积分或当日配额耗尽映射为
`ProviderQuotaExceededError`，本次运行不重试；Token/权限、参数、其他非零业务错误
和数据校验错误同样不重试。

**理由**：短时节流可能在退避后恢复，而账户额度或积分不足通常需要等待额度重置、
调整套餐或人工处理，立即重试只会浪费调用与时间。该分类满足 FR-008，并使运维日志
可以区分“稍后自动恢复”和“需要外部处理”。

**备选方案**：

- 将频率限制与额度耗尽统一重试：会延迟明确失败并制造无效调用。
- 无重试：短暂网络抖动会导致不必要的人工处理。

**来源**：

- [Prefect 工作流重试](https://docs.prefect.io/v3/how-to-guides/workflows/retries)

## 决策 6：结构化文件日志，不建执行表

**决策**：使用 Python 标准日志与 JSON Formatter 输出
`logs/trading-calendar-sync.jsonl`，按 10 MiB 轮转并保留 5 个文件。每个事件包含
`flow_run_id`、`schedule_slug`、`event`、市场、日期范围、覆盖结束日、完整性状态、
尝试次数、行数、
耗时和错误类别；计划运行还记录 `scheduled_at`、`started_at`、`completed_at`、
`schedule_delay_ms`、`run_duration_ms`、`schedule_to_completion_ms` 和
`timeliness_met`；
禁止记录 Token、完整请求体和数据库连接串。

**理由**：符合用户“不用记录表、写日志文件”的约束，同时 Flow Run ID 可关联 Prefect
运行上下文。标准库实现足够，不新增日志框架。

**备选方案**：

- 应用同步执行表：与已澄清范围冲突。
- 只写自由文本：难以检索状态和区分错误类别。
- 新增集中式日志服务：超出首期范围。

**来源**：

- [Prefect 工作流日志](https://docs.prefect.io/v3/how-to-guides/workflows/add-logging)

## 决策 7：不新增管理页面或同步 REST API

**决策**：人工补数通过 Prefect Deployment 参数或本地 Flow CLI 触发；下游日历判断
通过应用内 `TradingCalendarService` 契约调用。首期不新增前端页面或写操作 REST API。

**理由**：规格要求的是运维能力和下游判断，不要求公共网络接口。复用 Prefect 能避免
在现有认证体系尚未实现时增加管理端攻击面，也符合最小实现原则。

**备选方案**：

- 新增管理 REST API：需要同时建设认证、授权、审计和限流，扩大范围。
- 直接操作数据库补数：绕过校验、日志和幂等流程。

## 决策 8：测试层级

**决策**：

- 单元测试：窗口计算、市场代码、标准日历校验、错误分类、Provider 选择和日志脱敏。
- 通用 Client 契约测试：使用 `httpx.MockTransport` 验证不同 `api_name`、字段顺序、
  通用信封、错误映射和 Token 脱敏。
- Provider 一致性测试：同一套测试分别运行于 Tushare Adapter 与内存替换适配器，
  确认领域服务不依赖供应商类型。
- MySQL 集成测试：迁移、联合主键、`sync_mode` 覆盖、upsert、事务回滚、
  重复运行和重叠范围。
- Flow 集成测试：计划参数映射、重试判断、日志字段和成功/失败状态。
- 完整性测试：历史/当日缺口、未来内部断点和空批次失败；连续未来尾部缺失返回
  `FUTURE_PARTIAL` 且不合成休市记录。
- 及时性测试：使用固定计划时间与固定时钟验证三个耗时指标；运行验收按每个 Schedule
  最近 20 次计划运行统计达标率，样本不足时标为“暂定”，不把人工运行计入。

**理由**：覆盖宪章要求的外部契约、数据模型、故障路径与工作流集成，同时避免首期引入
无用户界面的端到端浏览器测试。

## 决策 9：供应商选择与迁移

**决策**：使用显式配置 `TRADING_CALENDAR_PROVIDER=tushare` 和代码内 Registry
构造 Provider。Provider 稳定标识写入 `trading_calendar.source`；供应商原生市场代码
写入 `source_market`。替换供应商时：

1. 新增实现 `TradingCalendarProvider` 的 Adapter。
2. 通过 Provider 一致性契约测试。
3. 在 Registry 注册稳定标识并配置所需秘密。
4. 在测试环境对同一日期窗口比对完整性。
5. 切换配置；不修改 Flow、Service、Repository 或表结构。

**理由**：显式 Registry 易于审查和测试，也不会引入运行时插件发现复杂度。
保留 `source/source_market` 可判断当前值来自哪个供应商，同时不保存供应商原始载荷。

**备选方案**：

- 在 Flow 中使用 `if provider == ...`：会让编排层随供应商增长。
- 将供应商选择写入每个业务调用：会扩散配置和错误处理。
- 首期实现自动故障转移：需要跨供应商数据仲裁，超出已批准范围。

## 决策 10：从预定时间测量计划及时性

**决策**：月度和年末计划以调度系统给出的预定执行时间为起点、Flow 进入终态的时间
为终点，计算 `schedule_to_completion_ms`；该值包含 Worker 排队、Provider 重试、
校验和数据库写入。另记录 `schedule_delay_ms = started_at - scheduled_at` 与
`run_duration_ms = completed_at - started_at` 以定位瓶颈。10 分钟内完成时
`timeliness_met=true`。人工运行没有预定计划时间，不参与 SC-002。

每个 Schedule 使用日志中最近 20 次已完成计划运行计算达标率；满 20 次后要求至少
95% 达标，样本不足时只报告暂定比例与样本数。测试使用固定时钟验证计算和边界，
不以缩短后的真实等待代替业务口径。

**理由**：只测实际运行时长会遗漏 Worker 未启动、并发排队和基础设施拥塞，不能证明
“预定时间后 10 分钟内完成”。拆分三个指标既保持 SC-002 的端到端定义，也能区分
调度延迟和执行性能。

**备选方案**：

- 从实际开始时间计时：无法覆盖计划排队延迟。
- 只读取 Prefect UI 人工判断：不可重复统计，也不便于日志排障。
- 新建执行统计表：与用户明确的“执行信息只写日志文件”约束冲突。

## 研究结论

所有技术未知项均已解决，没有遗留澄清项。上述方案不违反项目宪章。
