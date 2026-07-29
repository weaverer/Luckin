# 实施计划：[FEATURE]

**分支**：`[###-feature-name]` | **日期**：[DATE] | **规格**：[link]

**输入**：来自 `/specs/[###-feature-name]/spec.md` 的功能规格

**说明**：本模板由 `/speckit-plan` 填写；该命令定义具体执行流程。

## 摘要

[Extract from feature spec: primary requirement + technical approach from research]

## 技术上下文

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**语言/版本**：[例如 Python 3.12，或“需要澄清”]

**主要依赖**：[例如 FastAPI，或“需要澄清”]

**存储**：[如适用，例如 MySQL、ClickHouse、文件或不适用]

**测试**：[例如 pytest、Vitest、Playwright，或“需要澄清”]

**目标平台**：[例如 Linux Server、浏览器，或“需要澄清”]

**项目类型**：[例如 Web 应用、服务、CLI，或“需要澄清”]

**性能目标**：[领域相关的可度量目标，或“需要澄清”]

**约束**：[领域相关约束，或“需要澄清”]

**规模/范围**：[领域相关规模，或“需要澄清”]

## 宪章检查

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

> 所有项目文档必须使用简体中文（代码标识符、命令、协议字段及专有名词除外）。

- **规格与追溯**：每项设计和任务是否可追溯到需求、用户故事或宪章约束？
- **架构与数据边界**：是否说明组件职责、数据所有权、生命周期、一致性和接口契约？
- **第三方数据源可替换性**：如通过第三方 API 获取数据，是否定义供应商无关接口、
  规范化模型、独立适配器、配置或依赖注入选择方式、错误映射、迁移策略，以及契约测试和
  替代实现或测试替身？业务代码是否完全不依赖供应商 SDK、传输模型和专有字段？
- **测试与质量门禁**：是否定义单元、契约、集成及关键端到端测试，以及适用的静态检查、
  类型检查和构建命令？
- **安全与最小暴露**：是否识别秘密、输入验证、认证授权、网络暴露及破坏性操作风险？
- **可观测与运维**：是否定义结构化日志/指标、健康检查、故障排查和运行文档？
- **MySQL 表结构**：如新建或结构性修改项目拥有的 MySQL 表，是否逐表采用
  `BIGINT AUTO_INCREMENT` 主键、数据库维护的 `created_at/updated_at` 以及中文表/字段
  注释？如不采用，是否属于宪章允许的特殊场景，并在计划和 `data-model.md` 中记录理由、
  替代方案、唯一性、时间语义与迁移影响？
- **简洁性**：新增服务、框架、实时协议或抽象是否必要；复杂度是否在下表说明？

每项必须填写“通过”并给出证据，或填写“不适用”并给出具体理由。存在未获批准的失败项时，
计划不得进入下一阶段。

## 项目结构

### 文档（本功能）

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### 源代码（仓库根目录）
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**结构决策**：[记录选定的结构，并引用上方列出的实际目录]

## 复杂度跟踪

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
