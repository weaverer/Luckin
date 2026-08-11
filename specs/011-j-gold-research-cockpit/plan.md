# 实施计划：J金股研究驾驶舱

**分支**：`011-j-gold-research-cockpit` | **日期**：2026-08-10 | **规格**：[spec.md](spec.md)

## 摘要

将现有按月份/券商查询的券商金股页升级为可追溯的研究驾驶舱。计划复用现有推荐同步结果、股票身份、行业分类、ClickHouse 日线行情、沪深 300 基准、交易日历和自选组合；新增供应商无关的聚合查询能力、统一研究结果 DTO、驾驶舱页面状态和下钻上下文。所有派生指标在查询时携带统计范围、口径、生成时间和质量状态，评分仅用于研究排序。

## 技术上下文

**语言/版本**：Python 3.12；TypeScript；Vue 3。  
**主要依赖**：FastAPI、SQLAlchemy/Alembic、MySQL、ClickHouse、Vue Query、PrimeVue、ECharts、Axios、Vitest、Playwright。  
**存储**：既有 MySQL 事实/身份数据；ClickHouse 日线行情与分析查询；Redis 不新增用途。  
**测试**：pytest、契约测试、Vitest、Vue Test Utils、Playwright、类型检查、lint、构建。  
**目标平台**：Linux 服务端与浏览器；北京时间展示，跨系统时间使用 UTC ISO 8601。  
**项目类型**：受保护的 Web 投资研究工作台。  
**性能目标**：正常网络下 3 秒内显示首屏结构和数据状态；聚合查询结果可分页、可局部加载。  
**约束**：后复权收盘价；成交量/额原始口径；不重做同步任务；不提供交易或投顾；公共 API 使用统一响应信封。  
**规模/范围**：单月推荐、最近 3/8/12 个完整月份、20/60 个交易日窗口；面向现有工作台用户和既有数据量级。

**已确认计算参数**：综合评分由推荐共识 30%、推荐热度变化 25%、连续入选 20%、20 日超额收益 25%组成；缺失指标按可用指标重新归一化，少于 2 项可用指标不评分。券商能力最低有效样本量为 20 条，每条样本是一个“券商—股票—推荐月份”。

## 宪章检查

*GATE：研究前通过；设计后再次通过。*

- **规格与追溯：通过**。用户故事、FR/NFR、成功标准分别映射到查询、聚合、详情、状态和测试；不扩大同步任务范围。
- **架构与数据边界：通过**。推荐事实归现有 MySQL；行情/基准归 ClickHouse；领域聚合位于服务/查询层；前端只消费规范 DTO。
- **第三方数据源可替换性：通过**。本功能不新增外部来源；现有来源通过规范化 Provider 使用。行情、行业和基准若接入替代来源，适配器只输出项目模型，并提供测试替身。
- **测试与质量门禁：通过**。计划包含领域单元、API 契约、数据集成、组件和关键流程 E2E；实现后运行后端测试、前端 lint/typecheck/test/build 和 Playwright。
- **统一公共 API 响应：通过**。契约定义 `code/message/data/errors/request_id/timestamp`、分页位置、HTTP/业务码分离和具体的驾驶舱、股票详情、模块状态 DTO；契约测试覆盖成功/失败结构。
- **安全与最小暴露：通过**。复用现有会话授权；只读分析查询和自选写入执行现有 CSRF/所有权校验；日志不记录原始供应商响应或秘密。
- **可观测与运维：通过**。记录 request_id、筛选范围、查询耗时、模块状态、数据更新时间和失败类别；quickstart 提供健康检查与排障验证。
- **MySQL 表结构：通过**。优先不新增表；若实现需要持久化快照，必须另行更新规格/计划并满足 BIGINT、自维护时间和中文注释要求。本计划默认查询时聚合，不新增持久化快照表。
- **简洁性：通过**。不引入 WebSocket、SSE、后台同步或新基础设施；使用既有 REST、Vue Query 和 ECharts。

## 项目结构

```text
src/lucking/
├── api/routes/                    # J金股只读聚合与详情端点
├── services/                      # 口径校验、聚合和质量状态
├── repositories/workbench_queries/ # MySQL/ClickHouse 规范查询
├── ports/                         # 可替换行情、基准、行业读取接口（如需新增）
└── models/                        # 仅复用既有事实模型，不新增持久化快照
frontend/src/
├── views/JGoldResearchView.vue
├── composables/useJGoldResearch.ts
├── components/j-gold/             # 指标、雷达、异动、行业、券商、扩散模块
├── api/query-keys/j-gold.ts
└── utils/                         # 口径/状态/格式化展示
tests/
├── unit/                          # 领域口径与聚合
├── contract/                      # OpenAPI 与 Provider 替代实现
├── integration/                   # MySQL/ClickHouse 查询和质量状态
└── e2e/                           # 驾驶舱关键流程
```

**结构决策**：采用现有单体后端 + Vue 前端结构；领域聚合不放入 View 或 Router，查询结果不新增独立存储。

## 复杂度跟踪

无宪章违规。新增分析聚合服务是因为六项指标、历史窗口和质量状态必须共享同一统计范围，直接在视图或各端点重复计算会造成口径漂移。
