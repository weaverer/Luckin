# Lucking 前端技术栈

## 1. 文档目的

本文档定义 Lucking 第一版前端应用的技术选型、工程结构、开发规范和技术边界。

本文档只描述通用前端技术方案，不包含业务模块、页面功能和业务流程设计。

页面布局、主题色和组件视觉规范见 [前端 UI 设计与开发规范](./frontend-ui-design-guidelines.md)。

## 2. 技术目标

- 使用成熟、稳定且具有良好 TypeScript 支持的 Vue 生态。
- 建立统一的组件、主题、状态和接口访问规范。
- 保持工程结构清晰，便于持续扩展和维护。
- 优先控制第一版的实现复杂度。
- 保证代码质量、可测试性和生产环境可观测性。

## 3. 核心技术栈

| 分类 | 技术 | 用途 |
| --- | --- | --- |
| 前端框架 | Vue 3 | 使用 Composition API 构建前端应用 |
| 开发语言 | TypeScript | 提供静态类型检查 |
| 构建工具 | Vite | 本地开发、构建和环境变量管理 |
| UI 组件库 | PrimeVue 4 | 提供通用界面组件 |
| 图标库 | PrimeIcons | 提供与 PrimeVue 配套的图标 |
| 路由 | Vue Router | 页面路由、导航守卫和路由懒加载 |
| 客户端状态 | Pinia | 管理用户会话、主题和界面状态 |
| 服务端状态 | TanStack Vue Query | 管理接口数据、缓存、刷新和轮询 |
| HTTP Client | Axios | 访问 REST API，统一处理请求和响应 |
| 图表 | ECharts + vue-echarts | 构建数据可视化组件 |
| 单元测试 | Vitest | 测试工具函数、组合式函数和状态逻辑 |
| 组件测试 | Vue Test Utils | 测试 Vue 组件行为 |
| 端到端测试 | Playwright | 验证关键页面流程 |
| 代码检查 | ESLint | 检查代码质量和潜在错误 |
| 代码格式化 | Prettier | 统一代码格式 |
| 包管理器 | pnpm | 管理前端依赖和锁文件 |

## 4. 基础架构

第一版采用单页应用架构：

```text
Browser
   │
   │ HTTPS / REST / JSON
   ▼
Frontend Application
   │
   │ REST API
   ▼
Backend API
```

第一版不引入：

- WebSocket
- Server-Sent Events（SSE）
- 服务端渲染（SSR）
- 微前端
- 离线优先架构

需要自动刷新的数据统一通过 Vue Query 轮询实现。轮询频率由具体查询配置，并在不再需要刷新时停止。

## 5. Vue 开发规范

- 统一使用 Vue 3 Composition API。
- 单文件组件统一使用 `<script setup lang="ts">`。
- 开启 TypeScript 严格模式。
- 组件的 Props、Emits 和公开方法必须声明类型。
- 可复用状态逻辑封装为 Composable。
- 页面路由使用动态导入，避免全部代码进入首屏包。
- 不在模板中编写复杂的数据转换和业务判断。
- 避免使用 `any`；无法确定的数据先使用 `unknown` 并进行类型收窄。

## 6. PrimeVue 使用方案

### 6.1 主题模式

采用 PrimeVue 4 Styled Mode，以内置主题为基础，通过 Design Token 完成项目级定制。

主题规范：

- 颜色、字号、圆角、间距和阴影通过统一 Token 管理。
- 支持亮色和暗色主题。
- 不在业务组件中直接写死主题颜色。
- 优先使用 PrimeVue 提供的主题 API。
- 避免直接覆盖 PrimeVue 内部 `.p-*` 样式类。
- 全局样式和组件样式应保持明确的层级关系。

### 6.2 组件封装

通用能力可以在 PrimeVue 组件之上进行轻量封装，例如：

- 统一表格默认属性。
- 统一对话框尺寸和底部操作区。
- 统一表单字段、校验信息和帮助文本。
- 统一空状态、加载状态和错误状态。
- 统一确认操作和通知提示。

业务页面中的日期和月份选择统一使用 PrimeVue `DatePicker`，主导航使用 PrimeVue `Sidebar`，状态筛选和自选分组切换使用 PrimeVue `Tabs`；不得回退为浏览器原生日期输入或另建一套导航组件。

封装应保留 PrimeVue 原始能力，避免形成难以升级的重度二次组件库。

### 6.3 样式边界

第一版不默认引入 Tailwind CSS。布局和主题优先使用：

- PrimeVue Design Token
- 项目级 CSS 变量
- CSS Modules 或 Vue Scoped CSS
- 原生 Flexbox 和 CSS Grid

如后续需要完整的原子化 CSS 体系，再单独评估引入 Tailwind CSS 的收益和迁移成本。

## 7. 状态管理

### 7.1 Pinia

Pinia 只管理客户端拥有的全局状态，例如：

- 用户会话
- 权限信息
- 主题模式
- 布局状态
- 用户界面偏好

不应将普通接口查询结果长期复制到 Pinia。

### 7.2 TanStack Vue Query

Vue Query 管理来自后端的服务端状态，包括：

- 请求生命周期
- 加载和错误状态
- 查询缓存
- 请求去重
- 缓存失效
- 后台重新获取
- 分页查询
- Mutation
- 定时轮询

查询键必须集中定义或遵循统一的层级规范：

```ts
['resource']
['resource', 'list', filters]
['resource', 'detail', id]
```

默认配置需要根据项目进行显式调整：

- 设置合理的 `staleTime`。
- 根据接口幂等性配置 `retry`。
- 写操作默认不自动重试。
- 页面失焦时暂停非必要轮询。
- 查询不再活跃或数据状态稳定后停止轮询。

### 7.3 组件本地状态

仅在单个组件内部使用的状态保留在组件中，不放入 Pinia。例如：

- 对话框是否打开
- 当前页签
- 临时输入值
- 局部展开状态

## 8. REST API 访问

### 8.1 Axios 实例

项目只创建一个基础 Axios 实例，并统一配置：

- API Base URL
- 请求超时
- JSON 请求头
- 身份认证信息
- 请求标识
- 响应数据解包
- 统一错误转换
- 401 和 403 处理

组件不得直接创建新的 Axios 实例。

### 8.2 类型生成

后端以 OpenAPI 作为接口契约。前端优先根据 OpenAPI 自动生成：

- 请求参数类型
- 响应数据类型
- 数据模型
- API Client

生成代码与手写代码分开存放，不直接修改生成文件。

### 8.3 接口约定

除 HTTP 204、二进制和流式接口外，项目拥有的公共 JSON API 必须使用统一顶层结构：

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

- 成功响应使用 HTTP 2xx、`code=0`、空 `message` 和空 `errors`，端点数据放在 `data`。
- 失败响应使用正确的 HTTP 4xx/5xx、稳定的非零业务码和 `data=null`；不得以 HTTP 200
  包装失败。`message` 提供安全的中文摘要，字段级明细放在 `errors`。
- 分页列表的 `data` 固定为 `{items, pagination}`；`pagination` 至少包含 `limit`、
  `offset`、`total`、`has_more`，分页字段不得出现在顶层。
- `request_id` 用于关联请求、日志和响应；`timestamp` 使用 UTC ISO 8601，前端只在展示层
  转换时区。
- 排序、筛选、枚举、可空字段、错误明细和具体业务码必须由 OpenAPI 强类型模型定义，
  前端不得以 `any` 绕过契约。

Axios 响应拦截器必须先结合 HTTP 状态和业务 `code` 完成统一错误转换，再向业务层返回
`data`。页面组件不得自行解析协议层字段，也不得为单个接口发明不同的响应或分页结构。

## 9. ECharts 使用方案

- 使用 `echarts` 和 `vue-echarts`，不使用其他图表体系。
- 图表按需引入组件和渲染器，控制构建体积。
- 图表配置与数据转换逻辑分离。
- 公共颜色、字号和提示框样式与全局主题保持一致。
- 图表组件正确处理容器尺寸变化和实例销毁。
- 大数据集合避免使用深层响应式对象，可使用 `shallowRef`。
- 高频更新优先采用增量更新，避免重复创建图表实例。

PrimeVue 负责通用 UI，ECharts 负责数据可视化，两者不相互替代。

## 10. 推荐工程结构

```text
frontend/
├── public/
├── src/
│   ├── app/
│   │   ├── layouts/
│   │   ├── providers/
│   │   └── router/
│   ├── assets/
│   ├── components/
│   │   ├── common/
│   │   └── charts/
│   ├── composables/
│   ├── api/
│   │   ├── client/
│   │   ├── generated/
│   │   └── query-keys/
│   ├── stores/
│   ├── styles/
│   ├── types/
│   ├── utils/
│   ├── views/
│   ├── App.vue
│   └── main.ts
├── tests/
│   ├── unit/
│   └── e2e/
├── .env.example
├── eslint.config.js
├── package.json
├── playwright.config.ts
├── tsconfig.json
├── vite.config.ts
└── vitest.config.ts
```

随着工程增长，可以在不改变基础技术栈的前提下，将代码进一步调整为领域化目录。

## 11. 环境变量

Vite 客户端环境变量必须使用 `VITE_` 前缀，例如：

```dotenv
VITE_APP_TITLE=Lucking
VITE_API_BASE_URL=/api
```

安全要求：

- 不在前端环境变量中存放数据库密码、API 私钥或其他服务端秘密。
- `.env.example` 只保存示例值。
- 本机真实配置不提交到 Git。
- 所有前端环境变量都应视为用户可见信息。

## 12. 代码质量

### 12.1 必须执行的检查

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

建议将以上命令加入持续集成流程。

### 12.2 Git 提交前检查

可以使用 `lint-staged` 配合 Git Hooks，对暂存文件执行：

- ESLint
- Prettier
- 相关单元测试

Git Hooks 不应替代 CI 中的完整检查。

## 13. 测试策略

测试采用分层策略：

### 单元测试

重点覆盖：

- 工具函数
- 数据转换
- Composable
- Pinia Store
- 查询参数构造

### 组件测试

重点覆盖：

- 用户交互
- Props 和 Emits
- 加载、错误和空状态
- 表单校验
- 条件渲染

### 端到端测试

使用 Playwright 验证最关键的页面访问和交互路径。端到端测试数量保持精简，避免大量重复覆盖单元测试已经验证的逻辑。

## 14. 性能要求

- 所有页面路由默认懒加载。
- PrimeVue 和 ECharts 均采用按需导入。
- 大型组件可以进一步异步加载。
- 列表使用服务端分页；大量数据使用虚拟滚动。
- 避免在 Pinia 中保存大型响应式数据集合。
- 避免重复请求和无意义的高频轮询。
- 构建阶段检查产物体积和代码分包结果。
- 静态资源使用长期缓存和内容哈希。

## 15. 安全要求

- 前端权限控制只用于界面展示，后端必须执行最终权限校验。
- 不使用 `v-html` 渲染未经可信处理的内容。
- 身份凭证优先使用安全、受保护的 Cookie 方案。
- 如使用 Cookie 认证，需要配套 CSRF 防护。
- 不在日志、通知和错误页面中展示敏感数据。
- 对用户输入和服务端返回的富文本进行安全处理。
- 生产环境通过 HTTPS 提供服务。

## 16. 可访问性

- 表单控件必须具有关联标签和明确的错误信息。
- 所有交互操作必须可以使用键盘完成。
- 不使用颜色作为表达状态的唯一方式。
- 保证文本和背景具有足够对比度。
- 对图标按钮提供可访问名称。
- 使用 PrimeVue 组件时保留其默认语义和 ARIA 属性。

## 17. 构建与部署

Vite 负责生成前端静态资源。生产部署建议：

```text
Browser
   │
   ▼
Reverse Proxy
   ├── /        -> Frontend static files
   └── /api     -> Backend API
```

采用同域反向代理可以简化跨域和认证配置。

部署要求：

- SPA 路由回退到 `index.html`。
- 静态资源启用压缩和缓存。
- `index.html` 使用较短缓存。
- 带内容哈希的资源使用长期缓存。
- 前后端分别提供版本号或构建标识。

## 18. 依赖管理

- 使用 pnpm 和 `pnpm-lock.yaml`。
- 应用代码提交锁文件。
- 定期升级依赖，不进行无验证的大版本升级。
- PrimeVue 大版本升级前检查迁移指南。
- 自动化依赖升级必须通过类型检查、测试和生产构建。
- 删除未使用的依赖，避免同时引入功能重复的库。

## 19. 第一版技术决策摘要

```text
Framework       Vue 3
Language        TypeScript
Build Tool      Vite
UI              PrimeVue 4 + PrimeIcons
Router          Vue Router
Client State    Pinia
Server State    TanStack Vue Query
HTTP            Axios + REST API
Charts          ECharts + vue-echarts
Unit Test       Vitest + Vue Test Utils
E2E Test        Playwright
Quality         ESLint + Prettier
Package Manager pnpm
```

第一版明确使用 REST API 和轮询，不引入 WebSocket 或 SSE。
