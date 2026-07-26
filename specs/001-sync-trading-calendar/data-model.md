# 数据模型：交易日历

## 1. 数据所有权

`trading_calendar` 是业务查询使用的供应商无关当前日历，归 MySQL 所有。
Tushare 是 `CN-S` 首期 Provider，Prefect 只负责编排，不拥有业务日历。

本功能不创建市场表、同步配置表、同步执行表或历史版本表：

- 市场代码由代码中的受控值对象校验。
- Provider 由显式 Registry 和配置选择；供应商原始载荷不进入业务表。
- 计划配置版本化保存在 `prefect.yaml`。
- 执行状态写入 JSONL 日志并关联 Prefect Flow Run ID。
- 同一业务键只保留最近一次成功同步的当前值。

## 2. 实体：TradingCalendar

### 2.1 表结构

**表名**：`trading_calendar`

| 字段 | MySQL 类型 | 可空 | 默认值 | 说明 |
|------|------------|------|--------|------|
| `market_code` | `CHAR(4)` ASCII | 否 | 无 | 市场代码，首期仅 `CN-S` |
| `calendar_date` | `DATE` | 否 | 无 | 公历日期 |
| `is_open` | `BOOLEAN` | 否 | 无 | `true` 开市，`false` 休市 |
| `previous_open_date` | `DATE` | 是 | `NULL` | 来源给出的上一交易日 |
| `source` | `VARCHAR(32)` | 否 | 无 | 最近成功写入的 Provider 稳定标识 |
| `source_market` | `VARCHAR(32)` | 否 | 无 | Provider 原生市场标识，首期 `SSE` |
| `sync_mode` | `VARCHAR(16)` | 否 | 无 | 最近一次成功写入模式 |
| `created_at` | `DATETIME(6)` | 否 | 当前 UTC 时间 | 首次创建时间 |
| `updated_at` | `DATETIME(6)` | 否 | 当前 UTC 时间 | 最近成功同步时间 |

### 2.2 键与索引

- 联合主键：`PRIMARY KEY (market_code, calendar_date)`。
- 主键已支持按市场与日期点查、范围查；首期不增加重复二级索引。
- 如果未来出现跨市场按日期查询的明确需求，再评估
  `INDEX (calendar_date, market_code)`，首期不预建。

### 2.3 约束

- `market_code` 必须匹配 `^[A-Z]{2}-S$`，并属于受支持集合
  `CN-S/HK-S/JP-S/US-S/KR-S`；首期启用集合只有 `CN-S`。
- `calendar_date` 必须位于请求的闭区间内。
- `previous_open_date` 非空时必须早于 `calendar_date`。
- `source` 必须是 Registry 中已启用 Provider 的稳定标识；首期为 `tushare`。
- `source_market` 必须是所选 Provider 返回的非空原生市场标识；首期为 `SSE`。
- `sync_mode` 必须为 `monthly`、`year_end` 或 `manual`。
- 同一批次不得出现重复的 `calendar_date`。
- 标准模型中的 `is_open` 必须为布尔值；Tushare Adapter 只接受来源值 `0` 或 `1`
  并完成映射。
- 每次 upsert 必须保持 `created_at` 不变，将 `sync_mode` 覆盖为本次 Flow 模式，
  并将 `updated_at` 显式更新为本批次 UTC 时间。

### 2.4 SQLAlchemy 映射要点

- 使用复合主键，不额外增加无业务意义的自增 ID。
- Python 类型使用 `date` 和时区感知 `datetime`；写入 MySQL 前统一转换为 UTC naive
  `DATETIME(6)`，读取后按 UTC 解释。
- upsert 使用 `sqlalchemy.dialects.mysql.insert()` 和
  `on_duplicate_key_update()`；必须显式设置可变字段、`sync_mode` 和 `updated_at`，
  不得更新 `created_at`，也不依赖 Python `onupdate`。

## 3. 值对象

### 3.1 MarketCode

| 代码 | 业务含义 | 首期状态 | 来源映射 |
|------|----------|----------|----------|
| `CN-S` | 中国 A 股统一交易日历 | 启用 | Tushare `SSE` |
| `HK-S` | 中国香港股票市场 | 预留 | 未定义 |
| `JP-S` | 日本股票市场 | 预留 | 未定义 |
| `US-S` | 美国股票市场 | 预留 | 未定义 |
| `KR-S` | 韩国股票市场 | 预留 | 未定义 |

未知或格式不合法的代码在调用外部接口前拒绝。

### 3.2 CalendarStatus

| 值 | 判定 |
|----|------|
| `OPEN` | 存在记录且 `is_open = true` |
| `CLOSED` | 存在记录且 `is_open = false` |
| `UNKNOWN` | 指定市场和日期没有记录 |

`UNKNOWN` 与 `CLOSED` 不得互换。

### 3.3 SyncMode

| 值 | 参数 | 日期窗口 |
|----|------|----------|
| `monthly` | 计划传入 | 运行日当月 1 日至当年 12 月 31 日 |
| `year_end` | 计划传入 | 下一自然年 1 月 1 日至 12 月 31 日 |
| `manual` | 运维传入 | 显式 `start_date` 至 `end_date`，最长十年 |

## 4. 外部响应模型

Tushare 请求固定选择：

```text
exchange,cal_date,is_open,pretrade_date
```

Tushare Adapter 将每个来源行映射为供应商无关模型：

| Tushare 字段 | 领域字段 | 转换 |
|--------------|----------|------|
| `exchange` | `source_market` | 必须等于 `SSE` |
| `cal_date` | `calendar_date` | `YYYYMMDD` → `date` |
| `is_open` | `is_open` | `0/1` → `false/true` |
| `pretrade_date` | `previous_open_date` | 空值 → `NULL`，否则 `YYYYMMDD` → `date` |
| 固定映射 | `market_code` | `CN-S` |
| Adapter 标识 | `source` | `tushare` |
| Flow 上下文 | `sync_mode` | 当前 Flow 的 `monthly/year_end/manual` |

## 5. 批次状态与事务

批次不是持久化实体，只存在于 Flow 内存与日志中。

```text
RECEIVED
  → FETCHING
  → VALIDATING
  → WRITING
  → SUCCEEDED

任一步骤失败 → FAILED
```

规则：

1. `FETCHING` 完成前不打开写事务。
2. `VALIDATING` 必须由 Service 统一确认 Provider 返回标准模型、来源标识有效、
   日期位于请求范围内且唯一；Tushare Adapter 在映射阶段确认原生市场为 SSE。
3. 请求开始日至 `min(end_date, as_of_date)` 必须逐自然日完整覆盖；任一历史或当日
   缺口均导致整批失败。
4. `as_of_date` 之后允许只返回连续前缀，但返回的第一日必须衔接必需区间
   （未来专属范围则必须等于 `start_date`），且到最大返回日之间不得有内部断点。
5. 最大返回日 `coverage_end` 之后到 `end_date` 仅可作为尚未公布的连续未来尾部；
   该批次标记 `FUTURE_PARTIAL`，不为缺失日期合成记录，也不删除既有数据。
6. 空批次始终失败；`coverage_end = end_date` 时标记 `COMPLETE`。
7. `WRITING` 在单个 MySQL 事务中完成已验证前缀的整批 upsert。
8. 事务异常必须整体回滚，既有数据保持不变。
9. 只有事务提交后才能记录 `SUCCEEDED`；完整性状态单独记录为
   `COMPLETE` 或 `FUTURE_PARTIAL`。
10. 不根据来源缺失记录执行删除。

## 6. 数据量与生命周期

- `CN-S` 每个自然年最多 366 行。
- 五个预留市场十年数据少于 20,000 行。
- 当前值长期保留，无自动过期或清理策略。
- `sync_mode` 只表示最近一次成功写入方式，不构成执行历史或审计记录。
- JSONL 日志按 10 MiB 轮转，保留 5 个归档文件；日志生命周期独立于业务表。

## 7. 查询语义

`get_status(market_code, calendar_date)`：

- 命中开市记录 → `OPEN`。
- 命中休市记录 → `CLOSED`。
- 无记录 → `UNKNOWN`。
- 市场代码格式错误或未启用 → 参数错误，不返回 `UNKNOWN`。

范围查询按 `calendar_date ASC` 返回，用于验证和内部任务，不对“无数据”自动补齐虚拟行。
处于 `coverage_end` 之后的尚未公布日期因无记录而返回 `UNKNOWN`，不得解释为休市。
