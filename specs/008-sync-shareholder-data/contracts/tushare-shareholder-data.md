# 供应商契约：Tushare 股东数据三接口

> 契约范围：Tushare Adapter 与供应商之间的边界。本契约定义唯一允许调用的
> 接口、请求参数、字段映射、错误与限流语义、完整性门禁。供应商细节不得
> 泄漏进 Flow / Service / Repository / ORM（宪章 II）。
> 关联：`shareholder-data-provider.md`（Port）、`shareholder-data-service.md`、
> `prefect-flow.md`、`data-model.md`、`research.md`。

## 1. 目的

本功能唯一允许调用的供应商接口是 Tushare `top10_holders`（前十大股东，
https://tushare.pro/document/2?doc_id=61）、`top10_floatholders`
（前十大流通股东，doc_id=62）、`stk_holdernumber`（股东人数，
doc_id=166）。禁止调用这三个接口之外的任何 Tushare 端点（spec FR-017）。
三个接口各对应一条独立同步链路（Flow/run_key/审计 `data_kind`，见
`prefect-flow.md`/`shareholder-data-service.md`）：某接口失败不
影响其他两个接口；Adapter 对外暴露三个提取方法，内部共享同一
TushareClient 信封与节流器。

## 2. 端点与请求

| 项 | 值 |
|----|-----|
| Adapter | `TushareShareholderDataProvider`（`src/lucking/integrations/tushare/shareholder_data_provider.py`） |
| API | `top10_holders` / `top10_floatholders` / `stk_holdernumber` |
| 提取模式 | 按公告日全市场提取（**不传 ts_code**，实测 2026-08-05 验证可行；文档标注必填不准确），分页续取 |
| 业务参数 | 公告日期（单日），增量与回补均逐日调用 |
| 请求字段 | `top10_*`：`ann_date=YYYYMMDD`；`stk_holdernumber`：`start_date=end_date=YYYYMMDD`（公告日期语义，与 `top10_*` 的 start/end=报告期语义不同，不得混用） |
| 分页参数 | `limit=6000` + `offset`（实测验证：`has_more=True` 时 offset 翻页有效；limit=1 探测亦验证参数生效） |
| 输出字段 | 按 §3 白名单显式声明（实测与文档逐名一致） |

**参数语义注记（实测确认）**：`top10_*` 的 `start_date/end_date` 按
**报告期（end_date）**过滤，另有 `ann_date` 单日公告过滤参数；
`stk_holdernumber` 的 `start_date/end_date` 按**公告日期（ann_date）**
过滤，另有 `enddate`（截止日期）参数。增量推进一律按公告日期，
回补 `top10_*` 按报告期季度末、`stk_holdernumber` 按公告日（research
决策 1）。

## 3. 字段映射

规范字段名 = 来源字段名**原样保留**（research 决策 7）。`ts_code` 经身份
解析（003 `provider_mappings`）后不进入数据列。

**`top10_holders` / `top10_floatholders`（9 字段，两接口结构一致）**：

| 字段 | 规范类型 | 说明 |
|------|----------|------|
| `ann_date` | Date | 公告日期（增量水位依据；业务列） |
| `end_date` | Date | 报告期（业务身份组成部分；一般为每季度最后一天） |
| `holder_name` | String | 股东名称（业务身份组成部分） |
| `hold_amount` | Decimal(24,2) | 持有数量（股；实测 600000.SH 达 7,086,834,641 股，必须 24 位） |
| `hold_ratio` | Decimal(12,4) | 占总股本比例（%，如 21.2781） |
| `hold_float_ratio` | Decimal(12,4) | 占流通股本比例（%） |
| `hold_change` | Decimal(24,2) | 持股变动（股，可为负） |
| `holder_type` | String | 股东类型（实测取值：一般企业、自然人、保险投资组合、开放式投资基金、证金等） |
| `ts_code` | — | 仅用于身份解析，不进入业务列 |

**`stk_holdernumber`（4 字段）**：

| 字段 | 规范类型 | 说明 |
|------|----------|------|
| `ann_date` | Date | 公告日期（增量水位依据） |
| `end_date` | Date | 截止日期（股东户数统计日，业务身份组成部分） |
| `holder_num` | UInt32 | 股东户数（实测 300199.SZ 为 98,777 户） |
| `ts_code` | — | 仅用于身份解析，不进入业务列 |

**不进入 DTO 的字段**：`ts_code`（只用于身份解析）、文档或实测之外
出现的任何新字段（ED-006：不得进入业务表，白名单严格校验失败时整批失败）。

## 4. 错误映射与重试

| 供应商错误 | 规范类别 | 可重试 | 行为 |
|------------|----------|--------|------|
| 业务限流（频率超限业务码） | `PROVIDER_RATE_LIMITED` | 是 | 退避 30/120/300 秒，≤ 3 次，受整体 deadline 约束 |
| 超时/连接失败 | `PROVIDER_TIMEOUT` / `PROVIDER_NETWORK` | 是 | 同上 |
| 积分/权限不足 | `QUOTA_EXCEEDED` / `AUTHENTICATION` | 否 | 确定性失败，0 次重试 |
| 参数被拒、公告日无数据 | `PROVIDER_BAD_REQUEST` / 按空响应规则 | 按规则 | 确定性失败或空响应处理（见 §6） |
| 其他业务错误 | `PROVIDER_BUSINESS_ERROR` | 否 | 0 次重试 |

- 重试只在 Adapter 初次调用后进行；Flow `retries=0`，重试层数不叠加。
- 每次真实 HTTP 请求前必须经过节流器（§5），重试请求同样受节流约束。

## 5. 限流与节流（400 次/分钟，账户级共享预算）

- 供应商限流档位：用户显式指定每分钟 400 次，语义为**账户级共享预算**
  （三个接口的请求**合计** ≤ 400 次/分钟，spec FR-005；部署账户实测
  探测全程未触发限流拒绝）。
- **跨进程共享节流**（research 决策 4 修订）：任意 60 秒窗口内真实 HTTP
  请求数 ≤ 400（最小间隔 ≥ 150 毫秒 = 60/400）。Registry 组装时注入
  `RedisRateLimiter`（Redis ZSET 滑窗 + Lua 原子判定），三个接口的所有
  flow run 进程（含回补与增量同跑）共享同一预算；未注入时回退进程级
  `RateLimiter`（测试/直接构造路径）；Redis 不可达降级为进程级限流
  （fail-open，不阻断同步）。
- 节流只负责"不超过限流"，不替代错误重试；被限流拒绝仍按 §4 映射重试。
- 回补与增量、三个接口共用同一节流器（跨进程全局生效）。

## 6. 完整性门禁

- 单次请求最大返回 **6,000 行**（实测 2026-08-05：报告期 20260331 全市场
  ~5.4 万行分 9 页；披露高峰日单公告日触顶 6,000 行；独立配置
  `shareholder_data_page_limit=6000`，三个接口统一）。
- **`has_more` 驱动分页**（实测验证，ED-008"不猜测参数"在此有验证依据）：
  响应 `data.has_more=True` 时以 `offset=offset+6000` 续取，
  直至 `has_more=False`；任何一页位置不前进、页摘要 SHA-256 重复、
  超过最大页数（增量 10 页/日、回补 20 页/期）或中途失败 →
  `ProviderResponseCappedError`/不完整，本次不得标记成功。
- `stk_holdernumber` 文档"单次最大 3,000"**已过时**：实测 3 日窗口
  5,286 行完整返回（`has_more=False`）；完整性判定完全依赖 `has_more`
  标志，不依赖行数猜测。
- 空响应（0 行）：单公告日无披露属正常业务结果（如昨日 20260804 三接口
  均返回 0 行）；与"提取中断/被截断"区分，由 Service 按 spec FR-014/
  ED-005 判定（个别股票无数据正常，全市场空响应属正常披露节奏）。

## 7. 契约测试要点

- 对 `TushareShareholderDataProvider` 使用**真实部署账户**验证一次
  `ann_date` 全市场请求：字段全集与 §3 一致、无文档外字段、
  `has_more/offset` 分页行为（上线门禁，research 待验证项 1~3 已完成）。
- 用可注入 `client/sleep/monotonic` 的测试替身验证：字段白名单严格相等
  （`set(row) != set(fields)` 整批失败）、`has_more=True` 续取与
  位置不前进/重复页判定、节流间隔 ≥ 150 毫秒、重试退避序列与 deadline
  约束、错误分类映射。
- 提供至少一个可替代实现或测试替身，证明更换供应商不改业务代码
  （宪章 II；ED-007）。
- 契约测试不得依赖供应商 SDK；供应商错误码只出现在 Adapter 内部映射表。
