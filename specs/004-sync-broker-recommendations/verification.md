# 验证记录：每月券商金股同步

验证日期：2026-07-28  
结论：实现与自动化门禁通过；真实 Tushare 分页能力尚未由部署账户证明，因此生产配置继续保持分页关闭。

## 1. 实现范围

- 新增供应商无关 `BrokerRecommendationProvider`、规范 DTO、统一异常和独立 Registry。
- 新增仅调用 Tushare `broker_recommend` 的 Adapter，字段固定为
  `month,broker,ts_code,name`。
- 新增四张 MySQL 表、ORM 和 Alembic revision `003`。
- 新增计划、历史补跑和原 run 重试 Service/Prefect Flow。
- 新增稳定 run key、数据库 UTC 固定 2,100 秒租约、原子发布、缺席不删除、
  业务键 upsert、失败 issue 和内部筛选查询。
- 新增 `0 12 3,4 * *`、`Asia/Shanghai`、并发 1、`ENQUEUE` 的计划部署，
  以及无 Cron 的 `broker-recommendation-backfill/券商金股历史回补`、
  `broker-recommendation-retry/券商金股同步重试` 部署。
- 未新增公共 API、前端、ClickHouse 写入、Redis 缓存或 Tushare SDK。

## 2. 四表真实 DDL 与中文注释

在项目 MySQL 8.4 上应用 revision `003` 后，自动化测试逐表执行
`SHOW CREATE TABLE`，并通过 SQLAlchemy Inspector 查询
`information_schema` 元数据，验证：

| 表 | 表注释 | 物理主键 | UUID 业务标识 |
|---|---|---|---|
| `broker_recommendation` | 券商月度金股推荐 | `id BIGINT AUTO_INCREMENT` | `recommendation_id UNIQUE` |
| `broker_recommendation_sync_run` | 券商金股同步运行 | `id BIGINT AUTO_INCREMENT` | `run_id UNIQUE` |
| `broker_recommendation_sync_attempt` | 券商金股同步执行尝试 | `id BIGINT AUTO_INCREMENT` | `attempt_id UNIQUE` |
| `broker_recommendation_sync_issue` | 券商金股同步质量问题 | `id BIGINT AUTO_INCREMENT` | `issue_id UNIQUE` |

四表每列的实际非空中文 `COMMENT` 均与 ORM/data-model 字段说明逐项一致；
`created_at` 使用数据库默认值，`updated_at` 使用
`DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`；
attempt 的 `lease_expires_at` 非空。推荐表业务唯一键为
`recommendation_month + broker_name + stock_id`，表默认排序规则为
`utf8mb4_bin`。循环外键、运行身份检查约束、状态检查约束和查询索引均已由真实 DDL 验证。

## 3. 迁移路径

- 既有项目库：`002 → 003` 通过。
- 重复 `alembic upgrade head`：通过，为空操作。
- 开发表为空后：`003 → 002 → 003` 通过，既有股票表保留。
- 专用临时库 `lucking_migration_validation`：
  `base → 001 → 002 → 003 → head → 002 → 003 → head` 全部通过。
- 临时库验证后已删除；项目库最终恢复并停留在 revision `003`。

## 4. 行为场景

- 3 日首次发布、4 日更新/确认、首次发现运行保持、缺席推荐不删除：通过。
- Memory Provider 2,500 条及 Tushare fixture `1,000/1,000/500`：通过。
- 分页关闭时单页达到 1,000 安全失败；分页开启时满页续取、短页终止、
  重复整页失败：通过。
- 整个月份共享最多额外 3 次重试及 `30/120/300` 秒退避：通过。
- Unicode 空白折叠且不做 NFKC、大小写或标点归一：通过。
- 计划与补跑稳定 run key：通过；Provider、配置和实际开始时间不进入身份。
- 24 月闭区间展开：通过。
- 120 月闭区间接受，121 月、未来月、反向或非月首范围在创建 run 前拒绝：通过。
- 真实 MySQL 固定租约为 2,100 秒；到期前为 `IN_PROGRESS`，数据库 UTC 到期后
  旧 attempt 转 `ABANDONED`，并在原 `run_id` 创建 attempt 2：通过。
- 10 组计划/补跑同月并发：20 个运行分别成功可追踪，10 个相同业务键只产生
  10 条推荐；未比较股票简称等其他属性的最终版本：通过。
- 失败 attempt、issue 脱敏、显式 Retry、内部查询和日志白名单：通过。
- 单条未知股票身份生成脱敏 issue、计入 `invalid_count` 并跳过，同月其他有效推荐
  继续原子发布；全部记录均无法解析时整月失败：通过。

## 5. 质量命令

```text
uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 31 source files

uv run pytest -q
103 passed, 6 skipped

LUCKING_USE_LOCAL_MYSQL_TESTS=1 uv run pytest -m mysql -s -q
3 passed, 3 skipped；真实金股 DDL、中文注释、跨运行并发与租约测试通过。
自动创建随机临时库的用例因应用账号无建库权限跳过，但同一用例已通过容器管理员预建的
专用临时库完成验证；两个既有模块的 MySQL fixture 因未设置 TEST_DATABASE_URL 跳过。

uv run alembic upgrade head
通过；项目库为 revision 003。
```

以上为最终代码状态的命令输出。

## 6. 真实 Tushare 上线门禁

本次没有向真实 Tushare 发送探测请求，避免在未确认账户权限、频率窗口和运维授权时
消耗额度或触发限制。未打印或读取 Token，未保存原始响应。

决策：

- `BROKER_RECOMMENDATION_TUSHARE_PAGINATION_ENABLED=false` 保持默认且继续作为生产安全门禁。
- 未经部署账户或供应商沙箱证明 `limit/offset` 页面前进、满页续取、短页/空页终止及
  重复页探测前，不得开启分页。
- 分页关闭时若单页达到 1,000，运行以完整性错误失败且不发布。
- 若 Tushare 无法证明续取契约，应保持阻断并接入通过同一 Provider 契约的替代来源，
  不得调高页面上限或绕过完整性校验。

## 7. 追溯与签署

- FR-001/002/015/016：计划 Flow、目标月推导、补跑区间与 Prefect 部署。
- FR-003–007/017：规范化、稳定股票身份、业务唯一键、追加更新和跨类型并发。
- FR-008–012：run/attempt/issue、固定租约、终态、原子发布和原 run Retry。
- NFR：30 分钟目标、2,500 条容量、失败安全、数据库 UTC 和可观测日志。
- ED：Provider Port、独立 Tushare Adapter、Registry、Memory Provider 和替换契约。
- 宪章 VI：四表 BIGINT 自增物理主键、UUID 唯一业务标识、数据库维护时间、
  中文表/列注释均已验证。
- SC：Cron、幂等、120/121 月边界、10 组跨运行类型并发和五分钟诊断链均有实现与测试。

签署状态：代码与本地/真实 MySQL 验收完成；真实来源分页上线仍受第 6 节运维门禁约束。
