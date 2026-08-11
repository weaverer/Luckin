# 任务：投资工作台与任务通知

**输入**：`specs/009-investment-workbench/` 中的 `spec.md`、`plan.md`、`research.md`、
`data-model.md`、`contracts/` 和 `quickstart.md`

**测试要求**：本功能改变公共 API、数据模型、认证与故障处理，测试任务必须先于对应实现执行。
所有公共 JSON API 必须验证统一响应信封、HTTP 状态与业务码分离、分页位置、请求追踪和
UTC ISO 8601 时间戳。

**组织方式**：共享基础设施先完成；后续按用户故事分组，每个故事均有独立验收标准。

## 格式：`[ID] [P?] [Story?] 描述（含精确文件路径）`

- **[P]**：可与同阶段其他标记任务并行，且不修改同一文件
- **[Story]**：映射到 `spec.md` 的用户故事，例如 `[US1]`

---

## 阶段 1：初始化（共享工程）

**目标**：补齐后端依赖、服务端配置和 Vue 前端工程，使后续功能有稳定的构建入口。

- [X] T001 在 `pyproject.toml`、`uv.lock` 和 `.env.example` 中补齐 API、Redis 会话、Argon2、飞书及测试依赖和示例变量，并确保示例配置不含真实秘密
- [X] T002 [P] 在 `frontend/package.json`、`frontend/pnpm-lock.yaml` 和 `frontend/index.html` 中初始化 Vue 3 + TypeScript + Vite + PrimeVue 4 + Vue Query + Axios + ECharts 工程
- [X] T003 [P] 在 `frontend/tsconfig.json`、`frontend/vite.config.ts`、`frontend/vitest.config.ts`、`frontend/playwright.config.ts` 和 `frontend/eslint.config.js` 中启用 strict、代理、单元测试、E2E 与质量门禁
- [X] T004 [P] 在 `src/lucking/config.py` 和 `tests/unit/test_workbench_config.py` 中实现并验证会话/Cookie、`Asia/Shanghai` 汇总时区、`SecretStr` 飞书 webhook/签名密钥、生产 Secure Cookie 和禁止前端导出秘密的强类型服务端配置
- [X] T005 [P] 在 `frontend/src/main.ts`、`frontend/src/App.vue`、`frontend/src/app/providers/index.ts` 和 `frontend/src/env.d.ts` 中建立前端启动与 Provider 入口

**检查点**：Python 包可导入，前端空壳可执行 lint、typecheck、test 和 build。

---

## 阶段 2：基础能力（阻塞所有用户故事）

**目标**：建立七张治理合规的 MySQL 表、认证基础、统一 API 响应与前端共享框架。

**关键要求**：本阶段完成前，不得开始用户故事实现。

- [X] T006 [P] 先在 `tests/integration/test_workbench_mysql_schema.py` 编写七张工作台表的主键、数据库维护时间、唯一键、外键、索引及中文表/列注释失败测试
- [X] T007 在 `src/lucking/models/workbench.py` 和 `src/lucking/models/__init__.py` 中定义 AppUser、ImportantDate、WatchlistGroup、WatchlistMember、DailyTaskSummary、DailyTaskSummaryItem、DailyTaskNotificationAttempt ORM 与枚举
- [X] T008 在 `migrations/versions/007_create_workbench_tables.py` 中创建七张表及 `data-model.md` 指定的约束、索引和中文注释，并使 T006 通过
- [X] T009 [P] 先在 `tests/contract/test_workbench_openapi.py` 中验证设计态 `specs/009-investment-workbench/contracts/openapi.yaml` 的结构、operationId 唯一性、非 204 六字段信封、分页 `data.pagination`、强类型 DTO、稳定业务码枚举及 400/401/403/404/409/429/500/503 映射，并为当前应用基础壳验证无默认 422 与 HTTP/业务码分离；本阶段不要求尚未实现的用户故事 Router 已出现在运行时 `/openapi.json`
- [X] T010 在 `src/lucking/api/responses.py` 和 `src/lucking/api/errors.py` 中实现泛型成功/失败模型、分页模型、OpenAPI 已登记的稳定业务码枚举及领域异常到 HTTP 400/401/403/404/409/429/500/503 的映射
- [X] T011 在 `src/lucking/api/__init__.py`、`src/lucking/api/main.py`、`src/lucking/api/dependencies.py`、`src/lucking/api/routes/__init__.py`、`src/lucking/api/routes/auth.py`、`src/lucking/api/routes/calendar.py`、`src/lucking/api/routes/stocks.py`、`src/lucking/api/routes/watchlists.py`、`src/lucking/api/routes/broker_recommendations.py` 和 `src/lucking/api/routes/task_status.py` 中实现应用工厂、request_id 中间件、UTC 响应时间、请求校验到统一 400、统一 500、依赖注入、空 Router 模块及一次性聚合注册
- [X] T012 [P] 先在 `tests/unit/test_auth_service.py` 中覆盖统一登录失败、密码策略、账号禁用、退出和改密后全会话撤销
- [X] T013 [P] 在 `src/lucking/ports/session_store.py` 和 `src/lucking/repositories/redis_session.py` 中实现不透明会话 Port、摘要 Key、空闲/绝对过期、CSRF 状态与用户全会话撤销
- [X] T014 在 `src/lucking/repositories/workbench/__init__.py` 和 `src/lucking/repositories/workbench/users.py` 中实现 AppUser 事务 Repository、用户状态和用户所有权查询基础能力
- [X] T015 在 `src/lucking/services/auth.py`、`src/lucking/admin/__init__.py` 和 `src/lucking/admin/__main__.py` 中实现 Argon2id 认证、登录限流、预置/禁用账号命令、退出和修改密码服务
- [X] T016 在 `src/lucking/api/routes/auth.py` 和 `src/lucking/api/dependencies.py` 中实现登录、当前用户、退出、改密、Cookie、同源及 CSRF 校验，并保持 204 无响应体
- [X] T017 在 `tests/integration/test_workbench_redis_session.py` 和 `tests/integration/test_workbench_api.py` 中验证 Cookie/CSRF、会话过期、跨源拒绝、改密撤销、401/403/429 和统一错误响应
- [X] T018 [P] 在 `frontend/src/styles/tokens.css`、`frontend/src/styles/reset.css`、`frontend/src/styles/app.css` 和 `frontend/src/styles/theme/index.ts` 中实现 docs 规定的“月石鎏金”亮暗 Token 与全局样式
- [X] T019 [P] 在 `frontend/src/api/generated/`、`frontend/src/api/client/http.ts`、`frontend/src/api/client/errors.ts` 和 `frontend/src/api/query-keys/index.ts` 中生成 OpenAPI 类型并实现唯一 Axios 客户端、统一信封解包和错误转换
- [X] T020 在 `frontend/src/app/router/index.ts`、`frontend/src/app/router/routes.ts`、`frontend/src/stores/session.ts`、`frontend/src/stores/theme.ts`、`frontend/src/app/layouts/AppLayout.vue`、`frontend/src/app/layouts/AppSidebar.vue`、`frontend/src/app/layouts/AppTopbar.vue`、`frontend/src/components/common/FeaturePendingState.vue`、`frontend/src/views/CalendarView.vue`、`frontend/src/views/StocksView.vue`、`frontend/src/views/StockDetailView.vue`、`frontend/src/views/WatchlistsView.vue`、`frontend/src/views/BrokerRecommendationsView.vue` 和 `frontend/src/views/TaskStatusView.vue` 中实现受保护路由、会话/主题状态、响应式导航骨架及可编译的未交付页面状态；不得展示静态业务假数据
- [X] T021 [P] 在 `frontend/src/components/common/AppSurface.vue`、`frontend/src/components/common/AsyncState.vue` 和 `frontend/tests/component/common-states.spec.ts` 中实现并验证规范加载、空、错误、过期和刷新状态

**检查点**：数据库模式、认证、统一 API 信封、OpenAPI 类型生成和前端受保护布局均可独立验证。

---

## 阶段 3：用户故事 1——查看投资工作台（优先级：P1）🎯 MVP

**目标**：用户可登录、退出、修改密码并识别所有页面入口；已交付页面使用真实状态，未交付入口明确标识且不伪装业务空数据。

**独立测试**：有效账号登录后确认工作台、导航入口和交付状态；访问已交付入口后登录状态保持有效，未交付入口不导致构建失败；退出后受保护路由返回登录页。

### 测试（先写并确认失败）

- [X] T022 [P] [US1] 在 `frontend/tests/unit/session-store.spec.ts` 中覆盖会话恢复、统一 API 错误、改密后清空状态和退出行为
- [X] T023 [P] [US1] 在 `frontend/tests/component/login-dashboard.spec.ts` 中覆盖登录字段错误、加载状态、工作台全部入口、未交付状态、空状态、键盘操作，以及入口不存在阻塞性错误

### 实现

- [X] T024 [US1] 在 `frontend/src/composables/useAuth.ts`、`frontend/src/views/LoginView.vue` 和 `frontend/src/views/AccountView.vue` 中实现登录、当前会话、退出和修改密码流程
- [X] T025 [P] [US1] 在 `frontend/src/app/navigation.ts` 和 `frontend/src/app/layouts/AppSidebar.vue` 中配置工作台、日历、股票、行情、自选、金股、任务和账号入口、页面标题及交付状态
- [X] T026 [US1] 在 `frontend/src/views/DashboardView.vue` 和 `frontend/src/components/common/DataFreshness.vue` 中实现工作台入口、当日任务摘要容器、最近更新时间及规范空/错误状态
- [X] T027 [US1] 在 `frontend/tests/e2e/auth-workbench.spec.ts` 中验证未登录重定向、登录后完整导航、改密导致会话撤销和退出闭环

**检查点**：US1 可独立演示为登录后可导航的工作台 MVP；已交付页使用真实 API 状态，未交付页明确标识且不伪装为空数据。

---

## 阶段 4：用户故事 2——管理日历与重要日（优先级：P1）

**目标**：用户查看交易日/休市日/未知日期，并管理仅自己可见的重要日。

**独立测试**：指定月份显示真实交易日历；新增、刷新、编辑、删除重要日均正确，第二个账号不可见。

### 测试（先写并确认失败）

- [X] T028 [P] [US2] 在 `tests/unit/test_calendar_workspace.py` 中覆盖 400 天范围、UNKNOWN 语义、标题规范化、重复冲突和用户隔离
- [X] T029 [P] [US2] 在 `tests/integration/test_workbench_important_dates.py` 中添加 ImportantDate MySQL 事务、唯一约束和所有权契约测试
- [X] T030 [P] [US2] 在 `frontend/tests/component/calendar.spec.ts` 中覆盖月历状态、重要日表单校验、刷新保留和无交易日数据状态

### 实现

- [X] T031 [US2] 在 `src/lucking/repositories/workbench/important_dates.py`、`src/lucking/repositories/workbench_queries/__init__.py`、`src/lucking/repositories/workbench_queries/calendar.py` 和 `src/lucking/services/calendar_workspace.py` 中实现重要日事务及交易日历只读组合查询，并保持缺失日为 UNKNOWN
- [X] T032 [US2] 在 `src/lucking/api/routes/calendar.py` 中实现 `/calendar` 与重要日新增、修改、删除端点及用户所有权/CSRF 映射
- [X] T033 [P] [US2] 在 `frontend/src/api/query-keys/calendar.ts` 和 `frontend/src/composables/useCalendar.ts` 中实现日历查询、重要日 Mutation、缓存失效和字段错误映射
- [X] T034 [US2] 在 `frontend/src/views/CalendarView.vue` 和 `frontend/src/components/calendar/ImportantDateDialog.vue` 中实现月视图、交易状态、重要日 CRUD 和数据更新时间
- [X] T035 [US2] 在 `frontend/tests/e2e/calendar-important-dates.spec.ts` 中验证真实交易日历、重要日持久化、重复冲突、删除不影响交易日和跨用户隔离

**检查点**：US2 可在不依赖股票、自选或通知功能的情况下独立验收。

---

## 阶段 5：用户故事 3——浏览股票、行情与自选（优先级：P1）

**目标**：用户可搜索真实股票、查看 ClickHouse 日线行情及状态，并管理个人自选分组和成员。

**独立测试**：搜索并打开一只真实股票，确认行情时间/状态；创建分组、加入股票、刷新后移除，第二个账号不可访问。

### 测试（先写并确认失败）

- [X] T036 [P] [US3] 在 `tests/integration/test_workbench_clickhouse_queries.py` 中覆盖 stock_id 行情查询、稳定排序、120/400 条限制、停牌缺口和 CURRENT/STALE/MISSING 状态
- [X] T037 [P] [US3] 在 `tests/unit/test_stock_workspace.py` 中覆盖代码/名称搜索、稳定分页、Decimal 字符串和禁止 Provider 调用
- [X] T038 [P] [US3] 在 `tests/unit/test_watchlist_service.py` 中覆盖分组上限、名称规范化、成员上限、重复冲突、显式删除和用户隔离
- [X] T039 [P] [US3] 在 `tests/integration/test_workbench_watchlists.py` 中添加 WatchlistGroup/Member MySQL 契约、事务清理、股票外键和跨用户拒绝测试

### 实现

- [X] T040 [US3] 在 `src/lucking/repositories/workbench_queries/stocks.py` 中实现既有 `stock_current`、ClickHouse `daily_quote` 的只读查询与规范 stock_id 组合
- [X] T041 [US3] 在 `src/lucking/services/stock_workspace.py` 中实现股票搜索、详情、行情范围和 CURRENT/STALE/MISSING 判定
- [X] T042 [US3] 在 `src/lucking/repositories/workbench/watchlists.py` 中完成自选分组/成员事务 Repository、稳定排序和用户所有权过滤
- [X] T043 [US3] 在 `src/lucking/services/watchlist.py` 中实现分组 CRUD、成员增删、容量限制和冲突语义
- [X] T044 [P] [US3] 在 `src/lucking/api/routes/stocks.py` 中实现股票分页、详情和日线行情端点，确保分页只位于 `data.pagination`
- [X] T045 [P] [US3] 在 `src/lucking/api/routes/watchlists.py` 中实现自选分组与成员端点、CSRF 和跨用户 404 语义
- [X] T046 [P] [US3] 在 `frontend/src/api/query-keys/stocks.ts`、`frontend/src/composables/useStocks.ts` 和 `frontend/src/views/StocksView.vue` 中实现服务端搜索、筛选、分页及无结果状态
- [X] T047 [P] [US3] 在 `frontend/src/components/charts/BaseChart.vue`、`frontend/src/components/charts/DailyQuoteChart.vue` 和 `frontend/src/views/StockDetailView.vue` 中实现亮暗行情图、文本摘要、更新时间和缺失/过期状态
- [X] T048 [P] [US3] 在 `frontend/src/api/query-keys/watchlists.ts`、`frontend/src/composables/useWatchlists.ts` 和 `frontend/src/views/WatchlistsView.vue` 中实现分组及成员 CRUD、缓存失效和确认交互
- [X] T049 [US3] 在 `frontend/tests/component/stocks-watchlists.spec.ts` 和 `frontend/tests/e2e/stocks-watchlists.spec.ts` 中验证搜索时限、行情状态、分组持久化、成员增删和跨用户隔离

**检查点**：US3 使用现有项目数据接口和存储，不直接调用第三方 Provider，且可独立验收。

---

## 阶段 6：用户故事 5——接收每日任务汇总通知（优先级：P1）

**目标**：每天 Asia/Shanghai 20:00 生成唯一任务快照，经可替换通知 Port 发送飞书汇总，并支持安全重试与补发。

**独立测试**：用固定计划时点和 Memory Sender 生成汇总，核对六种状态、幂等、失败重试、补发快照和秘密脱敏。

### 契约与单元测试（先写并确认失败）

- [X] T050 [P] [US5] 在 `tests/contract/test_scheduled_task_catalog.py` 和 `tests/contract/test_task_execution_readers.py` 中验证 `prefect.yaml` 目录对齐，并为七个领域 Reader 定义六种归一状态 golden cases
- [X] T051 [P] [US5] 在 `tests/contract/test_notification_sender.py`、`tests/unit/test_daily_task_summary.py`、`tests/unit/test_feishu_notification.py` 和 `tests/integration/test_daily_task_summary_flow.py` 中覆盖通知替换、20:00 汇总、并发幂等、重试、20:05 失败可查、补发不重算和秘密脱敏

### 实现

- [X] T052 [P] [US5] 在 `src/lucking/ports/task_execution_reader.py`、`src/lucking/ports/notification_sender.py`、`src/lucking/integrations/task_readers/__init__.py` 和 `src/lucking/task_catalog.py` 中定义规范任务/通知 DTO、Reader 注册入口、Port 与计划任务目录
- [X] T053 [P] [US5] 在 `src/lucking/integrations/task_readers/trading_calendar.py` 中实现交易日历计划运行 Reader 及六态映射
- [X] T054 [P] [US5] 在 `src/lucking/integrations/task_readers/stock_list.py` 中实现股票列表计划运行 Reader 及六态映射
- [X] T055 [P] [US5] 在 `src/lucking/integrations/task_readers/market_data.py` 中实现复权因子、日线、每日基本面、周/月 K 线计划运行 Reader 及六态映射
- [X] T056 [P] [US5] 在 `src/lucking/integrations/task_readers/index_factor.py` 中实现指数技术因子计划运行 Reader 及六态映射
- [X] T057 [P] [US5] 在 `src/lucking/integrations/task_readers/stock_factor.py` 中实现股票技术因子计划运行 Reader 及六态映射
- [X] T058 [P] [US5] 在 `src/lucking/integrations/task_readers/shareholder_data.py` 中实现三类股东数据计划运行 Reader 及六态映射
- [X] T059 [P] [US5] 在 `src/lucking/integrations/task_readers/broker_recommendation.py` 中实现券商金股计划运行 Reader 及六态映射
- [X] T060 [US5] 在 `src/lucking/repositories/workbench/task_summaries.py`、`src/lucking/services/daily_task_summary.py` 中实现业务日期汇总认领、不可变快照、六态计数、通知 attempt 短事务、自动发送资格和原快照补发
- [X] T061 [US5] 在 `src/lucking/integrations/feishu/__init__.py`、`src/lucking/integrations/feishu/notification_sender.py` 和 `tests/contract/memory_notification_sender.py` 中实现 Feishu Adapter 与 Memory Sender，封装签名、20 KB 截断、限流、重试分类、错误映射和脱敏
- [X] T062 [US5] 在 `src/lucking/flows/daily_task_summary.py` 和 `prefect.yaml` 中实现汇总/补发 Flow、每日 20:00 `Asia/Shanghai` Deployment、并发限制 1、原计划时点和可恢复重试

**检查点**：US5 可仅依赖 Memory Sender 完成确定性验收，并能切换 Feishu Adapter 而不修改 Service 或 Flow。

---

## 阶段 7：用户故事 4——查看券商金股与任务执行情况（优先级：P2）

**目标**：用户可按月份/券商查看真实金股，并查看实时任务状态、20:00 快照和通知状态。

**独立测试**：准备六种任务状态和金股数据，确认推荐月份/券商筛选、更新时间、计数、错误摘要与通知状态均可区分且不泄密。

### 测试（先写并确认失败）

- [X] T063 [P] [US4] 在 `tests/unit/test_broker_recommendation_query.py` 中覆盖月份首日校验、推荐月份/券商/股票筛选、稳定分页、更新时间和禁止 Provider 字段
- [X] T064 [P] [US4] 在 `tests/integration/test_workbench_api.py` 中覆盖金股分页、实时任务、历史快照、404、六态计数、通知状态及安全错误摘要
- [X] T065 [P] [US4] 在 `frontend/tests/component/broker-tasks.spec.ts` 中覆盖金股筛选、任务六态、失败和未执行识别、文本标签、非颜色状态表达、安全错误摘要、局部轮询、快照/通知状态和空状态

### 实现

- [X] T066 [P] [US4] 在 `src/lucking/repositories/workbench_queries/broker_recommendations.py` 和 `src/lucking/services/broker_recommendation_query.py` 中实现按推荐月份/券商筛选的既有券商金股只读分页查询与规范股票身份组合
- [X] T067 [US4] 在 `src/lucking/services/daily_task_summary.py` 中实现任务页实时状态与历史快照查询，保留统计时点和最终状态的不同语义
- [X] T068 [P] [US4] 在 `src/lucking/api/routes/broker_recommendations.py` 中实现 `/broker-recommendations` 强类型分页端点
- [X] T069 [US4] 在 `src/lucking/api/routes/task_status.py` 中实现 `/task-status` 与 `/task-summaries/{business_date}` 强类型端点
- [X] T070 [P] [US4] 在 `frontend/src/api/query-keys/broker-recommendations.ts`、`frontend/src/composables/useBrokerRecommendations.ts` 和 `frontend/src/views/BrokerRecommendationsView.vue` 中以月份首日参数实现推荐月份/券商筛选、分页、更新时间和空状态，并替换 US1 未交付状态
- [X] T071 [US4] 在 `frontend/src/api/query-keys/task-status.ts`、`frontend/src/composables/useTaskStatus.ts`、`frontend/src/views/TaskStatusView.vue` 和 `frontend/tests/e2e/broker-tasks.spec.ts` 中实现自停轮询、实时/快照切换、通知失败展示和端到端验收，并断言六种状态均可通过文本和辅助标识识别、失败或未执行任务无需依赖颜色即可判断

**检查点**：US4 的券商金股查询可独立运行；任务页在 US5 完成后联调持久化快照和通知审计。

---

## 阶段 8：完善与横切关注点

**目标**：完成安全、性能、可观测、文档和全量质量门禁。

- [X] T072 [P] 在 `src/lucking/logging.py`、`src/lucking/api/main.py` 和 `README.md` 中补齐 request/summary/task/flow/attempt 关联日志、健康检查、故障排查和安全停止说明
- [X] T073 [P] 在 `tests/integration/test_workbench_security.py` 中验证密码哈希、Cookie、CSRF、webhook、签名密钥、SQL、堆栈和 Provider 原始响应不会进入 API、日志或前端产物
- [X] T074 [P] 在 `tests/integration/test_workbench_performance.py` 和 `frontend/tests/e2e/performance.spec.ts` 中验证 10,000 股票分页、400 日行情、3 秒首屏和 2 秒搜索目标
- [X] T075 在 `tests/contract/test_workbench_openapi.py` 和 `frontend/src/api/generated/` 中重新生成类型，并对最终运行时 `/openapi.json` 与设计契约执行全部路径、operationId、响应状态和稳定业务码枚举的全量一致性验证；同时验证所有公共响应强类型、无 `any`、失败不使用 HTTP 200、无默认 422、分页仅位于 `data.pagination`
- [X] T076 [P] 在 `README.md` 和 `specs/009-investment-workbench/quickstart.md` 中校准安装、账号预置、前后端启动、Prefect 部署、飞书配置、重试补发和视觉验收步骤
- [X] T077 依据 `pyproject.toml`、`frontend/package.json` 和 `specs/009-investment-workbench/quickstart.md` 验证所有 US1 未交付状态均已由真实 API 页面替换，运行 ruff、mypy、pytest、ESLint、Prettier、TypeScript、Vitest、build 与 Playwright，并在 `specs/009-investment-workbench/tasks.md` 中标记全部通过的任务

---

## 依赖与执行顺序

### 阶段依赖

- 阶段 1 无依赖，可立即开始。
- 阶段 2 依赖阶段 1，并阻塞所有用户故事。
- US1、US2、US3、US5 在阶段 2 完成后可并行推进；它们使用按领域拆分的 Repository 和 Reader 文件，不共享实现文件。
- US4 的券商金股部分只依赖阶段 2；任务快照和通知状态联调依赖 US5。
- 阶段 8 在计划交付的用户故事全部完成后执行。

### 用户故事完成顺序

```text
初始化 → 基础能力 ─┬→ US1 工作台 MVP
                  ├→ US2 日历与重要日
                  ├→ US3 股票、行情与自选
                  └→ US5 每日汇总通知 → US4 任务状态联调
                       基础能力 ─────────→ US4 券商金股
```

### 故事内顺序

- 测试任务必须先执行并确认能在缺少实现时失败。
- Repository/Port 先于 Service，Service 先于路由/Flow，API 先于前端联调。
- 修改同一文件的任务按编号顺序执行；标记 `[P]` 的任务仅在不发生文件冲突时并行。
- 每个故事在检查点通过后再视为完成，不以页面空壳或静态业务假数据替代验收。

## 并行执行示例

### US1

```text
并行：T022 会话 Store 单测；T023 登录/工作台组件测试
随后：T024 认证页面；并行 T025 导航；最后 T026、T027
```

### US2

```text
并行：T028 Service 单测；T029 Repository 集成测试；T030 前端组件测试
随后：T031 → T032；并行 T033；最后 T034 → T035
```

### US3

```text
并行：T036 ClickHouse、T037 股票 Service、T038 自选 Service、T039 MySQL 契约测试
随后：T040 → T041；T042 → T043；并行 T044～T048；最后 T049
```

### US5

```text
并行：T050 目录/Reader 契约、T051 通知/汇总/Flow 测试
随后：T052 定义 Port/目录；并行 T053～T059 七个领域 Reader；最后 T060 → T061 → T062
```

### US4

```text
并行：T063 后端查询单测、T064 API 集成测试、T065 前端组件测试
并行实现：T066 金股查询、T068 金股路由、T070 金股页面
任务状态链路：US5 → T067 → T069 → T071
```

## 实施策略

### MVP 优先

1. 完成阶段 1 和阶段 2。
2. 完成 US1，交付可登录、可改密、可导航且具有规范空状态的工作台。
3. 执行 US1 检查点并演示，不等待其他数据页面完成。

### 增量交付

1. US2 增加交易日历与个人重要日。
2. US3 增加真实股票、行情和自选能力。
3. US5 建立每日 20:00 汇总通知闭环。
4. US4 增加券商金股和完整任务运营视图。
5. 阶段 8 完成全量门禁后发布。

## 任务追溯摘要

- US1：FR-001～FR-003，NFR-001、NFR-002、NFR-004、NFR-005，SC-001。
- US2：FR-004～FR-005、FR-014，SC-002。
- US3：FR-006～FR-008、FR-014，ED-001、ED-003～ED-004，SC-003。
- US4：FR-009～FR-010、FR-014～FR-015，SC-005、SC-007。
- US5：FR-011～FR-016，ED-002～ED-004，NFR-003～NFR-004，SC-004～SC-006。
- 阶段 2/8：宪章 I～VII、统一公共 API 响应、MySQL 物理治理、安全和质量门禁。

## Phase 9: Convergence

- [X] T078 CRITICAL 将 `src/lucking/api/routes/stocks.py`、`watchlists.py`、`task_status.py` 中的事务与领域逻辑下沉至计划指定 Repository/Service，并用具体 Pydantic DTO 替换 `dict[str, Any]`，重新对齐运行时 OpenAPI、统一响应信封及生成客户端 per Constitution II/VII (contradicts)
- [X] T079 CRITICAL 为日历、股票行情、自选、金股、任务汇总、飞书通知新增失败优先的契约、单元、MySQL/ClickHouse 集成、组件及关键 E2E 测试，并证明受影响静态检查、类型检查和构建通过 per Constitution III (contradicts)
- [X] T080 CRITICAL 建立规范任务目录和七个领域 `TaskExecutionReader`，从权威运行表将真实记录归一为唯一的 SUCCEEDED/PARTIAL/FAILED/RUNNING/UNKNOWN/NOT_RUN 类别并替换固定 `NOT_RUN` 快照 per FR-011/FR-012, US5/AC1 (partial)
- [X] T081 实现通知 Port、飞书签名与 20 KB 截断、限流/可恢复重试分类、notification attempt 短事务、成功幂等、失败审计和基于原快照的人工补发 per FR-013, US5/AC3-4 (partial)
- [X] T082 完成自选分组重命名和删除、成员股票选择、稳定排序、容量限制、确认交互、缓存失效及跨用户 404 契约 per FR-008, US3/AC3 (partial)
- [X] T083 实现股票查询 Repository/Service 的 CURRENT/STALE/MISSING 判定、120/400 日稳定行情、停牌缺口、Decimal 字符串、更新时间、文本摘要及亮暗可访问图表 per FR-007, US3/AC2-4 (partial)
- [X] T084 将券商金股查询移入计划指定 Repository/Service，补齐月份首日、券商/股票筛选、稳定分页、更新时间、强类型 DTO 与前端分页空状态 per FR-009, US4/AC2 (partial)
- [X] T085 完成实时任务状态与历史快照的不同语义、时间/数量/安全错误摘要、通知状态、实时/快照切换和终态自停轮询 per FR-010/FR-014, US4/AC1-3 (partial)
- [X] T086 为重要日补齐编辑入口、字段错误映射、重复冲突反馈、刷新持久化及删除不影响交易日的跨用户 E2E per FR-005, US2/AC3 (partial)
- [X] T087 增加日历、股票、自选、金股和任务状态组件/E2E，并验证 10,000 股票分页、400 日行情、2 秒搜索、3 秒首屏及六态非颜色表达 per SC-002/SC-003/SC-007 (missing)
- [X] T088 为 request/summary/task/flow/attempt 增加结构化关联日志、健康检查、失败上下文和 README/quickstart 的启动、排障、安全停止说明 per FR-015, Constitution V (missing)
- [X] T089 增加安全回归测试，证明 webhook、签名密钥、密码哈希、SQL、堆栈和 Provider 原始响应不会进入 API、日志或前端产物 per FR-016/SC-006 (partial)
- [X] T090 以最终运行时 OpenAPI 重新生成 `frontend/src/api/generated/`，禁止手写 `any`/无约束字典并校验全部路径、operationId、状态码、业务码和分页位置 per plan: generated API client (partial)
- [X] T091 使用固定计划时点和 Memory Sender 验证 20:00 汇总、20:05 前通知、失败后 5 分钟内可查询、并发幂等和补发不重算 per SC-005 (missing)
- [X] T092 为股票、自选、金股和任务页面补齐 Vue Query query-key/composable、缓存失效、字段错误映射与局部自停轮询 per plan: frontend query architecture (partial)
- [X] T093 更新工作台真实交付状态，确认所有占位页均已替换且无静态业务假数据，并运行 ruff、mypy、pytest、ESLint、Prettier、TypeScript、Vitest、build 与 Playwright 全量门禁 per FR-003/FR-014 (partial)

## Phase 10: Convergence

- [X] T094 CRITICAL 补齐 `frontend/tests/component/calendar.spec.ts` 的重要日表单校验、刷新保留、字段错误和重复冲突场景，并审计所有已勾选任务的测试证据后纠正实现而非跳过门禁 per Constitution III, T030 (contradicts)
- [X] T095 CRITICAL 将股票、自选和任务公共 API 的事务/领域逻辑迁移至计划指定 Repository/Service，以具体 Pydantic DTO 消除 `dict[str, Any]` 并重新验证统一响应和运行时 OpenAPI per Constitution II/VII (contradicts)
- [X] T096 为日历补齐重要日编辑交互、更新 Mutation、冲突反馈、刷新持久化和跨用户 E2E，确保声明的 CRUD 与实际页面一致 per FR-005, T034 (partial)
- [X] T097 CRITICAL 用七领域 Reader 替换固定 `NOT_RUN` 目录，完成六态唯一归类、不可变快照、飞书签名、attempt 审计、成功幂等、失败重试和原快照补发 per FR-011/FR-012/FR-013 (partial)
- [X] T098 完成股票/行情/自选/金股/任务的 Repository、Service、强类型 API、真实状态页面以及组件、MySQL/ClickHouse 集成和关键 E2E 验收 per US3/US4 acceptance (missing)
- [X] T099 将日历、股票、自选、金股和任务页面统一迁移到 Vue Query query-key/composable，补齐 Mutation 缓存失效、字段错误映射和终态自停轮询 per plan: frontend query architecture (partial)
- [X] T100 增加 request/summary/task/flow/attempt 结构化关联日志、健康检查、运行排障/安全停止文档及秘密不进入 API、日志和前端产物的自动化回归 per Constitution V, SC-006 (missing)
- [X] T101 完成 T035～T100 全部未勾选任务后执行运行时契约、ruff、mypy、pytest、ESLint、Prettier、TypeScript、Vitest、build、Playwright 和性能门禁，并只对有通过证据的任务标记完成 per T035-T100 (partial)

## Phase 11: Convergence

- [X] T102 CRITICAL 将 `src/lucking/api/routes/calendar.py`、`stocks.py`、`task_status.py` 的市场、行情、任务、汇总和通知状态改为设计契约规定的强类型枚举，补齐运行时 OpenAPI 组件 Schema 与 `contracts/openapi.yaml` 的等价校验，并重新生成和验证 `frontend/src/api/generated/` per Constitution II/VII, plan: OpenAPI contract (contradicts)
- [X] T103 更新 `frontend/src/app/navigation.ts`、`frontend/src/views/DashboardView.vue` 和登录/工作台组件及 E2E 测试，将已实现业务入口标记为真实交付，并以任务状态 API 替换“等待任务状态能力交付”的占位摘要 per FR-003, US1/AC2-3, SC-001 (partial)
- [X] T104 在 `frontend/src/views/TaskStatusView.vue` 展示每项任务的开始/完成时间，并为实时状态和 20:00 快照提供整体六态计数，补充组件与 Playwright 验收 per FR-010, US4/AC1 (partial)
- [X] T105 统一 `TaskExecutionStatus`、任务 Reader、快照模型、OpenAPI、生成类型和前端状态展示，使六态与 SC-007 的成功、失败、部分完成、运行中、未执行、未知语义一致，并确保失败及未执行任务同时具有非颜色标识、文本标签和安全错误摘要 per SC-007 (contradicts)
- [X] T106 从 `DailyTaskNotificationAttempt` 查询并通过强类型任务汇总 API 暴露脱敏失败原因、尝试次数、最近尝试时间和可恢复重试/补发状态，在任务页展示并验证失败后 5 分钟内可查询 per FR-013, SC-005, US5/AC3 (partial)
- [X] T107 将 `frontend/src/views/CalendarView.vue`、`frontend/src/composables/useBrokerRecommendations.ts` 和 `frontend/src/composables/useTaskStatus.ts` 的默认业务日期/月统一按 `Asia/Shanghai` 日历日计算，增加 00:00～08:00 边界测试以防 UTC 截断回退一天 per FR-011/FR-014, plan: timezone constraint (partial)
- [X] T108 为 `frontend/src/views/WatchlistsView.vue` 的竖向分组排序增加键盘可操作的上移/下移或等价交互、正确方向语义和组件/E2E 验收，同时保留拖放排序 per FR-008, NFR-005 (partial)
