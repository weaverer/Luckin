# 技术研究：投资工作台与任务通知

## 1. 应用拓扑与交付边界

**决策**：采用同域部署的 Vue 单页应用和 FastAPI REST API。生产环境由反向代理将 `/`
指向前端静态资源、将 `/api` 指向后端；开发环境由 Vite 代理 `/api`。本期不引入 SSR、
WebSocket、SSE、微前端或单独的 BFF 服务。

**理由**：仓库宪章已指定 FastAPI/OpenAPI 为前后端唯一接口契约，
`docs/frontend-technology-stack.md` 已确定 Vue 3 + TypeScript + Vite 的单页应用结构和同域部署。
同域 Cookie 认证和 CSRF 防护比跨域凭据配置更简单，也满足内部工作台的刷新频率。

**替代方案**：SSR 会引入第二套服务端渲染生命周期；WebSocket/SSE 对每日任务状态并非必要，
Vue Query 轮询可以满足当前实时性；微前端对单团队首版没有收益。

## 2. 认证、会话与账号生命周期

**决策**：账号存储在 MySQL，密码使用 Argon2id 哈希；管理员通过后端管理命令预置账号。
登录后生成高熵不透明会话标识，只将会话状态放在 Redis，浏览器使用
`HttpOnly + Secure(生产) + SameSite=Lax` Cookie。会话采用 30 分钟空闲、8 小时绝对有效期；
退出删除当前会话，修改密码后撤销该用户全部会话。所有写请求要求同源校验和 CSRF Token。
会话、Cookie、每日汇总时区、飞书 webhook 和可选签名密钥统一由 `src/lucking/config.py` 的
强类型服务端配置承载；秘密字段使用 `SecretStr`，生产环境必须启用 Secure Cookie，且任何
`FEISHU_*` 配置不得导出为前端环境变量。

**理由**：Redis 符合宪章规定的临时状态职责，可即时撤销会话；不透明会话避免在浏览器保存
可长期使用的身份声明。`pwdlib[argon2]` 已在项目依赖中。修改密码撤销全部会话可降低凭据泄露风险。

**替代方案**：纯 JWT 无服务器状态，但退出和改密后的即时撤销需要额外版本检查；数据库会话会把
高频临时状态放入 MySQL；浏览器本地存储 Token 更容易受到脚本读取影响。

## 3. 前端工程与视觉规范

**决策**：严格采用 `docs/frontend-technology-stack.md` 和
`docs/frontend-ui-design-guidelines.md`：Vue 3 Composition API、TypeScript strict、Vite、
PrimeVue 4 Styled Mode、PrimeIcons、Vue Router、Pinia、TanStack Vue Query、Axios、
ECharts、Vitest、Vue Test Utils、Playwright 和 pnpm。实现“月石鎏金”亮暗主题，全部颜色、
间距和图表样式从统一 Token 读取；页面遵循浮动侧边栏、单一 `h1`、服务端分页、规范加载/空/错误状态。

**理由**：这些文档是项目已批准的前端事实来源。Pinia 仅保存会话、主题和布局状态，
Vue Query 管理 API 数据，能避免服务器状态双写。状态同时使用文字/图标，不只依赖颜色。

**替代方案**：Tailwind、第二套组件库和第二套图表库均会扩大样式与依赖面；静态模拟数据不能满足
已澄清的真实数据验收要求。

## 4. API 契约与类型生成

**决策**：所有浏览器能力通过版本化 `/api/v1` REST API 暴露，FastAPI OpenAPI 是唯一事实来源；
前端生成客户端放在 `frontend/src/api/generated/`，手写 Axios 封装、查询键和组合式函数分别存放。
所有有响应体的接口统一使用 `code/message/data/errors/request_id/timestamp`：HTTP 状态表达协议结果，
整数 `code` 表达业务结果；成功码为 0，失败码非零。分页固定封装在 `data={items,pagination}`，
`timestamp` 按宪章使用 UTC ISO 8601。Decimal 使用字符串传输。FastAPI 请求校验统一映射为
HTTP 400 与稳定业务码 `200001/200002/200003`，受保护端点、写端点和所有端点分别显式声明
统一 401、403、500，禁止
默认 422 或其他框架错误结构绕过信封。

稳定错误码在 OpenAPI `BusinessErrorCode` 中集中登记，并按 HTTP 状态映射为：认证/会话
`100001/100002`→401、CSRF `100003`→403、校验 `200001/200002/200003`→400、资源
`300001`→404、冲突 `400001`～`400004`→409、限流 `500001`→429、依赖不可用
`500002`→503、未预期错误 `900001`→500。

**理由**：与宪章和前端技术规范一致，统一响应让 Axios、Vue Query 和页面错误状态只处理一种协议；
业务码与 HTTP 状态分层便于前端逻辑、监控和日志关联；生成类型可减少前后端字段漂移。

**替代方案**：裸返回不同 DTO 会让组件重复判断协议；总是返回 HTTP 200 会破坏缓存、监控和标准错误语义；
GraphQL 会引入新的契约和运行时；组件直接调用 Axios 会破坏边界。

## 5. 业务数据读取边界

**决策**：API 层只调用项目 Service/Query Service，不直接调用供应商：交易日历、股票主数据和券商金股
读取现有 MySQL Repository；日线行情读取现有 ClickHouse `daily_quote`，按股票和日期范围稳定排序；
个人重要日、自选和账号归 MySQL。前端不直接接触数据库或任何第三方数据源。工作台 Repository
按用户、重要日、自选、任务汇总及日历/股票/金股查询拆分文件，避免并行故事共享一个聚合文件。

**理由**：复用现有供应商无关模型和身份 `stock_id`，保持数据所有权不变。行情是分析型事实，继续由
ClickHouse 拥有；个人配置和权限数据需要事务、唯一约束和一致写入，归 MySQL。

**替代方案**：为页面重新调用 Tushare 会绕过既有同步质量门禁；复制行情到 MySQL 会产生双重事实；
将个人配置放 Redis 无法提供可靠生命周期和审计。

## 6. 每日任务目录与状态归一化

**决策**：创建项目拥有的 `ScheduledTaskCatalog`，以 `schedule_slug`、展示名、Cron、业务时区和
任务类型描述 `prefect.yaml` 中所有有计划的 Deployment；契约测试保证目录与 `prefect.yaml` 对齐。
每个同步领域提供 `TaskExecutionReader` 适配器，将既有运行表归一为
`SUCCEEDED / PARTIAL / FAILED / RUNNING / UNKNOWN / NOT_RUN`。某业务日期只纳入该日 20:00 前
按目录应触发的计划运行；历史回补和人工运行不进入每日汇总。

**理由**：既有同步领域拥有不同运行表和状态机，统一读取端口可以避免汇总服务依赖各表的专有字段；
显式目录和对齐测试可防止新增 Deployment 后静默漏报。20:00 后才应运行的任务不应被误判未执行。

**替代方案**：只查询 Prefect API 无法获得项目业务发布与数据质量终态；只扫描数据库无法知道未执行任务；
在每个 Flow 内直接发通知会产生多条消息且无法给出全局缺口。

## 7. 汇总快照、并发与幂等

**决策**：20:00 的 Prefect Flow 调用 `DailyTaskSummaryService`，在 MySQL 以业务日期唯一认领
`daily_task_summary`，将该时刻每项任务状态保存到 `daily_task_summary_item`。相同业务日期并发或重跑
复用同一汇总；成功通知后默认不再发送，只有显式补发命令创建新的通知尝试。页面的“今日任务”可查询
实时归一状态，历史日期读取不可变汇总快照。

**理由**：持久化快照可以证明通知内容与 20:00 时点一致；数据库唯一约束和事务认领避免重复成功通知；
实时页与历史审计分别满足运营和追溯需要。

**替代方案**：只在内存拼接后通知无法审计；只保存汇总计数无法定位异常任务；用 Redis 作为唯一汇总存储
不符合持久审计需求。

## 8. 飞书自定义机器人适配器

**决策**：定义供应商无关 `NotificationSender` Port 和规范通知 DTO；Feishu Adapter 使用服务端
`SecretStr` 配置 webhook，可选配置签名密钥并按官方 HMAC-SHA256 规则签名，发送小于 20 KB 的
结构化消息卡片。适配器负责 HTTP、专有响应码、限流映射和脱敏；领域层只处理
`DELIVERED / RETRYABLE_FAILURE / PERMANENT_FAILURE`。

**理由**：飞书官方将 webhook 视为需要妥善保护的秘密，并支持关键词、IP 白名单和签名校验；
自定义机器人当前限制为单租户单机器人 100 次/分钟、5 次/秒，请求体不超过 20 KB。
本功能每日一次通知远低于限制，但仍需正确映射 429/限流响应。

**替代方案**：把飞书 JSON 写入领域服务会泄漏供应商协议；应用机器人 OpenAPI 需要额外应用权限，
与用户提供的群自定义机器人 webhook 不一致；直接把 webhook 写进仓库违反宪章。

**参考**：[飞书自定义机器人使用指南](https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN?lang=zh-CN)。

## 9. 通知重试与补发

**决策**：首次发送失败时，Prefect 对可恢复网络、429 和 5xx 错误按 30/120/300 秒最多重试 3 次；
鉴权、安全设置、请求体或永久业务错误不重试。每次尝试独立写入 `daily_task_notification_attempt`，
只保存安全错误分类、摘要和响应状态，不保存 webhook、签名、请求体或原始响应。补发复用原汇总快照，
不重新计算历史状态。

**理由**：与项目已有外部依赖失败策略一致；补发旧快照可保证“发送了什么”可审计，不因后续任务终态改变
而篡改 20:00 报告。

**替代方案**：无限重试会制造通知风暴；补发时重新统计会改变历史语义；保存完整响应可能包含敏感内容。

## 10. 可观测性与测试策略

**决策**：API 请求使用 `request_id`，日志关联用户业务 ID（不含用户名/密码）、会话摘要、summary ID、
task key、flow/run ID 和通知 attempt ID。后端覆盖 Service 单元测试、Repository/MySQL/Redis/ClickHouse
集成测试、飞书 Memory/HTTP Adapter 契约测试和 OpenAPI 快照；前端覆盖 composable/store 单元测试、
组件状态测试及登录—日历—自选—行情—任务的精简 Playwright 流程。

**理由**：满足宪章的可追溯、外部适配器替代性、安全和质量门禁；分层测试能定位领域、存储、协议和 UI 问题。

**替代方案**：仅端到端测试反馈慢且难定位；仅 mock HTTP 无法验证实际表约束、Cookie/CSRF 和 ClickHouse 查询。

## 11. MySQL 物理治理

**决策**：本功能新增七张项目业务表：`app_user`、`important_date`、`watchlist_group`、
`watchlist_member`、`daily_task_summary`、`daily_task_summary_item`、
`daily_task_notification_attempt`。全部采用 `id BIGINT AUTO_INCREMENT` 主键、数据库维护的
`created_at/updated_at`、业务唯一约束和完整中文表/列注释，不申请宪章例外。

**理由**：完全符合宪章 VI。即使 `watchlist_member` 是关联表，保留统一代理主键可以减少 ORM、审计和
后续扩展差异，业务唯一性仍由 `(group_id, stock_id)` 保证。

**替代方案**：复合主键虽可申请关联表例外，但当前没有必须偏离统一治理的收益。
