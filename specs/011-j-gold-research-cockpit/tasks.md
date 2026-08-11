# 任务：J金股研究驾驶舱

**输入**：本目录下的 `spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/openapi.yaml`、`quickstart.md`

**实现说明（2026-08-10）**：本次按“升级现有页面”的产品要求复用
`BrokerRecommendationsView.vue` 作为 J金股路由入口；部分同层测试合并到
`test_j_gold_research.py`、`test_j_gold_openapi.py`、`test_j_gold_api.py`、
`broker-tasks.spec.ts` 与 `j-gold-performance.spec.ts`，避免只按模块名拆分重复夹具。
当前仓库没有可验证行业映射，行业能力按规格保留并明确降级，不使用模拟分类补齐。

## 阶段 1：初始化

- [X] T001 在 `src/lucking/api/routes/j_gold.py`、`src/lucking/services/j_gold_research.py`、`src/lucking/repositories/workbench_queries/j_gold.py` 建立后端入口
- [X] T002 [P] 在 `frontend/src/views/JGoldResearchView.vue`、`frontend/src/composables/useJGoldResearch.ts`、`frontend/src/components/j-gold/` 建立前端入口
- [X] T003 [P] 在 `tests/unit/`、`tests/contract/`、`tests/integration/`、`tests/e2e/` 建立 J金股测试夹具入口

## 阶段 2：基础能力

- [X] T004 在 `tests/contract/test_j_gold_openapi.py` 验证 OpenAPI 端点、强类型 DTO、统一六字段信封、分页、request_id 和 UTC 时间戳
- [X] T005 在 `src/lucking/api/responses.py`、`src/lucking/api/errors.py` 复用统一成功/失败响应和 HTTP/业务码映射
- [X] T006 [P] 在 `src/lucking/ports/j_gold_data.py` 定义推荐、行业、行情、交易日和基准的供应商无关读取端口
- [X] T007 [P] 在 `src/lucking/models/j_gold.py` 定义查询上下文、质量状态和研究结果 DTO
- [X] T008 [P] 在 `tests/contract/test_j_gold_providers.py` 为数据端口增加 Memory 替代实现，验证供应商字段不泄露
- [X] T009 在 `src/lucking/repositories/workbench_queries/j_gold.py` 实现既有 MySQL/ClickHouse 查询适配，明确后复权和交易日窗口
- [X] T010 在 `src/lucking/services/j_gold_research.py` 实现月份回退、筛选校验、质量状态合并和模块级错误隔离
- [X] T011 [P] 在 `tests/unit/test_j_gold_context.py` 覆盖月份、券商、行业筛选及空/延迟/不足状态

## 阶段 3：用户故事 1——按月份掌握金股全貌（P1，MVP）

**独立测试**：用完整月份、上月缺失、行情延迟、基准缺失和筛选无结果夹具，验证所有模块使用同一范围。

- [X] T012 [P] [US1] 在 `tests/unit/test_j_gold_overview_metrics.py` 覆盖去重、新晋、高共识、升温、60 日突破和 20 日超额口径
- [X] T013 [P] [US1] 在 `tests/integration/test_j_gold_overview_queries.py` 验证推荐、股票、行业、行情和基准聚合及质量状态
- [X] T014 [P] [US1] 在 `tests/contract/test_j_gold_research_api.py` 验证 `GET /j-gold/research` 参数、分页和模块状态
- [X] T015 [P] [US1] 在 `frontend/tests/component/j-gold-overview.spec.ts` 覆盖首次加载、空结果、重试、清除筛选和指标定义
- [X] T016 [US1] 在 `src/lucking/services/j_gold_research.py` 实现六项总览指标及统一筛选上下文
- [X] T017 [US1] 在 `src/lucking/api/routes/j_gold.py` 实现 `GET /j-gold/research` 强类型响应
- [X] T018 [US1] 在 `frontend/src/composables/useJGoldResearch.ts` 实现筛选、缓存、刷新锁、局部重试和分页上下文
- [X] T019 [US1] 在 `frontend/src/views/JGoldResearchView.vue`、`frontend/src/components/j-gold/OverviewMetrics.vue` 实现标题、筛选栏、指标卡和来源状态
- [X] T020 [US1] 在 `frontend/tests/e2e/j-gold-overview.spec.ts` 验证完整月份、筛选联动和首屏状态

## 阶段 4：用户故事 2——机会雷达与股票详情（P1）

**独立测试**：用多券商重复推荐、停牌、上市不足窗口和数据不足夹具，验证去重、分页、状态、详情和自选。

- [X] T021 [P] [US2] 在 `tests/unit/test_j_gold_opportunity_radar.py` 覆盖去重、券商计数、连续入选、排序、状态和评分输入
- [X] T022 [P] [US2] 在 `tests/integration/test_j_gold_stock_detail.py` 验证详情的券商、历史、行业、后复权行情、来源和质量
- [X] T023 [P] [US2] 在 `frontend/tests/component/j-gold-radar.spec.ts` 覆盖排序、筛选、分页、状态文本和下钻
- [X] T024 [P] [US2] 在 `frontend/tests/e2e/j-gold-stock-detail.spec.ts` 验证详情上下文和加入自选无交易行为
- [X] T025 [US2] 在 `src/lucking/services/j_gold_research.py` 实现去重雷达、稳定排序、研究状态和透明多指标评分（共识 30%、热度变化 25%、连续入选 20%、20 日超额 25%；缺失指标按可用项归一化，少于 2 项不评分）
- [X] T026 [US2] 在 `src/lucking/api/routes/j_gold.py` 实现 `GET /j-gold/stocks/{stock_id}` 并复用自选所有权校验
- [X] T027 [US2] 在 `frontend/src/components/j-gold/OpportunityRadar.vue` 实现可排序、筛选、分页表格和事实依据
- [X] T028 [US2] 在 `frontend/src/components/j-gold/StockResearchDrawer.vue` 实现推荐、历史、行情口径、定义和质量下钻
- [X] T029 [US2] 在 `frontend/src/components/j-gold/AddToWatchlistAction.vue` 接入既有自选写入、CSRF 和成功反馈

## 阶段 5：用户故事 3——异动与行业共识（P2）

**独立测试**：用至少 3 个月历史推荐和行业分类夹具，验证触发依据和三类行业计数不混用。

- [X] T030 [P] [US3] 在 `tests/unit/test_j_gold_signals.py` 覆盖新增推荐、3 个月新高、升温、连续、下降、60 日新高和背离
- [X] T031 [P] [US3] 在 `tests/unit/test_j_gold_industry_consensus.py` 覆盖记录数、股票数、券商数、变化和覆盖状态
- [X] T032 [P] [US3] 在 `frontend/tests/component/j-gold-signals-industry.spec.ts` 覆盖依据不足、行业过滤和文本替代
- [X] T033 [US3] 在 `src/lucking/services/j_gold_research.py` 实现异动规则和行业共识聚合，携带区间、规则、时间和质量
- [X] T034 [US3] 在 `src/lucking/api/routes/j_gold.py` 扩展异动和行业共识 DTO，支持模块独立失败
- [X] T035 [US3] 在 `frontend/src/components/j-gold/GoldSignals.vue`、`IndustryConsensus.vue` 实现异动和行业模块

## 阶段 6：用户故事 4——券商能力与市场扩散（P2）

**独立测试**：用完整/缺失历史窗口、基准缺失和样本不足夹具，验证 12/8 个月统计和具体数值。

- [X] T036 [P] [US4] 在 `tests/unit/test_broker_selection_ability.py` 覆盖“券商—股票—推荐月份”样本、收益、覆盖率、20 条最低样本量和等级
- [X] T037 [P] [US4] 在 `tests/unit/test_market_recommendation_diffusion.py` 覆盖 8 个月去重数量、环比、缺失月份和状态
- [X] T038 [P] [US4] 在 `frontend/tests/component/j-gold-broker-diffusion.spec.ts` 覆盖样本不足、基准缺失、月份数值和文本替代
- [X] T039 [US4] 在 `src/lucking/services/j_gold_research.py` 实现 12 个月券商能力和 8 个月市场扩散，按最低样本门槛降级
- [X] T040 [US4] 在 `src/lucking/api/routes/j_gold.py` 扩展券商能力、市场扩散 DTO，返回周期、基准和质量状态
- [X] T041 [US4] 在 `frontend/src/components/j-gold/BrokerAbility.vue`、`MarketDiffusion.vue` 实现两个模块

## 阶段 7：完善与质量门禁

- [X] T042 [P] 在 `frontend/src/styles/` 和 J金股组件样式中完成亮暗主题、三种响应式布局、焦点和 reduced-motion
- [X] T043 [P] 在 `tests/integration/test_j_gold_observability.py` 验证 request_id、查询范围、耗时、模块失败和敏感信息脱敏
- [X] T044 [P] 在 `frontend/tests/component/j-gold-accessibility.spec.ts` 验证标题、标签、键盘、非颜色状态和图表文本替代
- [X] T045 在 `specs/011-j-gold-research-cockpit/quickstart.md` 补充启动、健康检查、口径和数据质量排障步骤
- [X] T046 在仓库根目录执行 `uv run pytest`、前端 `pnpm lint`、`pnpm typecheck`、`pnpm test`、`pnpm build` 和 Playwright
- [X] T047 在 `specs/011-j-gold-research-cockpit/quickstart.md` 执行最终正常、空数据、延迟、部分失败、样本不足、筛选联动和响应式验收
- [X] T048 [P] 在 `frontend/tests/e2e/j-gold-performance.spec.ts` 验证代表性完整月份和正常网络下首屏结构及加载/数据状态在 3 秒内可见
- [X] T049 [P] 在 `tests/unit/test_j_gold_score_and_thresholds.py` 验证评分权重、缺失指标归一化、少于 2 项不评分及券商 20 条样本门槛

## 依赖与执行顺序

阶段 1 → 阶段 2 → US1/US2/US3/US4 → 阶段 7。阶段 2 完成前不得开始用户故事；US1 和 US2 为 MVP，US3/US4 可在共享基础完成后并行。

## 并行机会

T002/T003；T006/T007/T008/T011；US1 的 T012—T015；US2 的 T021—T024；US3 的 T030—T032；US4 的 T036—T038；阶段 7 的 T042—T044 均可在依赖满足后并行。

## MVP 策略

先完成阶段 1—2、US1 和 US2，独立验证“按月份查看总览、机会雷达和可追溯股票详情”；之后增量交付 US3、US4，最后执行横切质量门禁。

## 格式校验

共 49 项任务；所有任务均使用 `- [ ] Txxx` 格式，用户故事任务均带 `[USn]`，并行任务均带 `[P]`，每项均含明确文件路径。

## Phase 8: Convergence

- [ ] T050 CRITICAL：在 `src/lucking/services/j_gold_research.py`、`src/lucking/api/main.py` 和 `tests/integration/test_j_gold_api.py` 为行情、基准及模块失败增加携带 request_id、筛选范围、耗时、失败类别和影响范围的脱敏结构化日志，禁止以静默空集合掩盖查询故障 per Constitution V / NFR-004 (contradicts)
- [ ] T051 CRITICAL：以 FastAPI 生成的 OpenAPI 为唯一契约来源，在 `src/lucking/models/j_gold.py`、`src/lucking/api/routes/j_gold.py`、`frontend/src/composables/useJGoldResearch.ts`、`frontend/src/components/j-gold/` 和 `specs/011-j-gold-research-cockpit/contracts/openapi.yaml` 建立具体研究 DTO/前端类型并移除 `Record<string, unknown>` 与跨层无约束字典 per Constitution II、VII / plan: typed DTO (contradicts)
- [ ] T052 CRITICAL：在 `tests/unit/`、`tests/contract/`、`tests/integration/`、`frontend/tests/component/` 和 `frontend/tests/e2e/` 补齐异动七类规则、模块失败与独立重试、空/延迟/不足状态、筛选联动、三档响应式、键盘和非颜色状态的自动化回归，并执行完整后端与前端质量门禁 per Constitution III / FR-010、FR-016、NFR-003 (missing)
- [ ] T053 在 `src/lucking/ports/j_gold_data.py`、`src/lucking/repositories/workbench_queries/j_gold.py`、`src/lucking/services/j_gold_research.py` 及对应契约/集成测试中接入可替换的规范行业分类能力，保证行业筛选、行业共识、行业升温及分类变更可追溯 per FR-011、FR-015 / US3-AC3 (partial)
- [ ] T054 在 `src/lucking/models/j_gold.py`、`src/lucking/services/j_gold_research.py`、`src/lucking/api/routes/j_gold.py`、`frontend/src/composables/useJGoldResearch.ts` 和各 J金股模块中实现模块级质量封装、异常隔离、局部加载和独立重试，保留其他可验证内容 per FR-016 / research decision 2 (partial)
- [ ] T055 在 `frontend/src/components/j-gold/OverviewMetrics.vue`、`OpportunityRadar.vue`、`StockResearchDrawer.vue` 及后端研究 DTO 中补全可操作的指标定义、统计时间/范围/来源/质量、评分原始值与默认权重、缺失项重归一化说明及研究状态触发依据 per FR-003、FR-008、FR-009、FR-014 / SC-004 (partial)
- [ ] T056 在 `src/lucking/services/j_gold_research.py`、`src/lucking/api/routes/j_gold.py`、`frontend/src/components/j-gold/GoldSignals.vue` 和相应测试中为异动增加事实引用、来源、生成时间、质量说明和可追溯下钻，并让行情类异动使用实际最新行情日期 per FR-010、FR-014 / SC-004 (partial)
- [ ] T057 在 `src/lucking/services/j_gold_research.py`、`src/lucking/api/routes/j_gold.py`、`frontend/src/components/j-gold/BrokerAbility.vue` 及对应测试中展示券商能力实际起止月份、完整结果或分页、等级阈值与计算依据，并在规则无法由已批准规格确认时保持未评级 per FR-012 (partial)
- [ ] T058 在 `frontend/src/composables/useJGoldResearch.ts`、`frontend/src/views/BrokerRecommendationsView.vue`、`frontend/src/components/j-gold/IndustryConsensus.vue` 和 E2E 测试中保留并可恢复行业下钻前后的月份、券商、行业、排序、分页及雷达分类上下文 per US3-AC3 / SC-002 (partial)
- [ ] T059 在 `frontend/src/components/j-gold/MarketDiffusion.vue` 及对应 DTO/测试中为最近 8 个月逐月展示质量状态、完整统计口径、生成时间和缺失影响说明，同时保留图表文本替代 per FR-013、FR-014 (partial)
- [ ] T060 在 `frontend/src/utils/`、`frontend/src/views/BrokerRecommendationsView.vue`、`frontend/src/components/j-gold/StockResearchDrawer.vue` 和相关测试中统一按 `Asia/Shanghai` 格式化展示时间，保留服务端 UTC ISO 8601 原值 per plan: timezone / Constitution technical constraint (partial)
