# 快速验证：每月券商金股同步

本指南用于实现完成后的端到端验证，不替代 `tasks.md` 或自动化测试。

## 1. 前置条件

- WSL2 Ubuntu、Docker Desktop 和 `uv` 可用。
- 已按 `README.md` 创建本机 `.env`。
- 现有股票列表迁移与同步已完成，`stock_current` 和 `stock_provider_mapping`
  能解析金股 fixture 中的全部股票。
- Tushare 部署账户有权调用 `broker_recommend`，并具有有效 Token。
- 已阅读 [数据模型](data-model.md)、[Provider 契约](contracts/broker-recommendation-provider.md)、
  [Service 契约](contracts/broker-recommendation-service.md)、
  [Tushare 契约](contracts/tushare-broker-recommend.md) 和
  [Flow 契约](contracts/prefect-flow.md)。

本机 `.env` 增加：

```dotenv
BROKER_RECOMMENDATION_PROVIDER=tushare
BROKER_RECOMMENDATION_TIMEZONE=Asia/Shanghai
BROKER_RECOMMENDATION_LOG_DIR=logs
BROKER_RECOMMENDATION_LOG_FILENAME=broker-recommendation-sync.jsonl
BROKER_RECOMMENDATION_FETCH_DEADLINE_SECONDS=1500
BROKER_RECOMMENDATION_RUN_LEASE_SECONDS=2100
BROKER_RECOMMENDATION_TIMELINESS_TARGET_MS=1800000
BROKER_RECOMMENDATION_PAGE_LIMIT=1000
BROKER_RECOMMENDATION_MAX_PAGES=100
BROKER_RECOMMENDATION_TUSHARE_PAGINATION_ENABLED=false
TUSHARE_TOKEN=replace-with-local-secret
TUSHARE_API_URL=https://api.tushare.pro
```

真实 Token 不得写入 `.env.example`、测试 fixture、命令输出、日志或数据库。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
docker compose ps
```

预期 MySQL 和 Prefect Server 健康，所有宿主端口仅绑定 `127.0.0.1`。
ClickHouse 和 Redis 虽由 Compose 启动，但金股应用代码不使用它们。

加载本机环境：

```bash
set -a
source .env
set +a

export DATABASE_URL="mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@127.0.0.1:${MYSQL_PORT}/${MYSQL_DATABASE}"
export PREFECT_API_URL="http://127.0.0.1:${PREFECT_PORT}/api"
```

## 3. 安装、迁移和股票身份前置检查

```bash
uv sync --all-groups
uv run alembic upgrade head
```

迁移测试必须覆盖：

1. 空数据库从 base 升级到 head。
2. 已处于 revision `002` 的数据库升级到 `003`。

验证表和约束：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SHOW CREATE TABLE broker_recommendation\\G
    SHOW CREATE TABLE broker_recommendation_sync_run\\G
    SHOW CREATE TABLE broker_recommendation_sync_attempt\\G
    SHOW CREATE TABLE broker_recommendation_sync_issue\\G
  "
```

预期：

- 四表均以 `id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID'` 为物理主键。
- `recommendation_id`、`run_id`、`attempt_id`、`issue_id` 均为 UUID 业务标识并具有 `UNIQUE`。
- 四表均包含数据库维护的 `created_at` 和 `updated_at`；
  `updated_at` 具有 `ON UPDATE CURRENT_TIMESTAMP`。
- attempt 表包含非空 `lease_expires_at`，认领时使用数据库 UTC 设置为当前时间加 35 分钟。
- 四表分别具有“券商月度金股推荐”“券商金股同步运行”“券商金股同步执行尝试”
  和“券商金股同步质量问题”中文表注释，每个字段均有非空中文注释。
- 推荐唯一键为月份、区分字符的规范券商名称和 `stock_id`。
- `stock_id` 引用 `stock_current`。
- `run_key`、`flow_run_id`、`run_id + attempt_no` 均有唯一约束；
  run 的计划字段和补跑批次字段按 `run_kind` 互斥。
- run、attempt 和 issue 的外键完整。
- 不存在推荐快照、行情、财务、预测或 Provider 原始数据表。

检查股票身份已有数据：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT venue_code, COUNT(*) AS stock_count
    FROM stock_current
    GROUP BY venue_code
    ORDER BY venue_code;
  "
```

预期包含测试使用的 `XSHG/XSHE/XBSE` 股票。金股同步不得为缺失股票创建主数据。

## 4. 运行质量门禁

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run pytest -m mysql
uv run alembic upgrade head
```

预期单元、Provider 契约、Tushare 请求审计、SQLite/MySQL Repository、Flow、
容量和日志安全测试全部通过。

## 5. 验证 Provider 与严格调用范围

运行契约测试：

```bash
uv run pytest tests/contract/test_broker_recommendation_provider.py
uv run pytest tests/contract/test_tushare_broker_recommend.py
```

测试必须捕获全部 HTTP 请求并证明：

- `api_name` 唯一值为 `broker_recommend`。
- 分页关闭时参数只有目标 `month=YYYYMM`；启用时参数精确为
  `month/limit/offset`，offset 按 `0,1000,2000...` 前进。
- 字段精确为 `month,broker,ts_code,name`。
- 未调用行情、股票列表、财务、预测或其他端点。
- `.SH/.SZ/.BJ` 只在 Adapter 中映射为 `XSHG/XSHE/XBSE`。
- Token、完整请求/响应和原始行不进入 DTO、异常或日志。

覆盖 fixture：

| Fixture | 预期 |
|---------|------|
| 0 行 | `EMPTY_AGGREGATE`，失败 |
| 1 行 | 成功 |
| 999 行 | 成功 |
| 分页关闭的 1,000 行 | `RESPONSE_CAPPED`，失败 |
| 分页启用的 1,000/1,000/500 行 | 取得 2,500 条后成功 |
| 分页启用的 1,000/0 行 | 取得 1,000 条后成功 |
| 重复满页或 offset 未前进 | `CONTINUATION_INCOMPLETE`，失败 |
| 月份错配 | `MONTH_MISMATCH`，失败 |
| 未知后缀 | `INVALID_FIELD`，失败 |

Memory Provider 的独立契约必须能提供 2,500 条且成功，以证明系统容量不受单页上限限制。

## 6. 验证重试边界

```bash
uv run pytest tests/contract/test_tushare_broker_recommend.py -k retry
```

预期：

- 网络超时、HTTP 429 和 5xx：整个月份跨页共享最多 3 次额外重试；
  单页场景总调用最多 4 次，进入下一页不会重置预算。
- 测试注入 sleep，验证退避序列 30/120/300 秒而不实际等待。
- deadline 不足时提前失败，不超过 25 分钟预算。
- 认证、权限、额度、参数、payload、空结果和触顶：总调用 1 次。
- Flow 与 Service 不叠加重试。

## 7. 部署每月计划

启动本机 Worker：

```bash
uv run prefect worker start --pool local-pool --type process
```

另一终端注册 Deployment：

```bash
uv run prefect deploy --name sync-broker-recommendations/default --no-prompt
uv run prefect deployment schedule ls broker-recommendation-sync/券商金股同步
```

预期：

- 创建的 Deployment 为 `broker-recommendation-sync/券商金股同步`。
- Schedule slug 为 `monthly-broker-recommendations`。
- Cron 为 `0 12 3,4 * *`。
- 时区为 `Asia/Shanghai`。
- 并发限制为 1，冲突策略为 `ENQUEUE`。
- 周末、节假日和非交易日仍生成计划运行。

## 8. 人工验证 3 日计划周期

使用明确原计划时点：

```bash
uv run prefect deployment run 'broker-recommendation-sync/券商金股同步' \
  --param schedule_slug=monthly-broker-recommendations \
  --param scheduled_at=2026-08-03T12:00:00+08:00
```

预期：

- 目标月份为 `2026-08-01`。
- 只调用 `broker_recommend(month=202608)`。
- 全批验证和单事务发布成功。
- 推荐表按月份、券商、`stock_id` 唯一。
- run 与 attempt 各新增一行，attempt 计数完整。
- 日志、异常和数据库无 Token、`ts_code` 或原始供应商行。

查询最近结果：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT r.run_id, r.run_kind, r.schedule_slug, r.scheduled_for,
           r.backfill_batch_id, r.target_month,
           r.status, r.attempt_count, r.published_at,
           a.attempt_id, a.attempt_no, a.provider_code, a.status,
           a.provider_request_count, a.provider_retry_count,
           a.received_count, a.valid_count, a.added_count,
           a.updated_count, a.unchanged_count, a.duplicate_count,
           a.invalid_count, a.conflict_count
    FROM broker_recommendation_sync_run r
    JOIN broker_recommendation_sync_attempt a ON a.run_id = r.run_id
    ORDER BY r.created_at DESC, a.attempt_no DESC
    LIMIT 10;
  "
```

成功 attempt 必须 `valid_count > 0`、`conflict_count = 0`，run 与 attempt 均为
`SUCCEEDED`。`invalid_count` 可以大于 0，但每条都必须对应一个脱敏
`UNKNOWN_STOCK_IDENTITY` issue，且不会写入推荐表。

## 9. 验证 3 日→4 日追加更新且不删除

使用固定 fixture：

- 3 日：推荐 A、B、C。
- 4 日：缺少 A，B 的股票简称改变，C 不变，新增 D。

先运行 3 日，再运行 4 日：

```bash
uv run prefect deployment run 'broker-recommendation-sync/券商金股同步' \
  --param schedule_slug=monthly-broker-recommendations \
  --param scheduled_at=2026-08-04T12:00:00+08:00
```

预期：

- 两个独立 run，目标月份均为 `2026-08-01`。
- A 仍存在，且 `last_confirmed_run_id` 保持 3 日周期。
- B 仍只有一行，简称更新，`first_seen_at` 不变，`last_confirmed_at` 刷新。
- C 仍只有一行，只刷新最近确认时间。
- D 新增。
- 4 日 absence 不产生 `BASELINE_MISSING`，不执行 delete 或 soft delete。

检查业务表：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT recommendation_month, broker_name, stock_id,
           venue_code, security_code, stock_name,
           first_seen_at, last_confirmed_at
    FROM broker_recommendation
    WHERE recommendation_month = '2026-08-01'
    ORDER BY broker_name, venue_code, security_code;
  "
```

## 10. 验证券商名称和股票身份

自动化 fixture 至少覆盖：

```text
"  中信证券  "       → "中信证券"
"中信　 证券"        → "中信 证券"
"ABC证券" 与 "abc证券" → 保持不同
```

预期：

- 只规范首尾和连续 Unicode 空白。
- 不做大小写、全半角、标点或别名映射。
- MySQL `utf8mb4_bin` 唯一语义与 Service 一致。
- 同券商同股重复不新增；同股票由不同券商推荐分别保存。
- Provider 映射与 venue + code 指向不同 `stock_id` 时整批失败。
- 单条未知股票记录 issue 后跳过，同月其他有效推荐继续发布，`stock_current` 不新增；
  全部记录均无法解析时整月失败。

真实 MySQL 门禁：

```bash
uv run pytest -m mysql tests/integration/test_broker_recommendation_mysql.py
```

## 11. 验证幂等、并发、历史补跑和重试审计

再次执行第 8 节完全相同的命令。

预期：

- `run_key` 不变，run 行数不增加。
- 成功运行不创建新 attempt，也不调用 Provider。
- 推荐总数和业务摘要不变。
- 切换 Provider、修改非身份配置或改变 `scope_fingerprint` 后，
  相同计划周期或相同 `backfill_batch_id + target_month` 的 `run_key` 仍不变。

自动化执行：

```bash
uv run pytest tests/integration/test_broker_recommendation_repository.py -k idempotent
uv run pytest -m mysql tests/integration/test_broker_recommendation_mysql.py -k concurrent
```

预期连续 30 次重复和 10 组并发首次认领始终只有一个权威 run，
不会产生重复推荐。

执行项目初始化的 24 月历史补跑：

```bash
uv run prefect deployment run 'broker-recommendation-backfill/券商金股历史回补' \
  --param start_month=2024-01-01 \
  --param end_month=2025-12-01 \
  --param backfill_batch_id=initial-load-2026-07
```

预期：

- 24 个目标月份各自形成一个 `BACKFILL` run。
- 一个历史月份失败不回滚其他成功月份。
- 重复执行相同命令时跳过成功月份；失败或过期月份解析到原 `run_id`
  并转换为 Retry，新 attempt 追加在原 run；未开始月份才创建首次 BACKFILL run。
- 使用新 `backfill_batch_id` 和单月范围可以主动刷新已成功月份，
  但推荐业务唯一键仍不重复。

使用 fixture 制造失败后，记录失败 `run_id` 并显式重试：

```bash
uv run prefect deployment run 'broker-recommendation-retry/券商金股同步重试' \
  --param run_id=replace-with-failed-run-id
```

预期同一个 run 的 `attempt_count` 增加，旧失败 attempt 保持不可变，
新 attempt 可成功；run 最终只有一个 `successful_attempt_id`。

使用数据库 UTC fixture 验证租约边界：

- attempt 认领时 `lease_expires_at` 为数据库当前 UTC 时间加 35 分钟。
- 到期前重复补跑返回 `IN_PROGRESS`，不创建第二 attempt。
- 到期后重放在同一事务将旧 attempt 标记为 `ABANDONED`、记录 issue，
  并对原 `run_id` 创建 Retry attempt。
- Worker 本地时钟偏移不得改变上述结果；首版没有心跳或续租。

验证补跑范围边界：

```bash
# 恰好 120 个月：2016-01 至 2025-12
uv run prefect deployment run 'broker-recommendation-backfill/券商金股历史回补' \
  --param start_month=2016-01-01 \
  --param end_month=2025-12-01 \
  --param backfill_batch_id=boundary-120-months

# 121 个月：2015-12 至 2025-12
uv run prefect deployment run 'broker-recommendation-backfill/券商金股历史回补' \
  --param start_month=2015-12-01 \
  --param end_month=2025-12-01 \
  --param backfill_batch_id=boundary-121-months
```

预期第一项按月隔离执行；第二项在创建任何 run 前整体拒绝，
数据库中不存在 `boundary-121-months` 对应运行。

最后执行 10 组计划运行与补跑运行同月并发验收，预期两个 run 分别可追踪，
相同 `recommendation_month + broker_name + stock_id` 无重复记录。
股票代码必须保持稳定；不比较股票简称等其他属性的跨 run 最终版本。

## 12. 验证失败保护

依次模拟：

1. 空响应。
2. 分页未验证时恰好 1,000 行触顶。
3. 分页重复整页、offset 未前进、超过最大页数或中途失败。
4. 月份错配。
5. 必需字段缺失。
6. 未知股票身份。
7. Provider 映射和规范键冲突。
8. 同一业务键不同简称冲突。
9. MySQL 发布中途失败。

每次失败前后计算推荐表规范摘要和总数。

预期：

- attempt 和 run 形成 `FAILED` 终态。
- attempt 保存 received/valid/invalid/conflict 等完整计数。
- issue 提供可操作类别，但不含原始 payload。
- 推荐表摘要与总数不变。
- 3 日或历史月份数据不删除。
- 可按原 `run_id` 安全重试。

## 13. 验证 2,500 条分页容量与 30 分钟及时性

```bash
uv run pytest tests/integration/test_broker_recommendation_capacity.py
```

预期：

- Memory Provider 的 2,500 条完整推荐全部处理。
- 分页 fixture 依次返回 1,000、1,000、500 行，系统只在第三页后结束。
- 有效推荐 100% 保存；未保存输入均有明确原因。
- 从原计划时点到终态不超过 30 分钟。
- 分页关闭时的 Tushare 1,000 行 fixture 仍因触顶失败，
  证明系统容量没有削弱来源完整性门禁。

及时率统计只计算自动 3 日/4 日计划，排除历史补跑和失败重试；连续 12 个月目标至少 99%。

## 14. 验证内部查询和 Provider 替换

通过 `BrokerRecommendationService.list_month` 验证：

- 目标月份必填。
- 可按规范券商、`stock_id`、venue 和代码筛选。
- 排序稳定，limit/offset 校验正确。
- 返回不含 `ts_code`、Provider code、运行错误或范围外数据。
- 调用入口在 Service 前完成认证授权。

替代 Provider 验证：

```bash
uv run pytest tests/contract/test_broker_recommendation_provider.py -k golden
```

预期 Memory/替代 Provider 对相同 golden fixture 产生相同规范摘要；
切换只改 Provider 配置，不修改 Service、Repository、业务表唯一键或消费者。

## 15. 上线前真实来源门禁

在不打印响应和 Token 的受控环境执行一次目标月份探测，确认：

- 部署账户具备 `broker_recommend` 权限。
- 调用频率覆盖正常调用和最多 3 次瞬态重试。
- `limit/offset` 被端点接受，页面上限确实生效。
- offset 前进后不会重复首页，满页之后能够取得不同后续页或可靠空终止页。
- 对受控月份重复探测时，聚合数量和规范摘要稳定。

将不含原始响应的验证证据记录到 `verification.md` 后，才可设置
`BROKER_RECOMMENDATION_TUSHARE_PAGINATION_ENABLED=true`。
若验证失败，必须保持分页关闭；结果恰好 1,000 行时停止上线或切换能证明完整性的替代 Provider。
不得调高 page limit 或关闭触顶校验。

## 16. 五分钟排障

```bash
tail -n 100 logs/broker-recommendation-sync.jsonl
rg '"event":"broker_recommendation_sync_(succeeded|failed)"' \
  logs/broker-recommendation-sync.jsonl* | tail -n 30
uv run prefect deployment schedule ls broker-recommendation-sync/券商金股同步
```

运维人员应在 5 分钟内回答：

1. 失败的是 3 日/4 日计划、历史补跑还是重试，目标月份是什么？
2. run/attempt/Flow Run ID 是什么？
3. Provider 请求和重试多少次？
4. 接收、有效、新增、更新、重复、无效和冲突各多少？
5. 错误属于瞬态、权限、触顶、字段、身份、冲突还是持久化？
6. 是否应按原 `run_id` 重试，或以哪个 `backfill_batch_id` 补齐历史月份？

任何排障输出都不得包含 Token、连接串、完整请求/响应或原始供应商行。
