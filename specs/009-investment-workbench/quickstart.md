# 快速验证：投资工作台与任务通知

## 1. 验证目标

本指南验证登录、重要日、自选、真实股票与行情、券商金股、任务状态、20:00 汇总及飞书通知。
接口字段以 [OpenAPI](contracts/openapi.yaml) 为准，数据约束见 [数据模型](data-model.md)，
页面必须符合 [前端技术栈](../../docs/frontend-technology-stack.md) 和
[前端 UI 规范](../../docs/frontend-ui-design-guidelines.md)。

## 2. 前置条件

- WSL2 Ubuntu、Python 3.12、uv、Node.js、pnpm 和 Docker Desktop 可用。
- MySQL、ClickHouse、Redis、Prefect Server 已按根目录 `README.md` 启动并健康。
- 现有交易日历、股票列表、日线行情和券商金股至少各有一批成功同步数据。
- 飞书验证使用专用测试群机器人；真实 webhook 和签名密钥不得进入命令历史、截图或版本库。

## 3. 配置

从 `.env.example` 补充以下服务端示例配置，真实值只写入本机 `.env`：

```dotenv
APP_ENVIRONMENT=development
APP_BASE_URL=http://127.0.0.1:8000
SESSION_COOKIE_NAME=lucking_session
SESSION_IDLE_TIMEOUT_SECONDS=1800
SESSION_ABSOLUTE_TIMEOUT_SECONDS=28800
SESSION_COOKIE_SECURE=false
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/REPLACE_ME
FEISHU_SIGNING_SECRET=replace-me-or-leave-empty-when-signing-is-disabled
WORKBENCH_TIMEZONE=Asia/Shanghai
DAILY_TASK_SUMMARY_HOUR=20
```

生产环境必须将 `SESSION_COOKIE_SECURE=true`；服务启动时必须拒绝生产环境关闭 Secure Cookie、
非 `Asia/Shanghai` 汇总时区或不符合飞书 HTTPS webhook 约束的配置。

前端只使用可公开变量：

```dotenv
VITE_APP_TITLE=Lucking
VITE_API_BASE_URL=/api/v1
```

确认前端环境中不存在 webhook、数据库密码、Token 或签名密钥。

## 4. 初始化

```bash
uv sync --all-groups
uv run alembic upgrade head
pnpm --dir frontend install --frozen-lockfile
```

使用管理命令预置测试账号（交互式读取密码，不允许命令行参数携带密码）：

```bash
uv run python -m lucking.admin create-user tester 测试用户
```

启动 API 和前端：

```bash
uv run uvicorn lucking.api.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
pnpm --dir frontend dev
```

部署每日汇总 Flow，并确保 Process Worker 正在运行：

```bash
uv run prefect --no-prompt deploy --name 每日任务汇总通知
uv run prefect worker start --pool local-pool --type process
```

## 5. 自动化质量门禁

后端：

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

前端：

```bash
pnpm --dir frontend lint
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

契约专项验证：

```bash
uv run pytest tests/contract/test_workbench_openapi.py
uv run pytest tests/contract/test_scheduled_task_catalog.py
uv run pytest tests/contract/test_notification_sender.py
uv run pytest tests/integration/test_workbench_mysql_schema.py -m mysql
uv run pytest tests/integration/test_workbench_redis_session.py
uv run pytest tests/integration/test_workbench_clickhouse_queries.py
```

预期：全部检查通过；OpenAPI 可生成 TypeScript 客户端；计划任务目录与 `prefect.yaml` 无差异；
七张新增 MySQL 表的主键、时间字段、唯一键、外键和中文注释与 `data-model.md` 一致。

接口契约测试还必须验证：所有非 204 响应均包含
`code/message/data/errors/request_id/timestamp`；HTTP 状态与业务码分别断言；成功响应 `code=0`；
分页字段只位于 `data.pagination`；`timestamp` 为 UTC ISO 8601；前端生成类型不含 `any`；
受保护端点声明并返回统一 401，写端点声明统一 403，请求校验统一映射为 400 而非 FastAPI 默认 422，
未预期错误使用统一 500 响应；还需逐项断言 OpenAPI `BusinessErrorCode` 的稳定枚举及其
400/401/403/404/409/429/500/503 映射，避免只校验业务码号段。

## 6. 端到端验收场景

### 6.1 登录、退出和修改密码

1. 未登录访问工作台任一受保护路由，确认跳转登录页且没有受保护 API 数据。
2. 错误账号或密码显示统一错误，不透露账号是否存在；连续失败触发限流。
3. 正确登录后确认 Cookie 为 HttpOnly、SameSite=Lax，生产配置要求 Secure。
4. 缺少或伪造 CSRF Token 执行写请求应返回 403。
5. 修改密码后所有已有会话失效；旧密码不能登录，新密码可以登录。
6. 退出后刷新受保护页面再次进入登录页。

### 6.2 日历和重要日

1. 打开包含已同步交易日的月份，确认 `OPEN/CLOSED/UNKNOWN` 与数据库一致。
2. 在同一日新增两个不同标题的重要日，刷新后仍存在。
3. 重复新增同标题重要日返回冲突；编辑和删除后状态正确。
4. 使用第二个测试账号确认看不到第一个账号的重要日。
5. 删除重要日后确认交易日状态没有变化。

### 6.3 股票、行情与自选

1. 按代码和名称搜索真实 `stock_current` 数据，确认服务端分页和稳定排序。
2. 打开股票详情，确认最新行情和日线图来自 ClickHouse，并显示数据日期/更新时间。
3. 对停牌、过期和完全缺失行情分别显示明确文本状态，不合成价格。
4. 创建、重命名、删除自选分组；添加和移除股票后刷新仍一致。
5. 第二个账号不能读取或修改第一个账号的分组，即使知道业务 UUID。

### 6.4 券商金股与任务页

1. 选择有真实同步数据的推荐月份（以月份首日表示），按券商筛选并核对股票身份与更新时间。
2. 打开今日任务页，确认计划任务按成功、部分完成、失败、运行中、未知或未执行显示。
3. 状态必须同时含文字或图标，不得只依赖颜色；错误摘要不得包含 Token、webhook 或原始响应。
4. 局部轮询时保留已展示数据并显示轻量刷新状态，不闪烁整个页面。

### 6.5 20:00 汇总和飞书通知

使用受控测试时钟或测试 Deployment 传入固定 `scheduled_for`，不要修改生产 Cron：

```bash
uv run prefect deployment run 'daily-task-summary/每日任务汇总通知' \
  --param scheduled_for=2026-08-08T20:00:00+08:00
```

验证：

1. 汇总只纳入该日 20:00 前应运行的计划任务，人工/回补任务不参与。
2. 数据库明细与任务页、飞书消息中的总数和各状态数量完全一致。
3. 相同日期重复自动触发不发送第二条成功通知。
4. 测试 429、5xx 和网络失败时按 30/120/300 秒重试；永久错误不重试。
5. 失败状态在任务页 5 分钟内可查；修复配置后使用补发 Deployment，内容摘要不改变。
6. 飞书消息小于 20 KB，异常明细过多时只截断展示，不改变汇总计数。

人工补发必须使用数据库中已存在的 `summary_id`，不会重新计算快照：

```bash
uv run prefect deployment run 'resend-daily-task-summary/每日任务汇总补发' \
  --param summary_id=替换为任务页或数据库中的汇总ID
```

## 7. 视觉与可访问性验收

- 在亮色和暗色主题分别核对 `design/frontend-theme-demo-light.png`、
  `design/frontend-theme-demo-dark.png` 的视觉方向。
- 桌面宽度 `≥1120px` 使用 248px 浮动侧边栏；860px、620px 两个断点不丢失基本操作。
- 页面只有一个 `h1`，主内容 Surface 不滥用阴影，香槟金可见面积不超过 5%，通用状态不使用绿色。
- 表单标签、字段错误、焦点环、图标可访问名称、键盘操作、Reduced Motion 和图表文本摘要均通过检查。
- 首屏 3 秒、股票搜索 2 秒和 20:05 通知目标按规格中的 SC-003/SC-005 采样验证。

## 8. 安全与故障检查

```bash
rg -n 'open-apis/bot/v2/hook|FEISHU_BOT_SIGNING_SECRET|password_hash' \
  frontend dist logs . --glob '!*.example' --glob '!specs/**'
```

预期：前端产物和日志无匹配；源码仅允许配置字段名、模型字段名和脱敏测试夹具，不得出现真实值。
停止 Worker 可阻止新通知任务；已开始的汇总由 MySQL 状态机和短事务保证可恢复。
API 健康检查为 `curl -fsS http://127.0.0.1:8000/healthz`。请求日志位于
`logs/workbench-api.jsonl`，汇总日志位于 `logs/daily-task-summary.jsonl`；分别按
`request_id`，以及 `summary_id/task_key/flow_run_id/attempt` 关联排障。安全停止时先暂停
“每日任务汇总通知” Deployment，再停止 Worker；不要删除数据库卷或汇总/attempt 记录。
