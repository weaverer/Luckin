# 工作流契约：券商金股 Prefect Flow

## 1. Flow

```python
@flow(name="broker-recommendation-sync", retries=0)
def sync_broker_recommendations(
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
) -> dict[str, object]: ...


@flow(name="broker-recommendation-backfill", retries=0)
def backfill_broker_recommendations(
    start_month: date,
    end_month: date,
    backfill_batch_id: str,
) -> dict[str, object]: ...


@flow(name="broker-recommendation-retry", retries=0)
def retry_broker_recommendation_sync(
    run_id: str,
) -> dict[str, object]: ...
```

计划和重试 Flow 只负责：

1. 加载 Settings。
2. 解析原计划时点和 schedule slug，或加载待重试 `run_id`。
3. 从 Registry 构造 Provider。
4. 构造数据库 Engine、Repository 和 Service。
5. 调用 Service。
6. 写结构化开始、成功或失败日志。
7. 返回可序列化规范结果。

历史补跑 Flow 先校验月份闭区间与非空 `backfill_batch_id`：首尾月份均计入，
范围必须为 1–120 个自然月、
不得包含未来月份且起始月份不得晚于结束月份；任一条件不满足时，在调用 Service 或创建
任何月度 run 前整体拒绝。校验通过后按自然月递增展开，为每个月调用同一 Service，
并汇总每月结果；各月份拥有独立 run 和事务。
它不得复制字段映射、券商规范化、身份解析、SQL、Provider 重试或删除逻辑。

补跑汇总至少返回：

```text
backfill_batch_id, start_month, end_month, total_month_count,
succeeded_month_count, failed_month_count, skipped_month_count,
in_progress_month_count, failed_months
```

Flow 必须尝试处理区间内全部月份；若任一月份失败，处理完其余月份后整体标记为失败并返回
脱敏汇总。相同批次重跑时，对每个月调用
`BrokerRecommendationService.resolve_backfill_month`：

- `START`：发送 Backfill 月命令。
- `SKIP_SUCCEEDED`：不调用 Provider，计入 `skipped_month_count`。
- `RETRY`：使用解析出的原 `run_id` 发送 Retry 命令，不得再次发送 Backfill 命令。
- `IN_PROGRESS`：不创建第二 attempt，计入 `in_progress_month_count`。

因此失败或过期月份复用原 run 并新增 attempt，尚未开始的月份才创建首次 run。

## 2. 原计划时点

- 自动计划运行：`scheduled_at is None` 时读取
  `prefect.runtime.flow_run.scheduled_start_time`。
- Prefect runtime 无计划时点的纯本地直接调用必须显式提供计划 aware datetime 和 slug；
  生产 Deployment 不允许使用当前时间回退。
- 不得使用实际开始时间替换可用的原计划时间。
- Service 负责从该时点推导北京时间目标月份。
- 历史补跑不接受或伪造 `scheduled_at`；目标月份只来自已校验区间。
- 失败重试只接受原 `run_id`，目标月和运行身份从数据库加载。

官方 runtime 契约：
<https://docs.prefect.io/v3/api-ref/python/prefect-runtime-flow_run>。

## 3. Deployment

在 `prefect.yaml` 增加：

```yaml
- name: default
  version: "1"
  tags: [broker-recommendation]
  description: 每月 3 日和 4 日北京时间 12:00 同步当前月券商金股。
  entrypoint: src/lucking/flows/broker_recommendation.py:sync_broker_recommendations
  concurrency_limit:
    limit: 1
    collision_strategy: ENQUEUE
  work_pool:
    name: local-pool
    work_queue_name: default
    job_variables: {}
  schedules:
    - cron: "0 12 3,4 * *"
      timezone: Asia/Shanghai
      slug: monthly-broker-recommendations
      active: true
      parameters:
        schedule_slug: monthly-broker-recommendations
```

创建后的 Flow/Deployment 全限定名称为
`broker-recommendation-sync/券商金股同步`。周末、节假日和非交易日不暂停。

历史补跑 Flow 不创建 Cron；可注册为仅人工触发的
`broker-recommendation-backfill/券商金股历史回补`。它必须拒绝空批次键、未来月份、
开始月份晚于结束月份以及超过 120 个月的范围。

## 4. 并发与重试

- Deployment 并发限制 1、冲突策略 `ENQUEUE` 只负责资源保护。
- MySQL `run_key` 唯一约束负责最终幂等。
- Flow `retries=0`，Service 不重试。
- Tushare Adapter 对当前请求的瞬态错误最多额外重试 3 次。
- 失败重试由运维显式发起并引用原 `run_id`。
- 同一补跑批次的月度 run key 负责重复提交与多 Worker 下的最终幂等；
  一个历史月份失败不回滚其他已成功月份。
- 计划与补跑 Flow 可并发处理同一月份并形成不同 run；数据库推荐业务唯一约束和原子 upsert
  只负责保证相同 `recommendation_month + broker_name + stock_id` 不产生重复记录；
  跨 run 的股票简称等其他属性不定义版本优先级。

## 5. 配置

```dotenv
BROKER_RECOMMENDATION_PROVIDER=tushare
BROKER_RECOMMENDATION_TIMEZONE=Asia/Shanghai
BROKER_RECOMMENDATION_LOG_DIR=logs
BROKER_RECOMMENDATION_LOG_FILENAME=broker-recommendation-sync.jsonl
BROKER_RECOMMENDATION_FETCH_DEADLINE_SECONDS=1500
BROKER_RECOMMENDATION_RUN_LEASE_SECONDS=2100
BROKER_RECOMMENDATION_TIMELINESS_TARGET_MS=1800000
BROKER_RECOMMENDATION_PAGE_LIMIT=1000
BROKER_RECOMMENDATION_MAX_PAGES=100
BROKER_RECOMMENDATION_TUSHARE_PAGINATION_ENABLED=false
TUSHARE_TOKEN=local-secret-only
TUSHARE_API_URL=https://api.tushare.pro
```

约束：

- 时区首期固定 `Asia/Shanghai`。
- 截止时间必须大于 0 且小于 30 分钟目标。
- 运行租约首期固定 2,100 秒，必须大于 1,500 秒 Provider deadline；
  由数据库 UTC 时钟创建和判断，首版不续租。
- page limit 默认且首期固定为 1,000，max pages 必须大于 0。
- `TUSHARE_PAGINATION_ENABLED` 只有部署账户或供应商沙箱验证 `limit/offset`
  前进和终止契约后才能设为 `true`；为 `false` 时满页必须失败。
- Token 仅在选中 Tushare Provider 时读取。
- `.env.example` 不含真实秘密。

## 6. 日志事件

至少包含：

```text
broker_recommendation_sync_started
broker_recommendation_provider_attempt_started
broker_recommendation_provider_attempt_failed
broker_recommendation_validation_completed
broker_recommendation_sync_succeeded
broker_recommendation_sync_failed
```

公共白名单字段：

```text
flow_run_id, run_id, attempt_id, attempt_no, run_kind, schedule_slug,
scheduled_at, backfill_batch_id, target_month, provider_code, started_at, completed_at,
provider_request_count, provider_retry_count, provider_page_count,
provider_page_limit, provider_last_page_count, received_count, valid_count,
added_count, updated_count, unchanged_count, duplicate_count,
invalid_count, conflict_count, duration_ms, schedule_lag_ms,
completed_after_schedule_ms, timeliness_target_ms, timeliness_met,
error_category, error_summary
```

`run_id` 和 `attempt_id` 是 UUID 业务标识；数据库 BIGINT 物理主键不得进入日志或 Flow 返回。

禁止字段：

- Token、Authorization、数据库连接串；
- 完整请求/响应、原始 Tushare 行和原始业务错误消息；
- 未经白名单的环境变量或异常对象；
- 完整 Provider 股票标识或券商原始行。

JSONL 沿用 10 MiB 轮转和 5 个归档文件。

## 7. 返回与失败

成功返回 `BrokerRecommendationSyncResult` 的 JSON 兼容表示：

- 枚举转字符串；
- 月份和时间使用 ISO 8601；
- 不含 Provider 原始字段或秘密。

失败时：

- MySQL attempt/run 已形成明确失败终态；
- 推荐表未发生部分修改；
- Flow 写安全错误类别、计数和及时性后重新抛出；
- Prefect 将运行标记为失败；
- 不触发 Flow 级自动重试。

## 8. Flow 测试

必须验证：

1. YAML Cron、时区、slug、entrypoint 和并发配置精确。
2. 自动运行读取 runtime 计划时点，而非实际当前时间。
3. 延迟跨月执行仍查询原月份。
4. 24 月历史补跑按月展开，相同批次重放跳过成功月，并把失败/过期月转换为原 `run_id` 的 Retry。
5. 3 日和 4 日产生不同 run，但目标月份相同。
6. 新批次键可以主动刷新同一历史月份，业务推荐仍不重复。
7. 120 月范围可执行；121 月、未来月、反向或空范围在产生任何 run 前整体拒绝。
8. 失败重试只引用原 `run_id`；同批次失败月不得创建第二个 BACKFILL run。
9. 10 组计划/补跑同月并发分别可追踪，且相同
   `recommendation_month + broker_name + stock_id` 不产生重复推荐；
   不比较股票简称等其他属性的跨 run 最终版本。
10. 固定 35 分钟租约在数据库 UTC 到期前返回 `IN_PROGRESS`，
    到期后原子标记旧 attempt 为 `ABANDONED` 并对原 `run_id` Retry。
11. Flow 不做 Provider 重试；瞬态调用总数由 Adapter 限定。
12. 成功/失败日志字段完整且不泄密，且不包含 BIGINT 物理主键。
13. 30 分钟及时性只对自动计划运行以原计划时点计算。
