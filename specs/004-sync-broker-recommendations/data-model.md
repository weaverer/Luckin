# 数据模型：每月券商金股同步

## 1. 数据所有权

- MySQL 拥有券商金股推荐、唯一计划周期、执行尝试和质量问题。
- 现有 `stock_current` 与 `stock_provider_mapping` 继续由股票列表领域拥有；
  金股同步只读取它们以解析稳定 `stock_id`，不得创建或修改股票主数据。
- Prefect 拥有编排运行状态，但不替代 MySQL 中的业务周期与尝试审计。
- 候选批次只存在于进程内存；不持久化原始 Tushare 行或完整响应。
- ClickHouse 与应用 Redis 不参与本功能。

所有数据库时间为 UTC `DATETIME(6)`；应用读取后恢复为 aware UTC。
`recommendation_month` 使用当月第一日的 `DATE` 表示自然月。

## 2. BrokerRecommendation

**表名**：`broker_recommendation`

表示某个券商在某个自然月推荐某只项目规范股票的长期有效业务事实。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `recommendation_id` | `CHAR(36)` ASCII | 否 | 主键 UUID |
| `recommendation_month` | `DATE` | 否 | 目标月份第一日 |
| `broker_name` | `VARCHAR(160)` `utf8mb4_bin` | 否 | 仅规范空白后的券商名称 |
| `stock_id` | `CHAR(36)` ASCII | 否 | FK → `stock_current.stock_id` |
| `venue_code` | `CHAR(4)` ASCII | 否 | `XSHG/XSHE/XBSE` 中的规范交易场所 |
| `security_code` | `VARCHAR(32)` ASCII | 否 | 推荐时来源明确返回的规范证券代码 |
| `stock_name` | `VARCHAR(160)` | 否 | 推荐时来源明确返回并可更新的股票简称 |
| `first_seen_run_id` | `CHAR(36)` ASCII | 否 | 首次成功保存的权威周期 |
| `first_seen_at` | `DATETIME(6)` | 否 | 首次成功保存 UTC |
| `last_confirmed_run_id` | `CHAR(36)` ASCII | 否 | 最近一次在可信批次中出现的周期 |
| `last_confirmed_at` | `DATETIME(6)` | 否 | 最近一次可信确认 UTC |
| `created_at` | `DATETIME(6)` | 否 | 行创建 UTC |
| `updated_at` | `DATETIME(6)` | 否 | 业务字段最近变化 UTC |

**键与索引**：

- 主键：`recommendation_id`。
- 唯一键：`(recommendation_month, broker_name, stock_id)`。
- 查询索引：`(recommendation_month, broker_name, venue_code, security_code)`。
- 查询索引：`(recommendation_month, stock_id)`。
- `broker_name` 使用 `utf8mb4_bin` 或等价区分字符排序规则，确保除空白外的字符保持原样。

**字段规则**：

- `recommendation_month` 必须为该月第一日。
- `broker_name = " ".join(raw_name.split())` 的语义；结果不得为空，最长 160 字符。
- 不允许 NFKC、大小写、标点、简称或机构别名映射。
- `venue_code + security_code` 必须与 `stock_id` 当前规范身份一致。
- `stock_name` 不得为空；只在来源为同一业务键明确返回新简称时更新。
- `first_seen_*` 创建后不可改变。
- 每次可信批次出现时刷新 `last_confirmed_*`；仅业务字段变化时刷新 `updated_at`。
- 不保存 `ts_code`、Provider code、原始 payload 或范围外字段。

## 3. BrokerRecommendationSyncRun

**表名**：`broker_recommendation_sync_run`

表示一个 3 日、4 日计划时点或其人工补跑共享的唯一权威周期。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `run_id` | `CHAR(36)` ASCII | 否 | 主键 UUID |
| `run_key` | `CHAR(64)` ASCII | 否 | 规范计划周期 SHA-256 |
| `schedule_slug` | `VARCHAR(64)` ASCII | 否 | 计划标识 |
| `scheduled_for` | `DATETIME(6)` | 否 | 原计划 UTC 时点 |
| `target_month` | `DATE` | 否 | 从原计划时点推导的月份第一日 |
| `scope_fingerprint` | `CHAR(64)` ASCII | 否 | 月份、市场与契约版本范围摘要 |
| `status` | `VARCHAR(12)` ASCII | 否 | `PENDING/RUNNING/SUCCEEDED/FAILED` |
| `attempt_count` | `INT UNSIGNED` | 否 | 已创建执行尝试数 |
| `successful_attempt_id` | `CHAR(36)` ASCII | 是 | 成功后指向唯一成功 attempt |
| `published_at` | `DATETIME(6)` | 是 | 成功发布 UTC |
| `created_at` | `DATETIME(6)` | 否 | 周期首次创建 UTC |
| `updated_at` | `DATETIME(6)` | 否 | 周期最近状态变化 UTC |

**键与规则**：

- `run_key` 唯一；输入为
  `schedule_slug + scheduled_for_utc + target_month + scope_fingerprint`。
- `scheduled_for` 必须是 Prefect 原计划时点或人工补跑明确传入的原时点。
- 3 日与 4 日因 `scheduled_for` 不同而形成两个 run。
- Provider code 不进入 `run_key`，更换 Provider 不改变业务周期身份。
- `SUCCEEDED` 不可重开；重复触发直接返回既有结果且不调用 Provider。
- `FAILED` 只允许显式补跑为 `RUNNING`，并新增 attempt。
- `successful_attempt_id` 只在同一事务将 run 置为 `SUCCEEDED` 时设置；
  迁移在 attempt 表创建后添加 FK。

## 4. BrokerRecommendationSyncAttempt

**表名**：`broker_recommendation_sync_attempt`

不可变地保存每次计划执行或人工补跑的起止、来源、计数和终态。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `attempt_id` | `CHAR(36)` ASCII | 否 | 主键 UUID |
| `run_id` | `CHAR(36)` ASCII | 否 | FK → sync run |
| `attempt_no` | `INT UNSIGNED` | 否 | 周期内从 1 递增 |
| `flow_run_id` | `VARCHAR(64)` ASCII | 否 | Prefect Flow Run ID |
| `provider_code` | `VARCHAR(32)` ASCII | 否 | 本次选中的 Provider |
| `status` | `VARCHAR(12)` ASCII | 否 | `RUNNING/SUCCEEDED/FAILED/ABANDONED` |
| `started_at` | `DATETIME(6)` | 否 | 实际开始 UTC |
| `completed_at` | `DATETIME(6)` | 是 | 终态 UTC |
| `provider_request_count` | `SMALLINT UNSIGNED` | 否 | 实际 Provider 请求次数 |
| `provider_retry_count` | `SMALLINT UNSIGNED` | 否 | 初次调用之外的重试次数 |
| `received_count` | `INT UNSIGNED` | 否 | 来源行数 |
| `valid_count` | `INT UNSIGNED` | 否 | 去重后有效候选数 |
| `added_count` | `INT UNSIGNED` | 否 | 新增推荐数 |
| `updated_count` | `INT UNSIGNED` | 否 | 业务字段更新数 |
| `unchanged_count` | `INT UNSIGNED` | 否 | 已确认但业务字段未变化数 |
| `duplicate_count` | `INT UNSIGNED` | 否 | 已解决的完全相同重复数 |
| `invalid_count` | `INT UNSIGNED` | 否 | 无效记录数 |
| `conflict_count` | `INT UNSIGNED` | 否 | 身份或字段冲突数 |
| `candidate_digest` | `CHAR(64)` ASCII | 是 | 规范排序候选 SHA-256 |
| `error_category` | `VARCHAR(48)` ASCII | 是 | 统一安全错误类别 |
| `error_summary` | `VARCHAR(500)` | 是 | 脱敏摘要 |
| `created_at` | `DATETIME(6)` | 否 | attempt 创建 UTC |

**键与规则**：

- 唯一键：`(run_id, attempt_no)`。
- `flow_run_id` 唯一；同一 Prefect 执行重复提交不得增加 attempt。
- 一个 run 最多一个 `SUCCEEDED` attempt。
- 成功要求：
  - 来源覆盖证据完整，且 Tushare 未命中 1,000 行上限；
  - `valid_count > 0`；
  - `invalid_count = conflict_count = 0`；
  - 推荐 upsert、attempt 和 run 成功状态已在同一事务提交。
- 失败 attempt 的所有计数也必须保存，以支持五分钟排障。
- `provider_retry_count ≤ 3`；确定性错误为 0。

## 5. BrokerRecommendationSyncIssue

**表名**：`broker_recommendation_sync_issue`

保存 attempt 级或记录级的安全数据质量问题。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `issue_id` | `CHAR(36)` ASCII | 否 | 主键 UUID |
| `attempt_id` | `CHAR(36)` ASCII | 否 | FK → sync attempt |
| `category` | `VARCHAR(48)` ASCII | 否 | 统一问题类别 |
| `provider_security_id_hash` | `CHAR(64)` ASCII | 是 | 可选 Provider 股票标识摘要 |
| `broker_name_hash` | `CHAR(64)` ASCII | 是 | 可选规范券商名称摘要 |
| `venue_code` | `CHAR(4)` ASCII | 是 | 安全定位 venue |
| `security_code` | `VARCHAR(32)` ASCII | 是 | 安全定位代码 |
| `field_name` | `VARCHAR(64)` ASCII | 是 | 规范字段名 |
| `safe_summary` | `VARCHAR(500)` | 否 | 白名单脱敏摘要 |
| `payload_hash` | `CHAR(64)` ASCII | 是 | 原始候选摘要，不保存原文 |
| `created_at` | `DATETIME(6)` | 否 | 检测时间 UTC |

问题类别至少包括：

```text
PROVIDER_RATE_LIMITED
PROVIDER_UNAVAILABLE
PROVIDER_DEADLINE
AUTHENTICATION
QUOTA_EXCEEDED
EMPTY_AGGREGATE
RESPONSE_CAPPED
CONTINUATION_INCOMPLETE
MONTH_MISMATCH
INVALID_FIELD
UNKNOWN_STOCK_IDENTITY
IDENTITY_CONFLICT
DUPLICATE
RECOMMENDATION_CONFLICT
ABANDONED
PERSISTENCE_ERROR
```

问题表禁止 Token、连接串、完整请求/响应、供应商原始消息和原始行。
每个未进入有效集合的输入必须有可判断类别；完全重复可记录为 `DUPLICATE`
并在去重后继续成功。

## 6. 关系

```text
StockCurrent 1 ── * BrokerRecommendation
BrokerRecommendationSyncRun 1 ── * BrokerRecommendationSyncAttempt
BrokerRecommendationSyncAttempt 1 ── * BrokerRecommendationSyncIssue
BrokerRecommendationSyncRun 1 ── * BrokerRecommendation
```

`BrokerRecommendationSyncRun.successful_attempt_id` 回指该 run 唯一成功 attempt。
正常业务流程不删除以上实体；迁移 downgrade 仅用于明确的开发回滚。

## 7. 状态转换

### Run

```text
PENDING → RUNNING → SUCCEEDED
                  ↘ FAILED → RUNNING
```

- `PENDING → RUNNING`：数据库原子认领周期并创建 attempt。
- `RUNNING → SUCCEEDED`：可信候选原子发布。
- `RUNNING → FAILED`：Provider、验证、身份或持久化失败。
- `FAILED → RUNNING`：显式补跑原计划周期并创建新 attempt。
- `SUCCEEDED` 为不可重开终态。

### Attempt

```text
RUNNING → SUCCEEDED
        ↘ FAILED
        ↘ ABANDONED
```

- attempt 终态不可重开。
- 运行超过租约且无心跳时，下一次显式补跑先把旧 attempt 标记为 `ABANDONED`
  并写 issue，再创建新 attempt。

## 8. 身份解析

对每条规范候选：

1. 使用 `(provider_code, provider_security_id)` 查询现有 `stock_provider_mapping`。
2. 使用 `(CN-S, venue_code, security_code)` 查询 `stock_current`。
3. 两者命中同一 `stock_id`：接受。
4. Provider 映射缺失但规范键唯一命中：接受该 `stock_id`，但金股同步不创建 Provider 映射；
   映射维护仍由股票列表领域负责。
5. 两者均缺失：`UNKNOWN_STOCK_IDENTITY`，整批失败。
6. 两者指向不同股票或规范键不唯一：`IDENTITY_CONFLICT`，整批失败。

不得按股票简称猜测身份，不得创建或更新 `stock_current`。

## 9. 原子发布

所有候选在内存完成验证后，一个 MySQL 事务必须：

1. 按 `run_key` 锁定 run，确认状态为 `RUNNING` 且 attempt 所有权匹配。
2. 再次确认所有 `stock_id` 仍存在且身份一致。
3. 对每个唯一业务键：
   - 不存在：插入，设置 `first_seen_*` 与 `last_confirmed_*`。
   - 已存在且简称等业务字段变化：更新字段与 `last_confirmed_*`，刷新 `updated_at`。
   - 已存在且未变化：只刷新 `last_confirmed_*`。
4. 不查询、删除、失效或修改候选集中缺席的既有推荐。
5. 写 attempt 的全部计数、摘要、`SUCCEEDED` 和完成时间。
6. 写 run 的 `successful_attempt_id`、`SUCCEEDED` 和 `published_at`。
7. 提交。

任一步失败整体回滚；随后独立事务写 `FAILED` attempt/run 和问题。
消费者只会看到上一个状态或完整可信新增/更新，不会看到半批结果。

## 10. 数据生命周期

- 推荐记录长期保留，不因后续批次缺席自动清理。
- run、attempt 和脱敏 issue 长期保留，用于幂等、审计和排障。
- 未来清理必须按月份显式执行，并先确认不再被业务使用。
- 不保存候选快照、原始供应商行、推荐理由、行情、财务或预测字段。
- JSONL 日志按 10 MiB 轮转并保留 5 个归档；日志生命周期独立于 MySQL。

## 11. 查询语义

应用内查询必须提供目标月份，并可选按以下条件筛选：

- 规范券商名称精确匹配；
- `stock_id`；
- `venue_code`；
- `security_code`。

稳定排序为 `broker_name, venue_code, security_code, recommendation_id`，
分页限制 `1 ≤ limit ≤ 1000`、`offset ≥ 0`。
返回推荐月份、券商名称、项目股票 ID、规范 venue、证券代码和股票简称，
不返回 Provider 标识、运行问题或供应商字段。

查询仅供已完成授权的项目内部调用方使用；认证、授权和访问控制由调用入口负责。

## 12. 需求追溯

- 推荐表、唯一键和查询：FR-003/004/005/013，SC-002/004/007。
- 追加更新且缺席不删除：FR-010/011，NFR-006/008，SC-004/005。
- run + attempt + issue：FR-001/002/008/009/012，NFR-001/003/004/006/007，
  SC-001/003/008。
- 全批校验与原子发布：FR-006/007/009/010，NFR-002/003/006，
  SC-002/003/005。
- 股票身份复用：FR-003/004/006，ED-005/006。
- Provider 隔离和字段限制：FR-014、ED-001 至 ED-006、NFR-005、SC-006/007。
