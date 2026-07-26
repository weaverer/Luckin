# 技术研究：每月券商金股同步

## 决策 1：建立独立金股垂直切片

**决策**：新增独立的 Provider、Adapter、Service、Repository、Model 和 Flow 模块，
复用现有 `TushareClient`、Registry 模式、MySQL、Prefect、配置及结构化日志组件。

**理由**：现有股票列表切片已验证 Port → Adapter → Service → Repository → Flow 边界，
但股票列表是权威全量，金股是追加与更新且缺席不删除。共享基础设施而不共享领域服务，
可以避免把相反的完整性规则混在一起。

**备选方案**：

- 将金股逻辑加入 `stock_list`：会让全量基线缺失规则误用于金股。
- 引入消息队列或新服务：当前每月两次、最多约 1,000 条的规模不需要。

## 决策 2：供应商无关 Port 与规范模型

**决策**：项目拥有 `BrokerRecommendationProvider`。输入为目标月份，输出为 Provider 代码、
目标月份、规范推荐记录、获取证据和 UTC 获取时间。规范记录只含月份、券商名称、
临时 Provider 股票标识、规范 venue、证券代码和股票简称。

**理由**：业务层只需要“某月某券商推荐某只规范股票”，不应知道端点名、Tushare 信封、
积分、错误码或专有后缀。Provider 标识只用于解析既有股票身份。

**备选方案**：

- Service 直接调用 `TushareClient`：泄漏端点、字段和错误语义。
- Adapter 直接返回 `stock_id`：越过项目股票身份解析和冲突检查。

## 决策 3：唯一端点和最小字段

**决策**：Tushare Adapter 只调用 `broker_recommend`，参数只有 `month=YYYYMM`，
请求字段严格为：

```text
month,broker,ts_code,name
```

`month` 必须等于目标月份；`broker` 传给领域层做空白规范化；
`ts_code` 仅用于 Provider 身份和 `.SH/.SZ/.BJ` 映射；
`name` 保存为来源明确提供的推荐时简称。

**理由**：官方接口只定义这四个输出字段，且说明月度金股通常每月 1–3 日更新。
精确字段白名单满足最小数据访问和 FR-014。

**备选方案**：

- 保存默认完整响应：扩大数据范围并造成供应商耦合。
- 调用股票或行情接口补字段：超出本功能范围。

**来源**：<https://tushare.pro/document/2?doc_id=267>

## 决策 4：不臆测分页，触顶保守失败

**决策**：首期单月调用一次。返回 0 行判为 `EMPTY_AGGREGATE`；
1–999 行可进入业务校验；恰好 1,000 行判为 `RESPONSE_CAPPED`，
不得发布。只有供应商正式确认并通过真实契约测试证明续取参数、稳定性和终止条件后，
才能扩展为循环提取。

**理由**：官方写明单次最大 1,000 行且“可循环提取”，但该端点公开输入只有 `month`，
没有 cursor、offset、limit、券商过滤或排序保证。使用未公开参数无法证明没有重复首页、
漏行或跨页漂移。ED-003 要求无法证明完整时失败。

**备选方案**：

- 接受 1,000 行：可能静默发布截断结果。
- 猜测 `offset/limit`：没有端点契约保证。
- 按券商分拆：官方没有券商输入参数。

**来源**：<https://tushare.pro/document/2?doc_id=267>、
<https://tushare.pro/document/1?doc_id=130>

## 决策 5：1,000 条容量与当前 Provider 上限分离

**决策**：Provider-neutral Service、Repository 和内存替身必须成功处理 1,000 条完整候选，
满足 NFR-002/SC-002；Tushare 恰好返回 1,000 条时因无法证明完整而失败。
上线前实测若触顶，必须获得受支持的续取契约或切换兼容 Provider。

**理由**：系统容量和某个来源的完整性能力是两个不同约束。降低系统容量会违反规格，
绕过当前来源上限门禁则会破坏数据可信度。

**备选方案**：

- 把系统容量降为 999：违反规格。
- 对 Tushare 特判 1,000 为成功：违反 ED-003。

## 决策 6：复用稳定股票身份，不创建第二套主数据

**决策**：Service 优先通过现有
`stock_provider_mapping(provider_code, provider_security_id)` 解析 `stock_id`，
并与 `stock_current(CN-S, venue_code, security_code)` 交叉校验。
无映射时可按唯一规范键解析已有股票，但不得由金股同步创建股票主数据；
不存在、不唯一或映射与规范键冲突时整批失败。

**理由**：`stock_current` 已拥有项目 UUID 和稳定股票身份。复用它避免 `ts_code`
成为业务键，也避免两个领域分别生成不同股票 ID。

**备选方案**：

- `ts_code` 作为推荐主键：更换 Provider 会改变业务身份。
- 金股模块生成新股票 UUID：分裂数据所有权。
- 按股票简称匹配：不可重复且容易误合并。

## 决策 7：券商名称规范化和唯一键

**决策**：券商名称使用 `" ".join(name.split())` 等价语义：
去除首尾 Unicode 空白，并将连续空白折叠为一个 ASCII 空格；
不做 Unicode NFKC、大小写、标点或别名转换。推荐唯一键为
`recommendation_month + broker_name + stock_id`，MySQL 券商列使用区分字符的排序规则。

**理由**：这精确落实澄清答案。数据库唯一键必须与领域相等语义一致，
否则 MySQL 默认不区分大小写/重音的排序规则可能错误合并名称。

**备选方案**：

- 别名字典：首版没有权威映射，可能合并不同机构。
- 完全不处理空白：常见格式噪声会制造重复。
- 仅应用层去重：并发写入仍可能重复。

## 决策 8：追加更新且缺席永不删除

**决策**：可信批次只新增首次出现的业务键、更新本批明确返回的简称等字段、
确认未变化行并刷新最近确认时间。不得扫描并删除、失效或修改本批缺席的既有推荐，
也不得使用股票列表的 `BASELINE_MISSING` 门禁。

**理由**：来源没有删除事件或完整快照声明，且用户已明确 4 日缺席时保留 3 日推荐。
该规则使接口漏数或延迟发布不会破坏已有数据。

**备选方案**：

- 4 日整月替换：会把暂时缺失误判为删除。
- 缺席软删除：同样创造了来源未提供的业务事实。
- 要求 4 日必须是 3 日超集：规格没有此要求，可能导致合法批次失败。

## 决策 9：权威计划周期与执行尝试分离

**决策**：使用 `broker_recommendation_sync_run` 表示一个唯一计划周期，
使用 `broker_recommendation_sync_attempt` 保存每次计划执行或人工补跑。
3 日和 4 日的计划时点不同，因此是两个 run；失败补跑复用原 `run_key` 并追加 attempt。
`SUCCEEDED` run 不可重开。

**理由**：FR-012 要求一个周期只有一个权威结果，FR-008 又要求每次补跑分别可追踪。
单行覆盖最近开始/完成时间会丢失旧尝试，逐次新建权威 run 又会产生多个结果。

**备选方案**：

- 完全照搬股票列表单 run 计数：不能完整保存各次补跑。
- 每次执行建立独立权威 run：违反幂等语义。

## 决策 10：数据库层并发认领

**决策**：`run_key = SHA256(schedule_slug | scheduled_for_utc | target_month |
scope_fingerprint)` 并受唯一约束。首次认领使用 MySQL 原子 insert-or-read
或捕获唯一冲突后重读并加锁，禁止简单 `SELECT → INSERT`。
同一 `flow_run_id` 重复提交返回同一 attempt；运行中周期由租约/心跳保护；
过期后记录 `ABANDONED` 问题，再允许显式补跑。

**理由**：两个并发首次触发可能都看不到行，仅靠 Prefect 并发限制无法防止多个 Worker、
人工触发或重启竞态。MySQL 唯一约束必须是最终保障。

**备选方案**：

- Redis 锁：增加状态源且不能替代业务唯一约束。
- Flow Run ID 作为周期键：它表示执行尝试，不是计划周期。

## 决策 11：全批校验与原子发布

**决策**：候选在内存完成覆盖、月份、必填字段、券商名称、股票身份、完全重复和冲突校验。
任一未解决无效或冲突导致整批失败。成功事务锁定 run/attempt，批量 upsert 推荐，
保留 `first_seen`，刷新 `last_confirmed`，写计数与摘要，并同时把 attempt/run 置为成功。
任一步失败整体回滚，再以独立事务保存失败计数和有限的安全 issue。

**理由**：消费者不会看到半批更新，失败批次也不会修改已有推荐。
单月约 1,000 条，内存校验比 staging 表简单。

**备选方案**：

- 逐行提交：会产生半批可见状态。
- 隔离坏行后仍发布：FR-009 要求存在未解决问题时失败。
- staging 表：当前规模不需要额外生命周期。

## 决策 12：瞬态重试和整体截止时间

**决策**：Tushare Adapter 对网络/超时、HTTP 429、明确短期限流和 5xx
在初次调用后最多重试 3 次，退避 30/120/300 秒；所有等待与调用受 25 分钟
单调时钟 deadline 限制。认证、权限、额度、参数、业务、载荷、空结果、触顶、
月份/身份/冲突和数据库错误不重试。Flow `retries=0`。

**理由**：匹配已澄清的“自动重试最多 3 次”，并为 30 分钟终态目标预留
持久化、日志和编排开销。只在 Adapter 重试可避免次数相乘。

**备选方案**：

- Flow 和 Adapter 双层重试：最大调用次数相乘。
- 所有错误重试：确定性错误不会自行恢复。
- 无限重试：无法在 12:30 前形成终态。

## 决策 13：计划时点来自 Prefect runtime

**决策**：Deployment 使用 Cron `0 12 3,4 * *`、`Asia/Shanghai`、
slug `monthly-broker-recommendations`、并发 1 和 `ENQUEUE`。
计划运行读取 Prefect runtime 的 `scheduled_start_time`；人工补跑必须传原计划时点。
Service 从原计划时点推导目标月份。

**理由**：排队、停机恢复或跨月补跑时，实际启动时间可能不属于原月份。
Prefect runtime 官方提供预期计划开始时间，正适合作为业务周期来源。

**备选方案**：

- `datetime.now()`：延迟跨月后会查询错误月份。
- 允许自由传 `target_month`：可能与计划时点矛盾。
- 系统 cron：绕过项目编排和可观测性。

**来源**：<https://docs.prefect.io/v3/api-ref/python/prefect-runtime-flow_run>

## 决策 14：配置、Provider 选择和迁移

**决策**：新增独立 `BROKER_RECOMMENDATION_PROVIDER=tushare` 及本域时区、日志、
截止时间和来源上限配置，共享 `TUSHARE_TOKEN/TUSHARE_API_URL`。
Registry 显式构造当前 Adapter，不做运行中静默 fallback。
替代 Adapter 必须通过相同 golden contract tests，并先影子运行对比规范摘要后再改配置。

**理由**：两个同步领域可以独立更换来源；秘密只在选中 Tushare 时解密。
静默 fallback 会混合来源语义并使运行结果不可审计。

**备选方案**：

- 复用 `STOCK_LIST_PROVIDER` 设置：耦合两个独立来源选择。
- 自动回退备用源：完整性和身份差异难以解释。
- 替换时重写既有 `stock_id`：破坏下游契约。

## 决策 15：四表 MySQL 模型

**决策**：新增：

1. `broker_recommendation`：长期有效的月度推荐事实。
2. `broker_recommendation_sync_run`：唯一权威计划周期。
3. `broker_recommendation_sync_attempt`：每次执行的不可变计数和终态。
4. `broker_recommendation_sync_issue`：有限的脱敏质量问题样本。

迁移为 revision `003`，并修正 `migrations/env.py` 的模型加载。

**理由**：MySQL 适合低量强一致事实、唯一约束和事务发布。四表是同时满足推荐查询、
周期幂等、逐次审计和问题排障的最小模型。

**备选方案**：

- 只用推荐表和日志：无法持久保证周期幂等。
- 三表且覆盖 run 尝试字段：丢失各次补跑历史。
- ClickHouse 或 Redis：不适合本功能事务事实所有权。

## 决策 16：测试与上线门禁

**决策**：

- 契约：Memory/Tushare golden semantics、唯一端点、精确字段、月份、后缀、0/999/1000、
  错误映射和替代 Provider。
- 单元：目标月、跨月补跑、Unicode 空白、唯一身份、重复、冲突、缺席不删除和重试分类。
- SQLite 集成：常规 upsert、计数、失败零发布和内部查询。
- MySQL 集成：区分字符 collation、首次并发认领、真实唯一约束、行锁、事务回滚和迁移。
- Flow/E2E：计划 runtime 时间、Cron、日志安全、3 日→4 日、30 次重复和 10 组并发补跑。
- 容量：Memory Provider 的 1,000 条成功与 Tushare fixture 的 1,000 条触顶失败。

**理由**：最高风险集中在第三方完整性、月份归属、MySQL 名称语义、并发首认领和失败原子性，
仅做单元或 SQLite 测试无法证明。

**备选方案**：

- CI 调真实 Tushare：依赖秘密、网络、积分和实时数据，不稳定。
- 只测 happy path：无法保护最关键的数据安全行为。

## 研究结论

所有计划所需技术未知项均已解决，没有遗留 `NEEDS CLARIFICATION`：

- 调度：北京时间每月 3、4 日 12:00，使用 Prefect 原计划时间。
- 目标月：原计划时点所属当前自然月。
- 当前端点：Tushare `broker_recommend`，仅四字段。
- 完整性：0 行与 1,000 行触顶失败；1–999 行继续校验。
- 容量：Provider-neutral 链路验证 1,000 条，当前来源触顶时阻断上线。
- 身份：复用现有 `stock_id` 与 Provider 映射，不持久化 `ts_code`。
- 唯一性：月份 + 精确规范券商名称 + `stock_id`。
- 生命周期：只新增/更新/确认，缺席永不删除。
- 审计：run + attempt + issue，单事务发布。
- 重试：初次调用后最多 3 次瞬态重试，Flow 不重试。
- 组件排除：无公共 API、前端、ClickHouse、应用 Redis或新依赖。

上述决策通过研究前和设计后宪章门禁。
