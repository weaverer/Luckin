# Lucking

## 项目治理

项目规格、计划、任务和实现必须遵循
[项目宪章](.specify/memory/constitution.md)。面向项目成员的文档默认使用简体中文；
代码标识符、命令、协议字段和第三方专有名词可以保留英文。

所有第三方数据 API 必须通过项目自有的供应商无关接口和独立适配器接入；业务代码不得
直接依赖供应商 SDK、传输模型或专有字段，并必须通过契约测试验证数据源可替换性。

项目拥有的新建或发生结构性变更的 MySQL 业务表默认使用 `BIGINT AUTO_INCREMENT`
主键、数据库维护的 `created_at/updated_at`，并为表和全部字段提供准确的中文注释。
复合身份、分区或框架内部表等特殊场景必须在功能计划和数据模型中逐表记录并通过宪章
检查，不得以口头约定跳过。

## Windows / WSL2 开发基础设施

应用进程在 WSL2 Ubuntu 本机运行，Docker Compose 只负责 MySQL、
ClickHouse、Redis 和 Prefect Server。所有端口都只绑定到
`127.0.0.1`，不会直接暴露到局域网。

### 首次启动

1. 启动 Docker Desktop，并在 **Settings → Resources → WSL Integration**
   中启用当前 Ubuntu 发行版。
2. 从示例创建本机配置；仓库已忽略包含真实密码的 `.env`：

   ```bash
   cp .env.example .env
   ```

3. 替换 `.env` 中所有 `change-me`，然后构建并启动：

   ```bash
   docker compose pull
   docker compose up -d --build --wait
   docker compose ps
   ```

当前开发环境已生成一份随机密码的本机 `.env`，无需再次复制示例文件。
如果标准端口已被其他本机服务占用，只需修改 `.env` 中对应的
`*_PORT` 宿主端口；容器内部端口无需更改。

### 本机连接

在需要启动 FastAPI 或 Prefect Worker 的终端中加载环境变量：

```bash
set -a
source .env
set +a

export DATABASE_URL="mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@127.0.0.1:${MYSQL_PORT}/${MYSQL_DATABASE}"
export CLICKHOUSE_HTTP_URL="http://${CLICKHOUSE_USER}:${CLICKHOUSE_PASSWORD}@127.0.0.1:${CLICKHOUSE_HTTP_PORT}/${CLICKHOUSE_DATABASE}"
export CLICKHOUSE_NATIVE_HOST="127.0.0.1"
export CLICKHOUSE_NATIVE_PORT
export REDIS_URL="redis://:${REDIS_PASSWORD}@127.0.0.1:${REDIS_PORT}/0"
export PREFECT_API_URL="http://127.0.0.1:${PREFECT_PORT}/api"
```

Prefect UI 位于 <http://127.0.0.1:4200>。本机 Process Worker 可用以下命令启动：

```bash
uv run prefect worker start --pool local-pool --type process
```

### 日常命令

```bash
# 查看状态和日志
docker compose ps
docker compose logs -f

# 停止容器但保留全部数据
docker compose down

# 删除容器及所有数据库卷（不可恢复）
docker compose down -v
```

持久化数据使用 `mysql_data`、`clickhouse_data`、`clickhouse_logs`、
`redis_data` 和 `prefect_data` 五个 Docker named volumes。Prefect Server
使用 SQLite，数据库文件位于 `prefect_data` 卷中的
`/root/.prefect/prefect.db`；Redis DB 0 供应用使用，DB 1 供 Prefect
消息系统使用。

## 交易日历同步

交易日历使用 MySQL 当前值表，首期通过可替换的 Provider 接口接入 Tushare，并以
Tushare `SSE` 日历表示 `CN-S`。代码不会调用或比对深交所、北交所日历。数据库中
保留 `created_at`、最近成功同步的 `updated_at` 和 `sync_mode`，不建立同步历史表。

### 配置与迁移

在本机 `.env` 配置以下字段，真实 Token 不得提交或写入日志：

```dotenv
DATABASE_URL=mysql+pymysql://lucking:本机密码@127.0.0.1:3306/lucking
TRADING_CALENDAR_PROVIDER=tushare
TUSHARE_TOKEN=本机秘密
TUSHARE_API_URL=https://api.tushare.pro
TRADING_CALENDAR_LOG_DIR=logs
TRADING_CALENDAR_TIMEZONE=Asia/Shanghai
```

安装依赖并执行迁移：

```bash
uv sync --all-groups
uv run alembic upgrade head
```

### Worker 与 Deployment

`prefect.yaml` 定义一个 `trading-calendar-sync/default` Deployment：

- `monthly-current-year`：每月 1 日 02:00，同步当月首日至当年末。
- `year-end-next-year`：每年 12 月 20 日 02:30，同步下一自然年。

两者均使用 `Asia/Shanghai`，并发限制为 1、冲突策略为 `ENQUEUE`。

```bash
uv run prefect worker start --pool local-pool --type process
uv run prefect --no-prompt deploy --name trading-calendar-sync/default
```

人工补数示例：

```bash
uv run prefect deployment run 'trading-calendar-sync/default' \
  --param mode=manual \
  --param market_code=CN-S \
  --param start_date=2026-01-01 \
  --param end_date=2026-12-31
```

人工范围必须显式给出起止日期、不得反向且最长十年。无效市场在外部调用前拒绝。

### 完整性、失败与额度处理

- 历史或当日缺口、未来内部断点、空批次、重复/越界/非法记录均整批拒绝，不写部分数据。
- 只有尚未公布的连续未来尾部可以降级为 `FUTURE_PARTIAL`；已验证前缀写入，
  尾部不合成休市记录。数据库中的既有当前值不会因来源缺失而删除。
- 网络、HTTP 429、短时频率限制和 5xx 最多重试 3 次，退避为 30/120/300 秒。
- Token/权限、积分/额度/当日配额、配置、载荷和数据库错误不重试。
- 遇到异常时先查看 `logs/trading-calendar-sync.jsonl`。停止 Worker 可阻止新任务；
  已开始的数据库批次由单事务保证提交或整体回滚。

### 日志、最近状态与及时性

日志采用 10 MiB、5 个归档文件的 JSONL，关联 Prefect Flow Run ID，并使用字段白名单
和秘密脱敏。查看最近状态及最近成功时间：

```bash
tail -n 100 logs/trading-calendar-sync.jsonl
rg '"event":"sync_(succeeded|failed)"' logs/trading-calendar-sync.jsonl* | tail -n 20
rg '"event":"sync_succeeded"' logs/trading-calendar-sync.jsonl* | tail -n 1
```

计划终态日志包含 `schedule_delay_ms`、`run_duration_ms`、
`schedule_to_completion_ms` 和 `timeliness_met`。每个 `schedule_slug` 使用最近
20 次计划终态计算达标率；不足 20 次标为暂定。人工运行不参与计划及时性。

### 更换数据源

新增 Provider 时，实现 `TradingCalendarProvider`、通过一致性契约测试、在
`src/lucking/integrations/registry.py` 注册工厂并配置自身秘密即可。Flow、Service、
Repository、市场代码和数据库结构不需要修改，也不会自动回退或合并多个来源。

### 质量与性能验证

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run python scripts/benchmark_trading_calendar.py
```

性能脚本使用 2080–2089 年隔离数据，结束时仅清理 `source=benchmark` 的对应范围。

## 股票列表同步

股票列表每天北京时间 09:00 执行，周末和休市日也照常运行。首期范围固定为
`CN-S`，完整覆盖上海、深圳、北京三个交易场所；没有 venue 子集开关。真实来源只调用
Tushare `stock_basic`，按 `SSE/SZSE/BSE × L/D/P/G` 获取 12 个分区，每次只请求
`ts_code,symbol,name,exchange,curr_type,list_status,list_date,delist_date`。
本功能不获取行情、成交、财务、公司、交易日历或其他证券数据。

### 配置、迁移与部署

在本机 `.env` 中配置以下字段。`TUSHARE_TOKEN` 只能保存在本机秘密配置中，不得提交、
打印或写入日志：

```dotenv
DATABASE_URL=mysql+pymysql://lucking:本机密码@127.0.0.1:13306/lucking
STOCK_LIST_PROVIDER=tushare
STOCK_LIST_SCOPE=CN-S
STOCK_LIST_TIMEZONE=Asia/Shanghai
STOCK_LIST_LOG_DIR=logs
STOCK_LIST_LOG_FILENAME=stock-list-sync.jsonl
STOCK_LIST_FETCH_DEADLINE_SECONDS=1500
STOCK_LIST_TIMELINESS_TARGET_MS=1800000
STOCK_LIST_SEGMENT_ROW_CAP=6000
TUSHARE_TOKEN=本机秘密
TUSHARE_API_URL=https://api.tushare.pro
```

安装、迁移并部署：

```bash
uv sync --all-groups
uv run alembic upgrade head
uv run prefect worker start --pool local-pool --type process
uv run prefect deploy --name sync-stock-list/default --no-prompt
```

Deployment `stock-list-sync/default` 的计划 slug 为 `daily-stock-list`，Cron 为
`0 9 * * *`，时区为 `Asia/Shanghai`，并发限制为 1，冲突运行进入队列。
Prefect 3.8 部署命令中的 `sync-stock-list` 是入口函数 `sync_stock_list` 的配置选择器；
部署完成后的 Flow/Deployment 全限定名称仍为 `stock-list-sync/default`。

### 人工运行与失败补跑

人工触发时使用明确的计划时点：

```bash
uv run prefect deployment run 'stock-list-sync/default' \
  --param scope_code=CN-S \
  --param schedule_slug=manual-stock-list \
  --param scheduled_at=2026-07-27T09:00:00+08:00
```

失败后补跑同一周期时保持 `scope_code`、`schedule_slug` 和 `scheduled_at` 不变，并增加：

```text
--param is_manual_retry=true
```

同一成功周期会直接返回已有权威结果，不再次调用 Provider；失败补跑复用同一
`run_key` 并增加 `attempt_count`。空聚合、分区触及 6,000 行上限、字段非法、身份冲突、
上一成功 Provider 身份缺失或数据库异常都会整批失败，当前列表和映射保持不变。

### 查询、安全与数据边界

`StockListService.list_current` 只返回项目股票 ID、市场、venue、证券代码、名称、币种、
上市状态及上市/退市日期，支持 venue、状态、代码、名称筛选和稳定分页。它是内部服务
入口，不是公共 API；调用它的应用入口负责先完成认证、授权、访问控制和审计。

日志仅记录白名单计数、状态、安全摘要和哈希标识，不记录 Token、原始供应商行或禁止字段。
Provider 专有标识只保存在映射表，不出现在当前列表查询结果中。

### 日志、及时性和五分钟排障

股票列表日志位于 `logs/stock-list-sync.jsonl`，按 10 MiB 轮转并保留 5 个归档。计划终态
包含 `schedule_delay_ms`、`run_duration_ms`、`schedule_to_completion_ms` 和
`timeliness_met`；单次目标为 30 分钟，最近 30 次计划运行用于计算及时率，人工运行不计入。

```bash
tail -n 100 logs/stock-list-sync.jsonl
rg '"event":"stock_list_sync_(succeeded|failed)"' logs/stock-list-sync.jsonl* | tail -n 30
docker compose ps
uv run prefect deployment schedule ls stock-list-sync/default
```

五分钟排障顺序：

1. 检查 MySQL、Prefect Server 和 Worker 是否在线，以及 Deployment 计划是否启用。
2. 用 Flow Run ID 关联 Prefect 状态、JSONL 终态和 `stock_list_sync_run`。
3. 查看 `error_category`、安全摘要和对应质量问题；不要复制 Token 或原始响应。
4. 对网络、429 或 5xx，确认当前失败分区已按 30/120/300 秒退避；认证、额度、载荷、
   完整性和数据库错误不会自动整批重试。
5. 修复原因后对原计划周期显式人工补跑；不要改计划时点来绕过失败记录。

需要安全停止时先暂停 Deployment 计划或停止 Worker，阻止新运行进入；已开始的发布由单事务
保证整体提交或回滚。不要用 `docker compose down -v`，除非明确接受不可恢复地删除全部卷。

### 股票列表质量门禁

```bash
uv run ruff check .
uv run mypy src
uv run pytest
uv run pytest tests/integration/test_stock_list_performance.py
uv run pytest tests/contract/test_stock_list_scope.py
```

更换数据源时只新增 `StockListProvider` Adapter、通过统一契约测试并在 Registry 中显式注册；
Service、Repository、Flow、固定 `CN-S` 范围和领域模型不依赖 Tushare。

## 券商金股同步

`broker-recommendation-sync/default` 在 `Asia/Shanghai` 每月 3、4 日 12:00
运行，只调用 Tushare `broker_recommend` 的 `month,broker,ts_code,name` 四字段。
4 日缺席的推荐不会删除；可信行按
`recommendation_month + broker_name + stock_id` 幂等新增、更新或确认。

配置见 `.env.example`。运行租约固定为数据库 UTC 的 35 分钟，Provider 截止时间为
25 分钟且 Flow 不自动重试。`limit/offset` 分页默认关闭；只有部署账户或沙箱确认页面
前进、满页续取和短页终止后，才能启用
`BROKER_RECOMMENDATION_TUSHARE_PAGINATION_ENABLED=true`。关闭时单页达到 1,000
会安全失败，避免把截断数据当完整月份发布。

历史补跑使用无 Cron 的 `broker-recommendation-backfill/manual`，传入月首
`start_month`、`end_month` 和稳定 `backfill_batch_id`。闭区间最多 120 个月；
121 个月、未来月份、反向范围会在创建任何 run 前整体拒绝。同批次成功月跳过，
失败月或数据库 UTC 判断已过期的运行按原 `run_id` 转为 Retry；有效租约返回
`IN_PROGRESS`。新批次键可主动刷新同月。计划与补跑并发时分别保留运行审计，
业务唯一键仍只产生一条推荐；股票简称允许按事务提交顺序落值。

日志写入 `logs/broker-recommendation-sync.jsonl`，只含白名单业务 UUID、状态、分页
证据和计数，不含 Token、连接串、原始响应或物理 BIGINT 主键。五分钟排障先关联
Prefect Flow Run、`run_id`、`attempt_id`，再查看安全 `error_category` 和 issue；
修复后调用 `broker-recommendation-retry/manual` 并传原 `run_id`。安全停止时暂停
Deployment 或停止 Worker，不要删除数据库卷。

单条推荐无法解析到 `stock_current` 时会写入脱敏 `UNKNOWN_STOCK_IDENTITY` issue、
增加 `invalid_count` 并跳过该条，不影响同月其他有效推荐；如果整月没有任何可解析记录，
运行仍会失败。
