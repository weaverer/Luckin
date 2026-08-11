# 实施计划：投资工作台与任务通知

**分支**：`009-investment-workbench` | **日期**：2026-08-08 | **规格**：[spec.md](spec.md)

**输入**：来自 `specs/009-investment-workbench/spec.md` 的功能规格

## 摘要

在现有 Lucking 数据同步单体中新增同域 FastAPI REST API 和 Vue 3 单页应用，提供管理员预置账号的
登录/退出/改密、交易日历与个人重要日、真实股票与日线行情、自选分组、券商金股和每日任务状态。
新增 Prefect 20:00 汇总 Flow，通过各同步领域的只读适配器归一计划任务状态，将不可变快照写入 MySQL，
再经供应商无关通知 Port 和飞书自定义机器人 Adapter 发送唯一汇总。Redis 只保存可撤销会话；
ClickHouse 和既有同步业务表保持只读与原所有权。前端严格遵循 `docs/` 中技术栈和“月石鎏金”UI 规范。

## 技术上下文

**语言/版本**：后端 Python 3.12；前端 TypeScript（strict）+ Vue 3

**主要依赖**：FastAPI、SQLAlchemy 2、Alembic、Pydantic Settings、Prefect 3、HTTPX、
pwdlib/Argon2、redis-py；Vue 3、Vite、PrimeVue 4、PrimeIcons、Vue Router、Pinia、
TanStack Vue Query、Axios、ECharts/vue-echarts

**存储**：MySQL（账号、重要日、自选、任务快照、通知审计及既有事务数据）；
ClickHouse（既有日线行情只读）；Redis（登录会话与 CSRF 临时状态）；Prefect Server（编排）

**测试**：pytest、pytest-cov、Memory/HTTP Adapter 契约测试、MySQL/Redis/ClickHouse 集成测试、
OpenAPI 契约；Vitest、Vue Test Utils、Playwright；ruff、mypy、ESLint、Prettier、TypeScript build

**目标平台**：WSL2/Linux 应用进程与现代桌面浏览器；Docker Compose 继续只承载
MySQL、ClickHouse、Redis 和 Prefect Server；生产使用同域 HTTPS 反向代理

**项目类型**：单体 Web 应用（现有 Python 后端 + 新增 `frontend/` SPA），不新增独立服务

**性能目标**：主要页面 3 秒内展示首屏结构/加载状态；95% 股票搜索 2 秒内展示结果；
20:00 汇总 5 分钟内完成并进入通知；股票列表按 10,000 条、行情单次最多 400 个交易日验证

**约束**：`Asia/Shanghai` 决定业务日与 20:00 调度，跨系统时间为 UTC/ISO 8601；
同域安全 Cookie + CSRF；所有秘密仅服务端配置；前端只使用真实项目 API；
服务端配置以强类型 Settings 承载且秘密使用 `SecretStr`；不引入 SSR/WebSocket/SSE/微前端/
Tailwind/第二套组件或图表库；状态不能只靠颜色表达

**规模/范围**：首期内部用户不超过 50；自选分组数量不设业务上限、每组最多 200 只股票；
计划任务目录不超过 100 项；汇总与通知审计保留至少 2 年；桌面端优先并保留窄屏基本操作

## 前端 API 数据结构

所有有响应体的 `/api/v1` 接口统一返回同一信封，前端 Axios 响应拦截器只解析这一种结构：

```json
{
  "code": 0,
  "message": "",
  "data": {},
  "errors": [],
  "request_id": "8f74d2c71b34431e9fa8e8e2d6ab23e1",
  "timestamp": "2026-08-08T12:00:00Z"
}
```

分页列表将数据和分页信息一起放入 `data`：

```json
{
  "code": 0,
  "message": "",
  "data": {
    "items": [],
    "pagination": {
      "limit": 100,
      "offset": 0,
      "total": 0,
      "has_more": false
    }
  },
  "errors": [],
  "request_id": "8f74d2c71b34431e9fa8e8e2d6ab23e1",
  "timestamp": "2026-08-08T12:00:00Z"
}
```

统一规则：

- HTTP 状态码表达协议层结果，业务 `code` 表达应用结果；不得以 HTTP 200 包装失败。
- 成功时 `code=0`、`message=""`、`errors=[]`，`data` 为具体 DTO、数组或分页对象。
- 失败时 `code` 为非零稳定整数、`message` 为安全中文摘要、`data=null`；
  `errors` 为结构化错误数组，无明细时使用空数组。
- 分页接口的 `data` 固定为 `{items,pagination}`，其中
  `pagination={limit,offset,total,has_more}`；非分页 DTO 不添加分页字段。
- `request_id` 和 UTC ISO 8601 `timestamp` 在所有有响应体的结果中必填。项目宪章要求跨系统时间使用
  ISO 8601，因此不采用 13 位 Unix 毫秒值。
- HTTP 204 不返回响应体；文件流等未来特殊接口须在 OpenAPI 中显式申请例外，本期没有例外。
- 除登录外的受保护端点必须在 OpenAPI 中声明统一 401；写端点声明统一 403；请求参数和请求体
  校验统一映射为 HTTP 400 与 `20xxxx` 业务码，不暴露 FastAPI 默认 422 结构；所有端点声明统一
  500 响应。运行时 `/openapi.json` 必须与设计契约一致。
- 业务码采用稳定枚举并与 HTTP 状态显式映射：`100001` 登录凭据错误（401）、`100002`
  会话缺失/过期/撤销（401）、`100003` CSRF/同源校验失败（403）、`200001` 请求校验失败
  （400）、`200002` 日期或查询范围无效（400）、`200003` 密码策略不满足（400）、`300001`
  资源不存在或不属于当前用户（404）、`400001` 重要日冲突（409）、`400002` 自选分组名冲突
  （409）、`400003` 自选成员重复（409）、`400004` 自选分组或成员容量超限（409）、`500001`
  请求限流（429）、`500002` 外部依赖不可用（503）、`900001` 未预期服务错误（500）。新增或
  变更业务码必须先更新 OpenAPI、契约测试和前端生成类型，不得只依赖号段约定。
- `errors[]` 元素统一为 `{field,code,message}`；非字段错误的 `field=null`，不得包含堆栈、SQL、
  凭据或第三方原始响应。
- 日期、时间、枚举、Decimal 和可空字段以 OpenAPI 为准；Decimal 统一用十进制字符串传输，
  防止 JavaScript 浮点精度损失。
- OpenAPI 为每种 `data` 定义强类型 Response，生成后的 TypeScript 不使用 `any`；
  Axios 只负责统一错误转换，不在组件内重复解包或判断协议形状。

## 宪章检查

*GATE：Phase 0 研究前通过；Phase 1 设计后已复核。*

### 研究前检查

- **规格与追溯：通过**。五个用户故事、FR-001～FR-016、NFR-001～NFR-005、
  ED-001～ED-004 和 SC-001～SC-007 已定义；研究任务分别对应认证、前端、查询、任务汇总、飞书与质量门禁。
- **架构与数据边界：通过**。预设前端只调用 FastAPI；Service/Repository 隔离协议与存储；
  MySQL/ClickHouse/Redis/Prefect 按宪章职责分配；既有同步领域只读。
- **第三方数据源可替换性：通过**。页面消费现有供应商无关 Service；飞书通过新
  `NotificationSender` Port 隔离，研究要求 Memory Sender、错误映射与契约测试。
- **测试与质量门禁：通过**。计划覆盖后端单元/契约/集成、前端单元/组件/E2E、OpenAPI 生成、
  静态检查、类型检查和生产构建。
- **统一公共 API 响应：通过**。计划定义六字段信封、HTTP/业务码分离、分页位置、统一
  400/401/403/500、request_id、UTC ISO 8601 时间戳和运行时 OpenAPI 契约测试。
- **安全与最小暴露：通过**。账号由管理员预置；密码哈希、Redis 会话、CSRF、同源、访问隔离、
  登录限流和 webhook `SecretStr` 已纳入研究；不扩大基础设施网络暴露。
- **可观测与运维：通过**。计划要求 request/summary/task/attempt/flow 关联标识、脱敏结构化日志、
  汇总/通知状态机、健康检查和 quickstart 故障验证。
- **MySQL 表结构：通过**。预计新增项目表全部使用 `BIGINT AUTO_INCREMENT`、数据库维护时间、
  UUID 业务身份、业务唯一键和中文表/列注释，不预申请例外。
- **简洁性：通过**。复用单体、现有数据库、Redis 和 Prefect；轮询满足任务页面；不新增服务或实时协议。

### 设计后复核

- **规格与追溯：通过**。[OpenAPI](contracts/openapi.yaml) 覆盖 FR-001～FR-010、FR-014；
  [每日任务契约](contracts/daily-task-notification.md) 覆盖 FR-011～FR-016；
  [quickstart](quickstart.md) 逐故事验证 SC-001～SC-007。
- **架构与数据边界：通过**。[data-model.md](data-model.md) 明确四类存储所有权、七张新表、
  Redis 会话和既有只读投影；[应用服务契约](contracts/application-services.md) 禁止路由和组件直连存储/供应商。
- **第三方数据源可替换性：通过**。飞书专有 webhook、签名、消息卡片、限流和错误码只在 Adapter；
  Service 只依赖规范通知 DTO/Disposition；Memory Sender 覆盖替代性。现有行情/金股仍走既有项目契约。
- **测试与质量门禁：通过**。设计定义 OpenAPI、目录对齐、Reader golden cases、Sender golden cases、
  MySQL/Redis/ClickHouse 集成、Cookie/CSRF/跨用户安全及 Playwright 关键流程。
- **统一公共 API 响应：通过**。[OpenAPI](contracts/openapi.yaml) 为成功和全部可达错误状态定义
  强类型响应；校验错误统一为 400，受保护/写端点声明 401/403，未预期错误声明 500；契约测试同时
  校验静态契约和运行时 `/openapi.json`，禁止默认 422 和 HTTP 200 包装失败。
- **安全与最小暴露：通过**。采用 Argon2id、Redis 可撤销不透明会话、HttpOnly/Secure/SameSite Cookie、
  CSRF 与同源校验；webhook/签名/密码不入前端、日志、表或错误；改密撤销全部会话。
- **可观测与运维：通过**。七表保留业务和通知审计；HTTP 外调不持事务；日志关联 request ID、
  summary ID、task key、flow/run/attempt；quickstart 包含部署、重试、补发和安全停止。
- **MySQL 表结构：通过**。`data-model.md` 逐表定义七张表的 `id BIGINT AUTO_INCREMENT`、
  `created_at/updated_at`、UUID、唯一/外键、字段类型和中文注释；`watchlist_member` 也不申请复合主键例外。
- **简洁性：通过**。仅新增 API/SPA 两个现有单体内入口、一个汇总 Flow、一个通知 Port；
  实时页采用 Vue Query 轮询，历史页读取快照，不新增消息队列、BFF、实时协议或重复数据源。

## 项目结构

### 文档（本功能）

```text
specs/009-investment-workbench/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   ├── application-services.md
│   └── daily-task-notification.md
└── tasks.md                       # 后续 /speckit-tasks 生成
```

### 源代码（仓库根目录）

```text
prefect.yaml

migrations/
└── versions/
    └── 007_create_workbench_tables.py

src/lucking/
├── config.py                       # 强类型服务端配置与 SecretStr 秘密
├── api/
│   ├── main.py
│   ├── dependencies.py            # 会话、CSRF、Repository/Service 注入
│   ├── responses.py               # 统一 code/message/data/errors 响应结构
│   ├── errors.py                  # HTTP 状态与业务 code 映射
│   └── routes/
│       ├── __init__.py             # 聚合并注册全部 APIRouter
│       ├── auth.py
│       ├── calendar.py
│       ├── stocks.py
│       ├── watchlists.py
│       ├── broker_recommendations.py
│       └── task_status.py
├── admin/
│   └── __main__.py                # 交互式预置/禁用账号
├── models/
│   └── workbench.py               # 七张 MySQL ORM 表及规范 DTO/枚举
├── ports/
│   ├── notification_sender.py
│   ├── session_store.py
│   └── task_execution_reader.py
├── repositories/
│   ├── workbench/
│   │   ├── __init__.py
│   │   ├── users.py               # 账号事务与用户状态
│   │   ├── important_dates.py     # 当前用户重要日事务
│   │   ├── watchlists.py          # 自选分组与成员事务
│   │   └── task_summaries.py      # 汇总快照与通知尝试事务
│   └── workbench_queries/
│       ├── __init__.py
│       ├── calendar.py            # 交易日历只读组合查询
│       ├── stocks.py              # 股票与 ClickHouse 行情只读查询
│       └── broker_recommendations.py # 券商金股只读查询
├── services/
│   ├── auth.py
│   ├── calendar_workspace.py
│   ├── stock_workspace.py
│   ├── watchlist.py
│   ├── broker_recommendation_query.py
│   └── daily_task_summary.py
├── integrations/
│   ├── feishu/
│   │   └── notification_sender.py
│   └── task_readers/              # 各既有同步领域到规范状态的只读适配器
├── flows/
│   └── daily_task_summary.py
└── task_catalog.py                # 与 prefect.yaml 对齐的计划任务目录

frontend/
├── public/
├── src/
│   ├── app/
│   │   ├── layouts/
│   │   │   ├── AppLayout.vue
│   │   │   ├── AppSidebar.vue
│   │   │   └── AppTopbar.vue
│   │   ├── providers/
│   │   └── router/
│   ├── api/
│   │   ├── client/
│   │   ├── generated/
│   │   └── query-keys/
│   ├── components/
│   │   ├── common/                # Surface、状态、加载、空、错误和未交付状态
│   │   └── charts/                # BaseChart、亮暗主题、行情图
│   ├── composables/
│   ├── stores/                    # 会话、主题、布局；不复制查询数据
│   ├── styles/
│   │   ├── theme/
│   │   ├── tokens.css
│   │   ├── reset.css
│   │   └── app.css
│   ├── views/
│   │   ├── LoginView.vue
│   │   ├── DashboardView.vue
│   │   ├── CalendarView.vue
│   │   ├── StocksView.vue
│   │   ├── StockDetailView.vue
│   │   ├── WatchlistsView.vue
│   │   ├── BrokerRecommendationsView.vue
│   │   ├── TaskStatusView.vue
│   │   └── AccountView.vue
│   ├── App.vue
│   └── main.ts
├── tests/
│   ├── unit/
│   ├── component/
│   └── e2e/
├── package.json
├── pnpm-lock.yaml
├── vite.config.ts
├── vitest.config.ts
└── playwright.config.ts

tests/
├── contract/
│   ├── test_workbench_openapi.py
│   ├── test_scheduled_task_catalog.py
│   ├── test_task_execution_readers.py
│   └── test_notification_sender.py
├── integration/
│   ├── test_workbench_mysql_schema.py
│   ├── test_workbench_important_dates.py
│   ├── test_workbench_watchlists.py
│   ├── test_workbench_redis_session.py
│   ├── test_workbench_clickhouse_queries.py
│   ├── test_workbench_api.py
│   ├── test_workbench_security.py
│   ├── test_workbench_performance.py
│   └── test_daily_task_summary_flow.py
└── unit/
    ├── test_workbench_config.py
    ├── test_auth_service.py
    ├── test_calendar_workspace.py
    ├── test_stock_workspace.py
    ├── test_watchlist_service.py
    ├── test_broker_recommendation_query.py
    ├── test_daily_task_summary.py
    └── test_feishu_notification.py
```

**结构决策**：保留现有 `src/lucking` 单体并新增 `api` 入口与工作台垂直切片；已有同步服务、
Repository 和业务表不迁移、不复制。前端按 `docs/frontend-technology-stack.md` 建立独立 `frontend/`
工程，OpenAPI 生成代码与手写客户端分离。汇总 Reader 按源领域拆分 Adapter，统一 Service 只处理
规范状态；飞书 Adapter 独占第三方协议。工作台事务 Repository 和只读 Query Repository 按领域拆分，
避免 US2、US3、US4、US5 并行修改同一文件。API 使用聚合 Router，故事路由只修改自身模块。
共享前端基础阶段先创建全部路由级页面和明确的未交付状态，后续故事只替换自身页面为真实 API 状态；
US1 因此可独立验收认证、工作台和导航，同时不会与并行故事争用页面文件。
该结构支持以后替换通知实现或添加页面，而不改变领域边界。

## 复杂度跟踪

无宪章违规或待批准例外。七张 MySQL 表、一个通知 Port 和多个只读任务 Reader 均由明确的
事务隔离、历史快照、供应商替换或既有异构运行模型要求驱动，未引入额外服务或基础设施。
