# 快速验证：每日股票列表同步

本指南用于实现完成后的端到端验证，不替代 `tasks.md` 或自动化测试。

## 1. 前置条件

- WSL2 Ubuntu、Docker Desktop 和 `uv` 可用。
- 已按 `README.md` 创建本机 `.env`。
- Tushare 账户有权调用 `stock_basic`，并具有有效 Token。
- 已阅读 [数据模型](data-model.md)、[Provider 契约](contracts/stock-list-provider.md)
  和 [Tushare 契约](contracts/tushare-stock-basic.md)。

本机 `.env` 增加：

```dotenv
STOCK_LIST_PROVIDER=tushare
STOCK_LIST_SCOPE=CN-S
STOCK_LIST_TIMEZONE=Asia/Shanghai
STOCK_LIST_LOG_DIR=logs
TUSHARE_TOKEN=replace-with-local-secret
TUSHARE_API_URL=https://api.tushare.pro
```

真实 Token 不得写入 `.env.example`、测试 fixture、命令输出或日志。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
docker compose ps
```

预期 MySQL 和 Prefect Server 健康，所有宿主端口仅绑定 `127.0.0.1`。
ClickHouse 和 Redis 虽由项目 Compose 启动，但股票列表应用代码不使用它们。

加载本机环境：

```bash
set -a
source .env
set +a

export DATABASE_URL="mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@127.0.0.1:${MYSQL_PORT}/${MYSQL_DATABASE}"
export PREFECT_API_URL="http://127.0.0.1:${PREFECT_PORT}/api"
```

## 3. 安装和迁移

```bash
uv sync --all-groups
uv run alembic upgrade head
```

验证表：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SHOW CREATE TABLE stock_current\\G
    SHOW CREATE TABLE stock_provider_mapping\\G
    SHOW CREATE TABLE stock_list_sync_run\\G
    SHOW CREATE TABLE stock_list_sync_issue\\G
  "
```

预期：

- `stock_current` 具有项目 UUID 及 venue + code 唯一约束。
- Provider 标识只在映射和同步来源中出现。
- `stock_list_sync_run.run_key` 唯一。
- 不存在股票列表快照、属性历史、行情或其他附加数据表。

## 4. 运行质量门禁

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run pytest -m mysql
uv run alembic upgrade head
```

预期单元、Provider 契约、Tushare 请求审计、MySQL、Flow 和日志测试全部通过。
迁移必须同时支持空库和已执行 `001` 的数据库。

## 5. 验证严格外部调用范围

运行 Tushare Adapter 契约测试：

```bash
uv run pytest tests/contract/test_tushare_stock_basic.py
```

测试必须捕获全部 HTTP 请求并证明：

- `api_name` 唯一值为 `stock_basic`。
- 恰好覆盖 `SSE/SZSE/BSE × L/D/P/G` 12 个唯一 segment。
- `CN-S` 固定包含三个交易所，配置中不存在排除任一交易所的选项。
- 每次请求只包含 `exchange/list_status` 参数。
- `fields` 精确等于 8 个白名单字段。
- 未调用交易日历、行情、成交、财务、指标、公司、指数或基金端点。
- 返回规范 DTO 不含行业、地域、公司详情或其他额外字段。

## 6. 部署每日计划

启动本机 Worker：

```bash
uv run prefect worker start --pool local-pool --type process
```

另一终端注册 Deployment：

```bash
uv run prefect deploy --name sync-stock-list/default --no-prompt
uv run prefect deployment schedule ls stock-list-sync/default
```

预期：

- `sync-stock-list/default` 只用于 Prefect 3.8 按入口函数
  `sync_stock_list` 选择 YAML 配置；创建出的 Deployment 名为
  `stock-list-sync/default`。
- Schedule slug 为 `daily-stock-list`。
- Cron 为 `0 9 * * *`。
- 时区为 `Asia/Shanghai`。
- 参数只有供应商无关的 `scope_code=CN-S` 和 schedule slug。
- Deployment 并发限制为 1，冲突策略为 `ENQUEUE`。

## 7. 人工运行

使用明确计划时点，便于后续验证相同 run_key：

```bash
uv run prefect deployment run 'stock-list-sync/default' \
  --param scope_code=CN-S \
  --param schedule_slug=manual-stock-list \
  --param scheduled_at=2026-07-27T09:00:00+08:00
```

预期：

- 仅调用 `stock_basic` 的 12 个 segment。
- 单个空 segment 可接受，聚合列表非空。
- 全批验证和单事务发布成功。
- Flow 返回 `SUCCEEDED` 及各类计数。
- 日志、异常和数据库不包含 Token 或原始供应商行。

## 8. 验证同步结果

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT run_id, schedule_slug, scheduled_for, business_date,
           provider_code, status, attempt_count,
           segment_count, completed_segment_count, capped_segment_count,
           received_count, valid_count, duplicate_count,
           invalid_count, conflict_count,
           added_count, updated_count, unchanged_count
    FROM stock_list_sync_run
    ORDER BY created_at DESC
    LIMIT 5;
  "
```

成功结果必须满足：

- `segment_count = completed_segment_count = 12`
- `capped_segment_count = 0`
- `valid_count > 0`
- `invalid_count = conflict_count = 0`
- `status = SUCCEEDED`
- `published_at` 非空

## 9. 验证当前股票列表

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT market_code, venue_code, listing_status, COUNT(*)
    FROM stock_current
    GROUP BY market_code, venue_code, listing_status
    ORDER BY market_code, venue_code, listing_status;

    SELECT stock_id, venue_code, security_code, display_name,
           currency_code, listing_status, listed_on, delisted_on
    FROM stock_current
    ORDER BY venue_code, security_code
    LIMIT 20;
  "
```

预期：

- venue 仅为 `XSHG/XSHE/XBSE`。
- 状态仅为 `ACTIVE/DELISTED/SUSPENDED/PENDING`。
- 币种为 `CNY`。
- 同一代码在不同 venue 可分别存在；同一 venue + code 不重复。
- 列表字段中没有 `ts_code`、行业、地域、行情、成交、财务或公司详情。

再通过 `StockListService.list_current` 验证按 venue、代码、名称和状态筛选，
确认返回值不包含 Provider 字段。该方法仅由项目内部已完成授权的调用方使用；
若从应用入口调用，入口必须先完成认证、授权和访问控制。

运行 10,000 条当前记录的查询性能验收：

```bash
uv run pytest tests/integration/test_stock_list_performance.py -k current_query
```

预期先完成一次预热，再连续执行 100 次覆盖无筛选、代码、venue、名称和状态的代表性查询，
至少 95 次在 1 秒内返回；计时不包含环境启动和验收数据准备。

## 10. 验证幂等和补跑

再次执行第 7 节完全相同的命令。

预期：

- `run_key` 不变且同步结果行数不增加。
- 已成功周期不再调用 Provider。
- `stock_current` 总数不增加，`stock_id` 保持不变。

使用测试 fixture 先制造失败，再显式补跑原计划周期：

```bash
uv run prefect deployment run 'stock-list-sync/default' \
  --param scope_code=CN-S \
  --param schedule_slug=manual-stock-list \
  --param scheduled_at=2026-07-28T09:00:00+08:00 \
  --param is_manual_retry=true
```

预期同一 `run_key` 的 `attempt_count` 增加；成功后状态变为 `SUCCEEDED`，
不会创建第二权威结果。

自动化验收还必须连续执行 30 次重复或补跑组合，确认始终只有一个权威结果且不产生重复股票。

## 11. 验证完整性保护

用契约或集成 fixture 依次模拟：

1. 一个 `P` 或 `G` segment 合法空集。
2. 12 个 segment 全部空。
3. 任一 segment 返回 5,999 行。
4. 任一 segment 返回恰好 6,000 行。
5. 一个 segment 缺失、超时或字段不匹配。
6. 上一成功列表中的一个 Provider 身份在本批完全消失。

预期：

- 场景 1 在其他 segment 非空且全部合法时可成功。
- 场景 2、4、5、6 整批失败。
- 场景 3 本身不因行数触发失败，但仍需通过其余全批校验。
- 所有失败场景中 `stock_current` 和 Provider 映射完全不变。
- 质量问题记录明确类别且不含原始行。

## 12. 验证字段、重复和冲突

分别模拟：

- 缺少代码、名称、交易所、币种或状态。
- 未知 `curr_type/list_status/exchange`。
- `ts_code` 后缀与交易所不一致。
- 非法日期或退市日期早于上市日期。
- 完全相同重复行。
- 同一 Provider ID 或 venue + code 对应不同字段。

预期：

- 无效字段和冲突使整批失败且当前列表不变。
- 完全相同重复行去重、`duplicate_count` 增加，在无其他问题时可成功。
- 不通过默认 CNY、最后一条覆盖或名称相似度静默修复。

## 13. 验证原子回滚

MySQL 集成测试在批量 upsert、Provider 映射和同步终结的各阶段注入异常：

```bash
uv run pytest -m mysql tests/integration/test_stock_list_repository.py
```

预期：

- 股票、映射和 `SUCCEEDED` 状态要么全部提交，要么全部回滚。
- 回滚后最近一次成功列表可继续查询。
- 失败结果与问题通过独立事务保存。
- 任何候选缺席的旧股票都不会被删除或自动改状态。

## 14. 验证容量与及时性

运行 10,000 条固定候选记录的容量测试：

```bash
uv run pytest tests/integration/test_stock_list_flow.py -k volume
```

预期：

- 全批内存校验和批量发布完成。
- 不引入 staging、ClickHouse 或 Redis。
- 报告获取、验证、事务和总耗时。
- 业务验收环境中从计划时点到终态不超过 30 分钟。
- 模拟最近 30 次 `daily-stock-list` 计划运行并计算及时率，人工运行不计入 SC-001。

终态日志包含：

```text
scheduled_at
started_at
completed_at
schedule_delay_ms
run_duration_ms
schedule_to_completion_ms
timeliness_met
```

## 15. 日志和 5 分钟排障

```bash
tail -n 100 logs/stock-list-sync.jsonl
rg '\"event\":\"stock_list_sync_(succeeded|failed)\"' logs/stock-list-sync.jsonl* | tail -n 20
```

运维人员应能在 5 分钟内确定：

- 是否完成全部 12 个 segment；
- 是否有 segment 触顶、字段无效、身份冲突或基线缺失；
- 当前列表是否发布；
- 是否需要对同一计划周期补跑。

确认日志中没有 Token、连接串、完整请求/响应或原始供应商行。

## 16. 验证 Provider 可替换

```bash
uv run pytest tests/contract/test_stock_list_provider.py
uv run pytest tests/unit/test_stock_list_service.py
```

预期：

- Memory Provider 与 Tushare Adapter 通过同一规范契约。
- Service 测试不导入 `integrations.tushare`。
- 替代 Provider 对固定 golden cases 产生相同 venue、代码、币种、状态和日期语义。
- 切换 Provider 不修改 Service、MySQL 核心模型或当前列表查询契约。

正式切换前先运行非发布影子对账并解决任何身份冲突。

## 17. 安全停止

暂停计划：

```bash
uv run prefect deployment schedule ls stock-list-sync/default
uv run prefect deployment schedule pause stock-list-sync/default <schedule-id>
```

停止 Worker 可阻止新运行。不要直接删除同步结果、当前股票或 Provider 映射。
中断的运行应由显式补跑复用原 `run_key` 恢复。

恢复计划：

```bash
uv run prefect deployment schedule resume stock-list-sync/default <schedule-id>
```
