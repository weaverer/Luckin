# 快速验证：交易日历同步

本指南用于在实现完成后验证功能，不替代实施任务。

## 1. 前置条件

- WSL2 Ubuntu 与 Docker Desktop 可用。
- 已按项目 `README.md` 创建本地 `.env`。
- 当 `TRADING_CALENDAR_PROVIDER=tushare` 时，Tushare 账户可以调用 `trade_cal`，
  并具有有效 Token。
- `uv` 已安装。

在 `.env` 增加：

```dotenv
TRADING_CALENDAR_PROVIDER=tushare
TUSHARE_TOKEN=replace-with-local-secret
TUSHARE_API_URL=https://api.tushare.pro
TRADING_CALENDAR_LOG_DIR=logs
TRADING_CALENDAR_TIMEZONE=Asia/Shanghai
```

真实 Token 不得写入 `.env.example` 或任何日志。未来选择其他 Provider 时，只要求
该 Provider 自身的秘密配置；领域服务和 Flow 参数不增加供应商专属字段。

## 2. 启动依赖

```bash
docker compose up -d --build --wait
docker compose ps
```

预期：MySQL、Redis 和 Prefect Server 健康；ClickHouse 虽由现有 Compose 启动，
但本功能不使用它。

加载本机环境变量：

```bash
set -a
source .env
set +a

export DATABASE_URL="mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@127.0.0.1:${MYSQL_PORT}/${MYSQL_DATABASE}"
export PREFECT_API_URL="http://127.0.0.1:${PREFECT_PORT}/api"
```

## 3. 安装与迁移

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
  -e "SHOW CREATE TABLE trading_calendar\\G"
```

预期：

- 联合主键为 `market_code, calendar_date`。
- 包含 `created_at`、`sync_mode` 和 `updated_at`。
- 没有同步执行记录表或历史版本表。

## 4. 运行质量门禁

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

预期：单元、Tushare 契约、MySQL 集成和 Prefect Flow 测试全部通过。

## 5. 部署工作流

启动本机 Process Worker：

```bash
uv run prefect worker start --pool local-pool --type process
```

在另一个终端注册 Deployment：

```bash
uv run prefect --no-prompt deploy --name trading-calendar-sync/交易日历同步
uv run prefect deployment schedule ls trading-calendar-sync/交易日历同步
```

预期存在两个启用的计划：

- `monthly-current-year`：每月 1 日 02:00，`Asia/Shanghai`
- `year-end-next-year`：每年 12 月 20 日 02:30，`Asia/Shanghai`

## 6. 人工补数

```bash
uv run prefect deployment run \
  'trading-calendar-sync/交易日历同步' \
  --param mode=manual \
  --param market_code=CN-S \
  --param start_date=2026-01-01 \
  --param end_date=2026-12-31
```

在 Prefect UI 或 CLI 等待 Flow 完成。预期：

- Registry 选择 `tushare`，其 Adapter 只调用 Tushare `SSE`。
- 保存 `CN-S` 的开市和休市日期。
- Flow 成功，日志包含接收/写入行数、`coverage_end` 和
  `COMPLETE/FUTURE_PARTIAL`，但不包含 Token。

## 7. 验证数据

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT market_code, MIN(calendar_date), MAX(calendar_date), COUNT(*)
    FROM trading_calendar
    WHERE market_code = 'CN-S'
    GROUP BY market_code;
  "
```

预期：来源已公布完整自然年时，日期覆盖完整自然年，记录数为 365 或 366；若来源只公布
连续未来前缀，最大日期等于日志中的 `coverage_end`，之后无记录的日期查询为 `UNKNOWN`。

检查最近写入模式：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT sync_mode, COUNT(*)
    FROM trading_calendar
    WHERE market_code = 'CN-S'
    GROUP BY sync_mode;
  "
```

预期：人工补数覆盖的记录其 `sync_mode` 为 `manual`。

检查 Provider 来源：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT source, source_market, COUNT(*)
    FROM trading_calendar
    WHERE market_code = 'CN-S'
    GROUP BY source, source_market;
  "
```

首期预期为 `source=tushare`、`source_market=SSE`。未来切换 Provider 时，
表结构和查询方式保持不变。

检查开市与休市都存在：

```bash
docker compose exec mysql mysql \
  -u"${MYSQL_USER}" \
  -p"${MYSQL_PASSWORD}" \
  "${MYSQL_DATABASE}" \
  -e "
    SELECT is_open, COUNT(*)
    FROM trading_calendar
    WHERE market_code = 'CN-S'
    GROUP BY is_open;
  "
```

## 8. 验证幂等

再次执行第 6 节相同命令，然后重复第 7 节计数。

预期：

- 总行数不增加。
- 内容与 Tushare 当前结果一致。
- `created_at` 保持不变，`sync_mode` 覆盖为本次执行模式，`updated_at` 更新。
- 不产生重复键。

## 9. 验证完整性边界

使用内存 Provider 或契约测试分别返回：

1. 请求范围内的完整自然日数据。
2. 覆盖历史和当日、但只包含连续未来前缀的数据。
3. 历史/当日缺口或未来前缀内部有断点的数据。
4. 空批次。

预期：

- 场景 1 返回 `COMPLETE`。
- 场景 2 成功写入已返回前缀，返回 `FUTURE_PARTIAL`、正确的 `coverage_end` 和
  `missing_future_count`；尾部不生成休市记录，查询为 `UNKNOWN`。
- 场景 3、4 失败且不改变既有数据。

## 10. 验证失败保护

使用契约测试或测试环境将 Tushare 响应替换为空批次、缺失日期或非 `SSE` 数据。

预期：

- Flow 失败且不提交任何部分数据。
- 已有 `trading_calendar` 记录保持不变。
- 凭据、额度/积分/当日配额耗尽和校验错误不重试；网络、短时限流、429、5xx
  最多重试 3 次。
- 日志包含明确错误类别。

分别模拟短时频率限制和额度耗尽。预期前者错误类别为 `RATE_LIMITED` 并按
30/120/300 秒退避；后者为 `QUOTA_EXHAUSTED`，首次失败后立即结束。

## 11. 查看日志

```bash
tail -n 20 logs/trading-calendar-sync.jsonl
```

预期每行均为有效 JSON，并包含：

- `timestamp`
- `event`
- `flow_run_id`
- `schedule_slug`
- `market_code`
- `start_date`
- `end_date`
- `coverage_end`
- `completeness_status`
- 结果统计或错误类别

确认日志中不存在 Token、数据库密码、连接串或完整请求体。

## 12. 验证计划及时性

从计划运行的终态 JSONL 日志确认存在：

- `scheduled_at`
- `started_at`
- `completed_at`
- `schedule_delay_ms`
- `run_duration_ms`
- `schedule_to_completion_ms`
- `timeliness_met`

验证公式：

```text
schedule_delay_ms = started_at - scheduled_at
run_duration_ms = completed_at - started_at
schedule_to_completion_ms = completed_at - scheduled_at
timeliness_met = schedule_to_completion_ms <= 600000
```

按 `schedule_slug` 分组读取最近 20 次已完成计划运行。满 20 次时，至少 95% 的
`timeliness_met` 必须为 `true`；不足 20 次时记录样本数和暂定比例。人工运行不计入。

## 13. 验证 Provider 可替换性

运行 Provider 一致性契约测试：

```bash
uv run pytest tests/contract/test_trading_calendar_provider.py
uv run pytest tests/unit/test_trading_calendar_service.py
```

预期：

- Tushare Adapter 与内存 Provider 使用同一套标准模型和异常。
- 领域服务测试使用内存 Provider，不导入 `integrations.tushare`。
- 切换测试 Provider 不修改 Flow、Service、Repository 或表结构。

验证通用 Tushare Client：

```bash
uv run pytest tests/contract/test_tushare_client.py
```

预期：`trade_cal` 和虚构的第二 API 名称共用同一请求、信封解析、错误分类与脱敏逻辑，
Client 中不存在日历专有字段映射。

## 14. 安全停止

暂停计划：

```bash
uv run prefect deployment schedule ls trading-calendar-sync/交易日历同步
uv run prefect deployment schedule pause \
  trading-calendar-sync/交易日历同步 \
  <schedule-id>
```

停止 Worker 使用 `Ctrl+C`。停止基础设施但保留数据：

```bash
docker compose down
```
