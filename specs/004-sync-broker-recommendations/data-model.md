# 数据模型：每月券商金股同步

## 1. 数据所有权

- MySQL 拥有券商金股推荐、唯一运行、执行尝试和质量问题。
- 现有 `stock_current` 与 `stock_provider_mapping` 继续由股票列表领域拥有；
  金股同步只读取它们以解析稳定 `stock_id`，不得创建或修改股票主数据。
- Prefect 拥有编排运行状态，但不替代 MySQL 中的业务周期与尝试审计。
- 单月全部分页候选只存在于进程内存；不持久化原始 Tushare 行或完整响应。
- ClickHouse 与应用 Redis 不参与本功能。

业务事件时间使用 UTC `DATETIME(6)`；宪章治理字段 `created_at/updated_at`
使用数据库维护的 `DATETIME`；应用读取后均恢复为 aware UTC。
`recommendation_month` 使用当月第一日的 `DATE` 表示自然月。

### 1.1 宪章 VI 统一物理治理

以下四张表均为项目自有新建业务表，不申请宪章例外，并统一遵循：

- `id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID'` 为物理主键。
- UUID 字段继续作为跨层稳定业务标识，使用 `CHAR(36)` 并建立 `UNIQUE`，
  不再承担物理主键职责。
- 每表包含：
  `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'`；
  `updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'`。
- `created_at/updated_at` 完全由数据库维护；应用不得显式覆盖。
  `updated_at` 表示数据库行最近一次变化，不代替 `first_seen_at`、`last_confirmed_at`、
  `started_at`、`completed_at` 或 `published_at` 的业务时间语义。
- 下列每个字段表的“说明”列即该列必须使用的非空中文 `COMMENT`；
  每个实体标题下单独给出的“表注释”即迁移必须使用的中文表 `COMMENT`。
- ORM、Alembic 迁移和实际 `SHOW CREATE TABLE` 必须在主键、唯一键、外键、
  默认值、`ON UPDATE`、排序规则及中文注释上完全一致。

## 2. BrokerRecommendation

**表名**：`broker_recommendation`

**表注释**：`券商月度金股推荐`

表示某个券商在某个自然月推荐某只项目规范股票的长期有效业务事实。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `recommendation_id` | `CHAR(36)` ASCII | 否 | 推荐业务UUID |
| `recommendation_month` | `DATE` | 否 | 目标月份第一日 |
| `broker_name` | `VARCHAR(160)` `utf8mb4_bin` | 否 | 仅规范空白后的券商名称 |
| `stock_id` | `CHAR(36)` ASCII | 否 | 项目规范股票业务UUID |
| `venue_code` | `CHAR(4)` ASCII | 否 | 规范交易场所代码：XSHG、XSHE或XBSE |
| `security_code` | `VARCHAR(32)` ASCII | 否 | 推荐时来源明确返回的规范证券代码 |
| `stock_name` | `VARCHAR(160)` | 否 | 推荐时来源明确返回并可更新的股票简称 |
| `first_seen_run_id` | `CHAR(36)` ASCII | 否 | 首次成功保存的同步运行业务UUID |
| `first_seen_at` | `DATETIME(6)` | 否 | 首次成功保存的UTC时间 |
| `last_confirmed_run_id` | `CHAR(36)` ASCII | 否 | 最近可信确认的同步运行业务UUID |
| `last_confirmed_at` | `DATETIME(6)` | 否 | 最近一次可信确认的UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与索引**：

- 物理主键：`id`。
- 业务标识唯一键：`recommendation_id`。
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
- 每次可信批次出现时刷新 `last_confirmed_*`；数据库在任意行更新时自动刷新 `updated_at`。
- 不保存 `ts_code`、Provider code、原始 payload 或范围外字段。

## 3. BrokerRecommendationSyncRun

**表名**：`broker_recommendation_sync_run`

**表注释**：`券商金股同步运行`

表示一个 3 日、4 日计划时点，或某个历史补跑批次中的单月权威运行。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `run_id` | `CHAR(36)` ASCII | 否 | 同步运行业务UUID |
| `run_key` | `CHAR(64)` ASCII | 否 | 规范运行身份的SHA-256摘要 |
| `run_kind` | `VARCHAR(12)` ASCII | 否 | 运行类型：计划运行或历史补跑 |
| `schedule_slug` | `VARCHAR(64)` ASCII | 是 | 计划运行标识；补跑为空 |
| `scheduled_for` | `DATETIME(6)` | 是 | 计划运行原定UTC时点；补跑为空 |
| `backfill_batch_id` | `VARCHAR(128)` ASCII | 是 | 历史补跑批次幂等键；计划运行为空 |
| `target_month` | `DATE` | 否 | 运行目标月份第一日 |
| `scope_fingerprint` | `CHAR(64)` ASCII | 否 | 月份、市场与契约版本的审计范围摘要 |
| `status` | `VARCHAR(12)` ASCII | 否 | 运行状态：待执行、执行中、成功或失败 |
| `attempt_count` | `INT UNSIGNED` | 否 | 已创建执行尝试数 |
| `successful_attempt_id` | `CHAR(36)` ASCII | 是 | 唯一成功执行尝试的业务UUID |
| `published_at` | `DATETIME(6)` | 是 | 成功发布的UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- 物理主键为 `id`，`run_id` 和 `run_key` 分别建立唯一约束。
- `run_key` 唯一；计划运行输入为
  `SCHEDULED + schedule_slug + scheduled_for_utc + target_month`；
  历史补跑输入为
  `BACKFILL + backfill_batch_id + target_month`。
- `scope_fingerprint` 只记录实际处理范围用于审计，不参与 `run_key`；
  Provider、配置和实际启动时间也不得改变业务运行身份。
- `SCHEDULED` 必须提供 `schedule_slug` 和 `scheduled_for`，且 `backfill_batch_id` 为空；
  `BACKFILL` 必须提供非空 `backfill_batch_id`，且计划字段为空。
- 3 日与 4 日因 `scheduled_for` 不同而形成两个 run。
- 同一补跑批次键中的每个月形成独立 run；相同批次键与月份重复提交复用该 run，
  新批次键允许主动刷新同一历史月份。
- Provider code 不进入 `run_key`，更换 Provider 不改变业务周期身份。
- `SUCCEEDED` 不可重开；重复触发直接返回既有结果且不调用 Provider。
- `FAILED` 只允许显式重试为 `RUNNING`，并新增 attempt。
- `successful_attempt_id` 只在同一事务将 run 置为 `SUCCEEDED` 时设置；
  迁移在 attempt 表创建后添加 FK。

## 4. BrokerRecommendationSyncAttempt

**表名**：`broker_recommendation_sync_attempt`

**表注释**：`券商金股同步执行尝试`

保存每次计划执行、历史补跑或失败重试的起止、来源、分页计数和终态；
进入终态后业务内容不可再修改。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `attempt_id` | `CHAR(36)` ASCII | 否 | 执行尝试业务UUID |
| `run_id` | `CHAR(36)` ASCII | 否 | 所属同步运行的业务UUID |
| `attempt_no` | `INT UNSIGNED` | 否 | 运行内从 1 递增 |
| `flow_run_id` | `VARCHAR(64)` ASCII | 否 | Prefect工作流运行标识 |
| `provider_code` | `VARCHAR(32)` ASCII | 否 | 本次选中的数据来源代码 |
| `status` | `VARCHAR(12)` ASCII | 否 | 尝试状态：执行中、成功、失败或已放弃 |
| `started_at` | `DATETIME(6)` | 否 | 实际开始的UTC时间 |
| `lease_expires_at` | `DATETIME(6)` | 否 | 运行租约到期的UTC时间 |
| `completed_at` | `DATETIME(6)` | 是 | 进入终态的UTC时间 |
| `provider_request_count` | `SMALLINT UNSIGNED` | 否 | 实际数据来源请求次数 |
| `provider_retry_count` | `SMALLINT UNSIGNED` | 否 | 初次调用之外的重试次数 |
| `provider_page_count` | `SMALLINT UNSIGNED` | 否 | 已成功取得的页面数，包含空终止页 |
| `provider_page_limit` | `INT UNSIGNED` | 否 | 本次页面行数上限 |
| `provider_last_page_count` | `INT UNSIGNED` | 否 | 终止页面原始行数 |
| `received_count` | `INT UNSIGNED` | 否 | 来源行数 |
| `valid_count` | `INT UNSIGNED` | 否 | 去重后有效候选数 |
| `added_count` | `INT UNSIGNED` | 否 | 新增推荐数 |
| `updated_count` | `INT UNSIGNED` | 否 | 业务字段更新数 |
| `unchanged_count` | `INT UNSIGNED` | 否 | 已确认但业务字段未变化数 |
| `duplicate_count` | `INT UNSIGNED` | 否 | 已解决的完全相同重复数 |
| `invalid_count` | `INT UNSIGNED` | 否 | 无效记录数 |
| `conflict_count` | `INT UNSIGNED` | 否 | 身份或字段冲突数 |
| `candidate_digest` | `CHAR(64)` ASCII | 是 | 规范排序候选的SHA-256摘要 |
| `error_category` | `VARCHAR(48)` ASCII | 是 | 统一安全错误类别 |
| `error_summary` | `VARCHAR(500)` | 是 | 脱敏摘要 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- 物理主键为 `id`，`attempt_id` 建立唯一约束。
- 唯一键：`(run_id, attempt_no)`。
- `flow_run_id` 唯一；同一 Prefect 执行重复提交不得增加 attempt。
- 一个 run 最多一个 `SUCCEEDED` attempt。
- `lease_expires_at` 在认领 attempt 时使用数据库 UTC 时钟设置为当前时间加 35 分钟；
  创建后不续租。Repository 必须使用数据库 UTC 比较租约，不使用 Worker 本地时钟。
- 成功要求：
  - 来源覆盖证据完整，分页时已取得首个小于 `provider_page_limit` 的终止页；
  - 未出现重复整页、续取位置未前进、超过最大页数或中途失败；
  - `valid_count > 0`；
  - `conflict_count = 0`；`invalid_count` 可以大于 0，但每一条都必须有
    `UNKNOWN_STOCK_IDENTITY` issue，且该记录未进入推荐表；
  - 推荐 upsert、attempt 和 run 成功状态已在同一事务提交。
- 失败 attempt 的所有计数也必须保存，以支持五分钟排障。
- `provider_retry_count ≤ 3`；确定性错误为 0。

## 5. BrokerRecommendationSyncIssue

**表名**：`broker_recommendation_sync_issue`

**表注释**：`券商金股同步质量问题`

保存 attempt 级或记录级的安全数据质量问题。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `issue_id` | `CHAR(36)` ASCII | 否 | 质量问题业务UUID |
| `attempt_id` | `CHAR(36)` ASCII | 否 | 所属执行尝试的业务UUID |
| `category` | `VARCHAR(48)` ASCII | 否 | 统一问题类别 |
| `provider_security_id_hash` | `CHAR(64)` ASCII | 是 | 可选数据来源股票标识摘要 |
| `broker_name_hash` | `CHAR(64)` ASCII | 是 | 可选规范券商名称摘要 |
| `venue_code` | `CHAR(4)` ASCII | 是 | 安全定位用交易场所代码 |
| `security_code` | `VARCHAR(32)` ASCII | 是 | 安全定位代码 |
| `field_name` | `VARCHAR(64)` ASCII | 是 | 规范字段名 |
| `safe_summary` | `VARCHAR(500)` | 否 | 白名单脱敏摘要 |
| `payload_hash` | `CHAR(64)` ASCII | 是 | 原始候选摘要，不保存原文 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- 物理主键为 `id`，`issue_id` 建立唯一约束。
- `attempt_id` 外键引用 attempt 的唯一业务标识。
- issue 创建后业务内容不可修改；因此正常情况下 `updated_at = created_at`，
  但字段仍按宪章由数据库统一维护。

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
PAGINATION_NOT_VERIFIED
PAGINATION_NOT_ADVANCING
REPEATED_PAGE
MAX_PAGES_EXCEEDED
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
以上跨表引用均使用带唯一约束的 UUID 业务标识；BIGINT 物理主键不进入跨层业务契约。
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
- `FAILED → RUNNING`：显式重试原运行并创建新 attempt。
- `SUCCEEDED` 为不可重开终态。

### Attempt

```text
RUNNING → SUCCEEDED
        ↘ FAILED
        ↘ ABANDONED
```

- attempt 终态不可重开。
- `RUNNING` attempt 达到固定 `lease_expires_at` 后，下一次显式重试在数据库事务内
  再次确认已过期，先把旧 attempt 标记为 `ABANDONED` 并写 issue，再创建新 attempt。
- 首版不续租、不增加心跳字段；35 分钟租约覆盖 25 分钟 Provider deadline
  及 10 分钟发布缓冲。

### 历史补跑月份解析

补跑 Flow 对闭区间内每个月先按 `backfill_batch_id + target_month` 解析既有 run：

1. 不存在：创建 `BackfillBrokerRecommendationMonthCommand`，形成首次 BACKFILL run/attempt。
2. `SUCCEEDED`：跳过 Provider 调用并计入补跑汇总的成功跳过数。
3. `FAILED`：取得原 `run_id`，转换为 `RetryBrokerRecommendationSyncCommand` 并新增 attempt。
4. `RUNNING` 且租约有效：不创建第二 attempt，标记该月仍在进行。
5. `RUNNING` 且租约过期：先将旧 attempt 置为 `ABANDONED` 并记录问题，
   再以原 `run_id` 转换为 Retry 命令。
6. 相同批次键与月份即使 Provider、配置或 `scope_fingerprint` 变化，
   仍解析到同一 run；主动刷新必须使用新的批次键。

月份范围必须先整体校验并按首尾月份均计入的自然月数量计算：恰好 120 个月允许；
121 个月及以上、未来月份、
空范围或起始月份晚于结束月份时，不得创建任何 run。

## 8. 身份解析

对每条规范候选：

1. 使用 `(provider_code, provider_security_id)` 查询现有 `stock_provider_mapping`。
2. 使用 `(CN-S, venue_code, security_code)` 查询 `stock_current`。
3. 两者命中同一 `stock_id`：接受。
4. Provider 映射缺失但规范键唯一命中：接受该 `stock_id`，但金股同步不创建 Provider 映射；
   映射维护仍由股票列表领域负责。
5. 两者均缺失：`UNKNOWN_STOCK_IDENTITY`，保存脱敏 issue、增加 `invalid_count`
   并跳过该条；只要仍有其他有效推荐，月份可以成功。
6. 两者指向不同股票或规范键不唯一：`IDENTITY_CONFLICT`，整批失败。

不得按股票简称猜测身份，不得创建或更新 `stock_current`。若全部输入都因未知身份被跳过，
因 `valid_count = 0` 整月失败。

## 9. 原子发布

所有候选在内存完成验证后，一个 MySQL 事务必须：

1. 按唯一 `run_key` 锁定 run，确认状态为 `RUNNING` 且 attempt 所有权匹配。
2. 再次确认所有 `stock_id` 仍存在且身份一致。
3. 对每个唯一业务键：
   - 不存在：插入，设置 `first_seen_*` 与 `last_confirmed_*`。
   - 已存在且简称等业务字段变化：更新字段与 `last_confirmed_*`；
     `updated_at` 由数据库自动刷新。
   - 已存在且未变化：只刷新 `last_confirmed_*`。
   - 计划与补跑不同 run 并发命中同一业务键时，唯一约束保证只保留一行；
     股票代码必须与同一 `stock_id` 的稳定身份一致。
     股票简称等其他属性不定义跨 run 版本优先级，最终值可随事务提交顺序确定，
     并发验收不得据此判定更新丢失。
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
- `id` 仅用于数据库物理组织，不进入业务契约；UUID 业务标识和业务唯一键长期稳定。

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
- run + attempt + issue：FR-001/002/008/009/012/015/016/017，
  NFR-001/003/004/006/007/010，SC-001/003/008/009/010。
- 全批校验与原子发布：FR-006/007/009/010，NFR-002/003/006，
  SC-002/003/005。
- 股票身份复用：FR-003/004/006，ED-005/006。
- Provider 隔离和字段限制：FR-014、ED-001 至 ED-006、NFR-005、SC-006/007。
- 物理主键、UUID 业务标识、数据库维护时间及中文元数据：宪章 VI；
  不改变 FR-004/012/017 的业务身份语义。
