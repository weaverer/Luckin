# 数据模型：投资工作台与任务通知

## 1. 数据所有权与存储职责

- MySQL 拥有账号、重要日、自选分组、任务汇总快照和通知尝试，提供事务、唯一约束和持久审计。
- Redis 仅拥有短期登录会话和 CSRF 状态，不作为用户、个人配置或通知结果的事实来源。
- 现有 MySQL `trading_calendar`、`stock_current`、`stock_provider_mapping`、
  `broker_recommendation` 及各同步运行表继续由原领域拥有，本功能只读。
- 现有 ClickHouse `daily_quote` 继续由行情领域拥有，本功能只读；页面不得触发供应商调用。
- Prefect 拥有 Flow 编排状态；MySQL 业务运行表和本功能任务汇总是业务终态与通知审计事实来源。
- `ScheduledTaskCatalog` 是代码内配置，描述应在某业务日期 20:00 前运行的计划任务；
  它不是数据库实体，必须通过契约测试与 `prefect.yaml` 的计划 Deployment 对齐。

跨系统业务时间使用 UTC `DATETIME(6)` 和 ISO 8601；`business_date`、`event_date` 使用
`Asia/Shanghai` 的业务 `DATE`。展示层负责时区转换。

## 2. MySQL 统一物理治理

以下七张新增表全部遵循宪章 VI，不申请例外：

- `id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID'` 为物理主键。
- 对外或跨层引用使用 `CHAR(36)` UUID 业务标识并建立 `UNIQUE`，不暴露物理主键。
- 每表均包含数据库维护字段：
  `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'`；
  `updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'`。
- 每张表和每个字段都必须使用本文件给出的中文注释；ORM、Alembic 和实际 DDL 必须一致。
- 所有外键建立索引；删除账号、分组或汇总时由 Service 显式执行受控事务，不依赖无提示级联删除。

## 3. AppUser

**表名**：`app_user`

**表注释**：`应用用户`

管理员预置的内部账号。系统不提供用户注册和找回密码。

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `user_id` | `CHAR(36)` ASCII | 否 | 用户业务UUID |
| `username` | `VARCHAR(64)` ASCII | 否 | 规范化登录名 |
| `display_name` | `VARCHAR(80)` | 否 | 用户显示名称 |
| `password_hash` | `VARCHAR(255)` ASCII | 否 | Argon2id密码哈希 |
| `status` | `VARCHAR(16)` ASCII | 否 | 账号状态：ACTIVE或DISABLED |
| `password_changed_at` | `DATETIME(6)` | 否 | 最近密码变更UTC时间 |
| `last_login_at` | `DATETIME(6)` | 是 | 最近成功登录UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `user_id`、`username` 分别唯一；`username` 保存为小写，必须匹配 `[a-z0-9][a-z0-9._-]{2,63}`。
- 新密码长度为 12–128 个 Unicode 字符，必须拒绝与当前密码相同的值；原密码验证成功后方可修改。
- `password_hash` 永不进入 API、日志或通知；禁用账号后其全部 Redis 会话失效。
- 状态转换：`ACTIVE ↔ DISABLED` 仅由管理命令执行；用户不能修改自身状态。

## 4. ImportantDate

**表名**：`important_date`

**表注释**：`用户重要日`

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `important_date_id` | `CHAR(36)` ASCII | 否 | 重要日业务UUID |
| `user_id` | `BIGINT` | 否 | 所属用户主键ID |
| `event_date` | `DATE` | 否 | 重要日期 |
| `title` | `VARCHAR(120)` | 否 | 重要日标题 |
| `title_key` | `VARCHAR(120)` | 否 | 标题规范化唯一键 |
| `notes` | `VARCHAR(1000)` | 是 | 重要日备注 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `important_date_id` 唯一；`user_id → app_user.id`。
- `(user_id, event_date, title_key)` 唯一；`title_key` 为标题去首尾空白、折叠连续空白并大小写规范化后的值。
- 标题规范化后长度 1–120，备注最多 1000；重要日不改变或覆盖交易日历。
- 所有读取、修改和删除必须同时使用当前用户身份过滤，禁止仅凭业务 UUID 跨用户访问。

## 5. WatchlistGroup

**表名**：`watchlist_group`

**表注释**：`用户自选分组`

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `group_id` | `CHAR(36)` ASCII | 否 | 自选分组业务UUID |
| `user_id` | `BIGINT` | 否 | 所属用户主键ID |
| `name` | `VARCHAR(80)` | 否 | 分组名称 |
| `name_key` | `VARCHAR(80)` | 否 | 分组名称规范化唯一键 |
| `notes` | `VARCHAR(1000)` | 否 | 分组策略备注 |
| `tags` | `JSON` | 否 | 至少一个可跨分组复用的标签字符串 |
| `sort_order` | `INT UNSIGNED` | 否 | 分组显示顺序 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `group_id` 唯一；`user_id → app_user.id`；`(user_id, name_key)` 唯一。
- 名称规范化后长度 1–80，备注长度 1–1000，标签至少一个且单项不超过 30 个字符；分组数量不设业务上限，`sort_order` 从 0 开始并由批量排序事务维护。
- 删除非空分组时必须在同一事务删除成员，并要求显式确认；不删除股票主数据。

## 6. WatchlistMember

**表名**：`watchlist_member`

**表注释**：`自选分组股票成员`

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `member_id` | `CHAR(36)` ASCII | 否 | 自选成员业务UUID |
| `group_id` | `BIGINT` | 否 | 所属自选分组主键ID |
| `stock_id` | `CHAR(36)` ASCII | 否 | 规范股票业务UUID |
| `sort_order` | `INT UNSIGNED` | 否 | 股票显示顺序 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `member_id` 唯一；`group_id → watchlist_group.id`；`stock_id → stock_current.stock_id`。
- `(group_id, stock_id)` 唯一；同一分组最多 1000 只股票。
- Service 必须先验证分组属于当前用户，再添加、删除或排序成员。

## 7. DailyTaskSummary

**表名**：`daily_task_summary`

**表注释**：`每日任务汇总`

表示 `Asia/Shanghai` 某业务日期在 20:00 观察到的唯一任务状态快照。

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `summary_id` | `CHAR(36)` ASCII | 否 | 汇总业务UUID |
| `business_date` | `DATE` | 否 | 北京时间业务日期 |
| `scheduled_for` | `DATETIME(6)` | 否 | 原定汇总UTC时点 |
| `status` | `VARCHAR(16)` ASCII | 否 | 汇总状态：BUILDING、READY或FAILED |
| `notification_status` | `VARCHAR(16)` ASCII | 否 | 通知状态：PENDING、SENDING、SENT或FAILED |
| `total_count` | `INT UNSIGNED` | 否 | 纳入统计的任务总数 |
| `succeeded_count` | `INT UNSIGNED` | 否 | 成功任务数 |
| `partial_count` | `INT UNSIGNED` | 否 | 部分完成任务数 |
| `failed_count` | `INT UNSIGNED` | 否 | 失败任务数 |
| `running_count` | `INT UNSIGNED` | 否 | 运行中任务数 |
| `unknown_count` | `INT UNSIGNED` | 否 | 无法从权威记录确定结果的任务数 |
| `not_run_count` | `INT UNSIGNED` | 否 | 应执行但未执行任务数 |
| `snapshot_digest` | `CHAR(64)` ASCII | 是 | 汇总项目规范内容SHA-256摘要 |
| `generated_at` | `DATETIME(6)` | 是 | 汇总完成UTC时间 |
| `notified_at` | `DATETIME(6)` | 是 | 最近成功通知UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `summary_id`、`business_date` 分别唯一；相同业务日期重跑复用同一行。
- `total_count` 必须等于六个状态计数之和；`READY` 时 `snapshot_digest` 和 `generated_at` 非空。
- `SENT` 时 `notified_at` 非空；成功通知后自动重跑不得再次发送，显式补发除外。
- 状态转换：`BUILDING → READY|FAILED`；失败生成可重试回 `BUILDING`；
  通知状态 `PENDING → SENDING → SENT|FAILED`，显式补发允许 `FAILED|SENT → SENDING`。

## 8. DailyTaskSummaryItem

**表名**：`daily_task_summary_item`

**表注释**：`每日任务汇总明细`

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `item_id` | `CHAR(36)` ASCII | 否 | 汇总明细业务UUID |
| `summary_id` | `BIGINT` | 否 | 所属每日汇总主键ID |
| `task_key` | `VARCHAR(128)` ASCII | 否 | 计划任务规范键 |
| `schedule_slug` | `VARCHAR(64)` ASCII | 否 | 计划调度标识 |
| `display_name` | `VARCHAR(120)` | 否 | 任务显示名称 |
| `source_domain` | `VARCHAR(64)` ASCII | 否 | 运行数据所属领域 |
| `status` | `VARCHAR(16)` ASCII | 否 | 归一状态 |
| `source_run_id` | `VARCHAR(128)` ASCII | 是 | 来源运行业务标识 |
| `source_flow_run_id` | `VARCHAR(128)` ASCII | 是 | Prefect Flow Run标识 |
| `started_at` | `DATETIME(6)` | 是 | 任务开始UTC时间 |
| `completed_at` | `DATETIME(6)` | 是 | 任务完成UTC时间 |
| `record_count` | `BIGINT UNSIGNED` | 是 | 成功处理记录数 |
| `error_category` | `VARCHAR(64)` ASCII | 是 | 安全错误分类 |
| `error_summary` | `VARCHAR(500)` | 是 | 脱敏错误摘要 |
| `observed_at` | `DATETIME(6)` | 否 | 状态观察UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `item_id` 唯一；`summary_id → daily_task_summary.id`；`(summary_id, task_key)` 唯一。
- `status` 仅允许 `SUCCEEDED/PARTIAL/FAILED/RUNNING/UNKNOWN/NOT_RUN`。
- `NOT_RUN` 不得伪造来源运行、开始或完成时间；`FAILED/PARTIAL` 可保存脱敏错误摘要。
- 汇总进入 `READY` 后明细不可被自动重算覆盖；显式补发只读取快照。

## 9. DailyTaskNotificationAttempt

**表名**：`daily_task_notification_attempt`

**表注释**：`每日任务通知尝试`

| 字段 | MySQL 类型 | 可空 | 说明 |
|---|---|---:|---|
| `id` | `BIGINT AUTO_INCREMENT` | 否 | 主键ID |
| `attempt_id` | `CHAR(36)` ASCII | 否 | 通知尝试业务UUID |
| `summary_id` | `BIGINT` | 否 | 所属每日汇总主键ID |
| `attempt_no` | `INT UNSIGNED` | 否 | 汇总内尝试序号 |
| `trigger_kind` | `VARCHAR(16)` ASCII | 否 | 触发类型：AUTOMATIC或MANUAL_RETRY |
| `status` | `VARCHAR(16)` ASCII | 否 | 尝试状态：RUNNING、SUCCEEDED或FAILED |
| `provider_code` | `VARCHAR(32)` ASCII | 否 | 通知实现代码 |
| `response_status` | `INT UNSIGNED` | 是 | 外部HTTP响应状态 |
| `error_category` | `VARCHAR(64)` ASCII | 是 | 安全错误分类 |
| `error_summary` | `VARCHAR(500)` | 是 | 脱敏错误摘要 |
| `started_at` | `DATETIME(6)` | 否 | 尝试开始UTC时间 |
| `completed_at` | `DATETIME(6)` | 是 | 尝试完成UTC时间 |
| `created_at` | `DATETIME` | 否 | 创建时间 |
| `updated_at` | `DATETIME` | 否 | 更新时间 |

**键与规则**：

- `attempt_id` 唯一；`summary_id → daily_task_summary.id`；`(summary_id, attempt_no)` 唯一。
- 表中禁止保存 webhook URL、签名密钥、完整请求体和原始响应体。
- 每个汇总同一时刻最多一个 `RUNNING` 尝试，由事务行锁和状态确认保证。

## 10. Redis SessionRecord

Redis Key：`auth:session:{sha256(raw_session_token)}`，TTL 为剩余空闲与绝对有效期的较小值。

| 字段 | 说明 |
|---|---|
| `user_id` | 用户业务UUID |
| `csrf_token_hash` | CSRF Token摘要 |
| `issued_at` | UTC签发时间 |
| `last_seen_at` | UTC最近活动时间 |
| `absolute_expires_at` | UTC绝对过期时间 |

规则：Cookie 中只保存高熵原始会话标识；日志只记录摘要前缀。会话过期、退出、账号禁用或改密后不可恢复。

## 11. 既有只读实体与页面投影

- **CalendarDayView**：组合 `trading_calendar` 与当前用户 `important_date`；缺失交易日记录为 `UNKNOWN`，
  不合成休市。
- **StockListView**：读取 `stock_current`，按交易场所、证券代码和 `stock_id` 稳定分页；不返回 Provider 字段。
- **StockMarketView**：按 `stock_id + trade_date` 从 ClickHouse `daily_quote` 读取日线区间，
  与股票主数据组合；停牌日无行保持无数据语义。
- **BrokerRecommendationView**：读取 `broker_recommendation` 并关联 `stock_current`，按推荐月份、券商和股票筛选。
- **LiveTaskStatusView**：`ScheduledTaskCatalog` 与各 `TaskExecutionReader` 的当前结果合并；只展示计划运行，
  不修改源运行表。

## 12. 事务、一致性与并发

- 重要日、自选分组及成员的写操作各自在单个 MySQL 事务完成；所有权检查和修改使用同一事务。
- 汇总认领锁定 `business_date` 唯一行；明细批量写入和计数/摘要转 `READY` 在同一事务完成。
- 飞书 HTTP 调用不得持有数据库事务；先创建 `RUNNING` attempt，外部调用后以短事务写终态。
- MySQL 汇总与飞书发送不存在分布式事务，采用 attempt 状态机和幂等检查实现至少一次尝试、至多一次自动成功通知。
- ClickHouse 和既有同步表均为只读；页面查询失败不得回写或触发重新同步。

## 13. 规模与索引

- 内部用户首期按不超过 50 人设计；自选分组数量不设业务上限、每组最多 200 只股票、每年 5000 个重要日。
- 股票列表按现有约 10,000 条容量设计；行情范围接口单次最多 400 个交易日，默认 120 日。
- 每日计划任务按不超过 100 项设计；汇总和通知尝试保留至少 2 年，后续归档策略在运维评审中确定。
- 必需索引：重要日 `(user_id,event_date)`；分组 `(user_id,sort_order)`；成员 `(group_id,sort_order)`；
  汇总 `business_date`；明细 `(summary_id,status)`；通知尝试 `(summary_id,attempt_no)`。
