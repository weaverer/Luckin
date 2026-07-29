# 功能规格：[FEATURE NAME]

> 本规格及其说明必须使用简体中文；代码标识符、命令、协议字段和专有名词可保留英文。

**功能分支**：`[###-feature-name]`

**创建日期**：[DATE]

**状态**：草案

**输入**：用户描述：“$ARGUMENTS”

## 用户场景与测试 *（必填）*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### 用户故事 1 - [简短标题]（优先级：P1）

[Describe this user journey in plain language]

**优先级理由**：[说明价值及其优先级理由]

**独立测试**：[说明如何独立验证该故事及其交付价值]

**验收场景**：

1. **Given** [initial state], **When** [action], **Then** [expected outcome]
2. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### 用户故事 2 - [简短标题]（优先级：P2）

[Describe this user journey in plain language]

**优先级理由**：[说明价值及其优先级理由]

**独立测试**：[说明如何独立验证]

**验收场景**：

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

### 用户故事 3 - [简短标题]（优先级：P3）

[Describe this user journey in plain language]

**优先级理由**：[说明价值及其优先级理由]

**独立测试**：[说明如何独立验证]

**验收场景**：

1. **Given** [initial state], **When** [action], **Then** [expected outcome]

---

[Add more user stories as needed, each with an assigned priority]

### 边界情况

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- What happens when [boundary condition]?
- How does system handle [error scenario]?

## 需求 *（必填）*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### 功能需求

- **FR-001**: System MUST [specific capability, e.g., "allow users to create accounts"]
- **FR-002**: System MUST [specific capability, e.g., "validate email addresses"]
- **FR-003**: Users MUST be able to [key interaction, e.g., "reset their password"]
- **FR-004**: System MUST [data requirement, e.g., "persist user preferences"]
- **FR-005**: System MUST [behavior, e.g., "log all security events"]

*Example of marking unclear requirements:*

- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified - email/password, SSO, OAuth?]
- **FR-007**: System MUST retain user data for [NEEDS CLARIFICATION: retention period not specified]

### 非功能需求 *（适用时必填）*

<!--
  明确与功能相关且可验证的质量约束。至少评估安全与隐私、性能与容量、可靠性与恢复、
  可观测性、可访问性、数据生命周期和运维影响；不适用的类别应写明理由。
-->

- **NFR-001**: 系统必须 [可验证的安全、可靠性、性能或可访问性要求]
- **NFR-002**: 系统必须 [日志、指标、审计或故障恢复要求，且不得泄露敏感信息]

### 外部数据依赖 *（通过第三方 API 获取数据时必填）*

<!--
  按业务需要描述所需数据、时效、质量、失败与降级行为，不指定供应商 SDK 或专有模型。
  明确更换数据供应商时必须保持稳定的业务行为和公共契约，以及供应商能力差异的处理规则。
-->

- **ED-001**: 系统必须从可替换的数据来源获得 [业务数据]，并保持 [业务语义/公共行为] 稳定
- **ED-002**: 当数据源限流、超时、缺失、重复或返回冲突数据时，系统必须 [可验证的行为]
- **ED-003**: 数据来源变更不得要求修改 [受保护的业务流程或公共契约]

### 关键实体 *（功能涉及数据时填写）*

<!--
  对每个实体说明业务身份、唯一性、生命周期，以及创建/更新时间是否具有业务语义。
  本节保持技术无关；具体 MySQL 主键、时间字段、中文注释和例外设计在 plan/data-model 中落实。
-->

- **[Entity 1]**: [What it represents, key attributes without implementation]
- **[Entity 2]**: [What it represents, relationships to other entities]

## 成功标准 *（必填）*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### 可度量结果

- **SC-001**: [Measurable metric, e.g., "Users can complete account creation in under 2 minutes"]
- **SC-002**: [Measurable metric, e.g., "System handles 1000 concurrent users without degradation"]
- **SC-003**: [User satisfaction metric, e.g., "90% of users successfully complete primary task on first attempt"]
- **SC-004**: [Business metric, e.g., "Reduce support tickets related to [X] by 50%"]

## 假设

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- [Assumption about target users, e.g., "Users have stable internet connectivity"]
- [Assumption about scope boundaries, e.g., "Mobile support is out of scope for v1"]
- [Assumption about data/environment, e.g., "Existing authentication system will be reused"]
- [Dependency on existing system/service, e.g., "Requires access to the existing user profile API"]
- [第三方数据能力假设，例如数据时效、字段可得性和可接受的降级范围]
