# 数据模型：每日股票列表同步

## 1. 数据所有权

MySQL 是本功能唯一业务存储，拥有股票当前值、供应商身份映射、同步结果和质量问题。
Prefect 只拥有 Flow Run 编排状态；候选股票列表在进程内存中完成全批校验后即释放。

本功能不创建：

- 每日列表快照或股票属性历史版本；
- 行情、成交、财务、指标或公司信息表；
- ClickHouse 表；
- Redis 业务键或缓存；
- API 或前端专用模型。

## 2. 公共约定

- `stock_id`、`run_id`、`issue_id` 为项目生成的 UUID。
- 股票身份语义是“某证券在某交易场所的上市身份”，不是公司或发行人。
- 首期 `market_code=CN-S`。
- 规范 venue 为 `XSHG/XSHE/XBSE`。
- 事件瞬间在 Python 中使用 UTC aware `datetime`，MySQL 使用 UTC naive
  `DATETIME(6)` 并在边界显式转换。
- `business_date` 是计划时点在 `Asia/Shanghai` 的自然日。
- 字符串代码使用大小写敏感的 ASCII 排序规则；证券代码保留前导零。

## 3. StockCurrent

**表名**：`stock_current`

保存最近一次成功同步发布的股票当前值。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `stock_id` | `CHAR(36)` ASCII | 否 | 主键、项目稳定 UUID |
| `market_code` | `CHAR(4)` ASCII | 否 | 首期 `CN-S` |
| `venue_code` | `CHAR(4)` ASCII | 否 | `XSHG/XSHE/XBSE` |
| `security_code` | `VARCHAR(32)` ASCII | 否 | 交易场所内证券代码 |
| `display_name` | `VARCHAR(160)` | 否 | 当前股票名称 |
| `currency_code` | `CHAR(3)` ASCII | 否 | 首期 `CNY` |
| `listing_status` | `VARCHAR(16)` ASCII | 否 | 当前规范上市状态 |
| `listed_on` | `DATE` | 是 | 上市日期 |
| `delisted_on` | `DATE` | 是 | 退市日期 |
| `last_seen_run_id` | `CHAR(36)` ASCII | 否 | 最近成功观察该股票的同步 |
| `last_seen_at` | `DATETIME(6)` | 否 | 最近成功观察时间 UTC |
| `created_at` | `DATETIME(6)` | 否 | 首次创建时间 UTC |
| `updated_at` | `DATETIME(6)` | 否 | 最近字段变化或确认时间 UTC |

**键与索引**：

- 主键：`stock_id`。
- 唯一键：`(market_code, venue_code, security_code)`。
- 查询索引：`(market_code, listing_status, venue_code, security_code)`。
- 名称筛选首期采用受控的前缀/包含查询；没有明确搜索规模前不新增全文索引。

**状态枚举**：

| 值 | 含义 |
|----|------|
| `ACTIVE` | 已上市 |
| `DELISTED` | 已退市 |
| `SUSPENDED` | 暂停上市 |
| `PENDING` | 已批准但尚未交易 |

**校验规则**：

- `market_code/venue_code/security_code/display_name/currency_code/listing_status`
  不得为空。
- `security_code` 只包含 Adapter 已验证的交易场所代码字符，必须保留前导零。
- `listed_on` 和 `delisted_on` 同时存在时，`delisted_on >= listed_on`。
- `DELISTED` 必须具有 `delisted_on`；其他状态不得因为字段缺失自动生成日期。
- 任何股票不得因一次同步缺席而删除或改变状态。

## 4. StockProviderMapping

**表名**：`stock_provider_mapping`

隔离供应商身份与项目股票身份。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `provider_code` | `VARCHAR(32)` ASCII | 否 | 联合主键，稳定 Provider 代码 |
| `provider_security_id` | `VARCHAR(96)` ASCII | 否 | 联合主键，供应商证券标识 |
| `stock_id` | `CHAR(36)` ASCII | 否 | 项目股票身份 |
| `last_seen_run_id` | `CHAR(36)` ASCII | 否 | 最近成功观察同步 |
| `last_seen_at` | `DATETIME(6)` | 否 | 最近成功观察时间 UTC |
| `created_at` | `DATETIME(6)` | 否 | 映射创建时间 UTC |

**约束**：

- 主键：`(provider_code, provider_security_id)`。
- 唯一键：`(provider_code, stock_id)`；一个 Provider 在当前值模型中只能有一个当前标识。
- 同一 Provider 标识不得映射到多个 `stock_id`。
- 该表只用于身份解析和来源追溯，业务筛选不得依赖供应商标识。
- 新 Provider 首次对账时，可在规范 venue + code 唯一且无冲突时附加映射。

## 5. StockListSyncRun

**表名**：`stock_list_sync_run`

每个业务计划周期恰有一条权威同步结果；失败后的补跑复用该记录并增加尝试数。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `run_id` | `CHAR(36)` ASCII | 否 | 主键 UUID |
| `run_key` | `CHAR(64)` ASCII | 否 | 规范周期键 SHA-256 |
| `schedule_slug` | `VARCHAR(64)` ASCII | 否 | 首期 `daily-stock-list` |
| `scheduled_for` | `DATETIME(6)` | 否 | 计划时点 UTC |
| `business_date` | `DATE` | 否 | 北京时间业务日期 |
| `scope_code` | `VARCHAR(32)` ASCII | 否 | 首期 `CN-S` |
| `scope_fingerprint` | `CHAR(64)` ASCII | 否 | venue/status 范围摘要 |
| `provider_code` | `VARCHAR(32)` ASCII | 否 | 本次 Provider |
| `flow_run_id` | `VARCHAR(64)` ASCII | 否 | 最近尝试的 Prefect 标识 |
| `status` | `VARCHAR(12)` ASCII | 否 | `PENDING/RUNNING/SUCCEEDED/FAILED` |
| `attempt_count` | `INT UNSIGNED` | 否 | 从 1 开始 |
| `started_at` | `DATETIME(6)` | 是 | 最近尝试开始 UTC |
| `completed_at` | `DATETIME(6)` | 是 | 最近尝试终态 UTC |
| `published_at` | `DATETIME(6)` | 是 | 成功事务提交 UTC |
| `segment_count` | `SMALLINT UNSIGNED` | 否 | 预期分区数，首期 12 |
| `completed_segment_count` | `SMALLINT UNSIGNED` | 否 | 成功分区数 |
| `capped_segment_count` | `SMALLINT UNSIGNED` | 否 | 恰好触及来源上限的分区数 |
| `received_count` | `INT UNSIGNED` | 否 | 12 分区原始行数 |
| `valid_count` | `INT UNSIGNED` | 否 | 去重后有效股票数 |
| `duplicate_count` | `INT UNSIGNED` | 否 | 已解决的完全相同重复数 |
| `invalid_count` | `INT UNSIGNED` | 否 | 无效记录数 |
| `conflict_count` | `INT UNSIGNED` | 否 | 身份或字段冲突数 |
| `baseline_count` | `INT UNSIGNED` | 是 | 上一成功列表映射数 |
| `added_count` | `INT UNSIGNED` | 否 | 新增股票数 |
| `updated_count` | `INT UNSIGNED` | 否 | 字段发生变化数 |
| `unchanged_count` | `INT UNSIGNED` | 否 | 字段未变化数 |
| `candidate_digest` | `CHAR(64)` ASCII | 是 | 规范排序候选列表 SHA-256 |
| `error_category` | `VARCHAR(48)` ASCII | 是 | 统一安全错误类别 |
| `error_summary` | `VARCHAR(500)` | 是 | 脱敏摘要 |
| `created_at` | `DATETIME(6)` | 否 | 周期首次创建 UTC |
| `updated_at` | `DATETIME(6)` | 否 | 最近状态更新时间 UTC |

**键与规则**：

- `run_key` 唯一；生成输入为
  `schedule_slug + scheduled_for_utc + scope_fingerprint`。
- 同一 `run_key` 的相同 `flow_run_id` 不增加尝试数。
- `SUCCEEDED` 不可重开；重复触发直接返回既有结果。
- `FAILED` 可显式补跑为 `RUNNING`，`attempt_count` 增加。
- `SUCCEEDED` 要求：
  - `completed_segment_count = segment_count = 12`
  - `capped_segment_count = 0`
  - `valid_count > 0`
  - `invalid_count = conflict_count = 0`
  - 发布事务成功
- `published_at` 只在 `SUCCEEDED` 时存在。
- 失败不得更改 `stock_current` 或 Provider 映射。

## 6. StockListSyncIssue

**表名**：`stock_list_sync_issue`

保存同步或候选记录的安全数据质量问题。

| 字段 | MySQL 类型 | 可空 | 说明 |
|------|------------|------|------|
| `issue_id` | `CHAR(36)` ASCII | 否 | 主键 UUID |
| `run_id` | `CHAR(36)` ASCII | 否 | 所属计划周期 |
| `attempt_no` | `INT UNSIGNED` | 否 | 发生问题的尝试 |
| `category` | `VARCHAR(32)` ASCII | 否 | 问题类别 |
| `provider_security_id_hash` | `CHAR(64)` ASCII | 是 | 可选标识摘要 |
| `venue_code` | `CHAR(4)` ASCII | 是 | 规范 venue |
| `security_code` | `VARCHAR(32)` ASCII | 是 | 安全定位代码 |
| `field_name` | `VARCHAR(64)` ASCII | 是 | 规范字段 |
| `safe_summary` | `VARCHAR(500)` | 否 | 白名单脱敏摘要 |
| `payload_hash` | `CHAR(64)` ASCII | 是 | 原始候选摘要，不保存原文 |
| `created_at` | `DATETIME(6)` | 否 | 检测时间 UTC |

问题类别至少包括：

```text
SEGMENT_FAILED
SEGMENT_CAPPED
EMPTY_AGGREGATE
INVALID_FIELD
UNKNOWN_ENUM
DUPLICATE
IDENTITY_CONFLICT
BASELINE_MISSING
PERSISTENCE_ERROR
```

完全相同重复行可标记为 `DUPLICATE` 并在去重后继续；其他未解决问题导致整批失败。
表中禁止 Token、连接串、完整请求/响应和原始供应商行。

## 7. 关系

```text
StockListSyncRun 1 ── * StockListSyncIssue
StockListSyncRun 1 ── * StockCurrent（通过 last_seen_run_id）
StockCurrent 1 ── * StockProviderMapping
```

删除同步结果、股票或映射均不属于本功能正常流程；迁移 downgrade 仅用于明确的开发回滚。

## 8. 状态转换

```text
PENDING → RUNNING → SUCCEEDED
                  ↘ FAILED → RUNNING
```

- `PENDING → RUNNING`：成功认领唯一周期。
- `RUNNING → SUCCEEDED`：全批校验通过并在同一事务发布。
- `RUNNING → FAILED`：Provider、完整性、身份、验证或持久化失败。
- `FAILED → RUNNING`：显式补跑同一计划周期。
- 运行心跳超过配置期限时，下一次补跑先记录 `ABANDONED` 类问题，再重入 `RUNNING`。

## 9. 原子发布

候选集在内存验证通过后，一个 MySQL 事务必须：

1. 按 `run_key` 锁定同步记录。
2. 再次确认状态不是 `SUCCEEDED`。
3. 解析或创建 `stock_id` 与 Provider 映射。
4. 批量 upsert `stock_current`；保持 `created_at`，更新显式字段和最近观察信息。
5. 不删除或修改任何候选集中缺席的旧股票。
6. 更新同步计数、摘要、终态和 `published_at`。
7. 提交。

事务任一步失败则整体回滚；随后独立事务记录 `FAILED` 和问题。消费者因此只会看到上一个
成功列表或完整的新列表，不会看到半批更新。

## 10. 数据生命周期

- 股票当前值和 Provider 映射长期保留，不因来源缺席自动清理。
- 同步结果和脱敏质量问题长期保留，用于幂等、审计和排障。
- 不持久化候选完整列表、原始供应商行或额外股票字段。
- JSONL 日志按 10 MiB 轮转，保留 5 个归档文件；日志生命周期独立于 MySQL。

## 11. 查询语义

应用内查询支持：

- 按 `market_code` 获取当前列表；
- 按 `venue_code`、`listing_status` 精确筛选；
- 按 `security_code` 精确或前缀筛选；
- 按 `display_name` 受控包含筛选。

查询只返回 `stock_current` 规范字段和项目 `stock_id`，不返回 Provider 标识、
同步错误、原始字段或本功能范围外数据。
查询仅由项目内部已完成授权的调用方使用；认证、授权和访问控制由调用入口负责。
性能验收使用 10,000 条当前记录，完成一次预热后连续执行 100 次代表性查询，
至少 95 次在 1 秒内返回。若名称包含查询无法稳定满足目标，优先依据测量结果增加
受控索引或收窄匹配方式，不引入缓存或新存储。

## 12. 需求追溯

- `stock_current`：FR-003、FR-004、FR-007、FR-009、FR-010、FR-013。
- Provider 映射：FR-007、ED-005、ED-006。
- 同步结果：FR-001、FR-005、FR-008、FR-011、FR-012。
- 质量问题：FR-006、FR-011、NFR-004。
- 字段限制与无其他表：FR-002、FR-014、ED-001、ED-002、SC-005。
- 原子发布和长期保留：NFR-003、NFR-006、NFR-009、SC-004。
- 内部查询及性能：FR-013、NFR-010、SC-009。
