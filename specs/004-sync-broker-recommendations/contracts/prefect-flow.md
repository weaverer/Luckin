# 工作流契约：券商金股 Prefect Flow

## 1. Flow

```python
@flow(name="broker-recommendation-sync", retries=0)
def sync_broker_recommendations(
    scheduled_at: datetime | None = None,
    schedule_slug: str | None = None,
    is_manual_retry: bool = False,
) -> dict[str, object]: ...
```

Flow 只负责：

1. 加载 Settings。
2. 解析原计划时点和 schedule slug。
3. 从 Registry 构造 Provider。
4. 构造数据库 Engine、Repository 和 Service。
5. 调用 Service。
6. 写结构化开始、成功或失败日志。
7. 返回可序列化规范结果。

Flow 不执行字段映射、券商规范化、身份解析、SQL、重试或删除。

## 2. 原计划时点

- 自动计划运行：`scheduled_at is None` 时读取
  `prefect.runtime.flow_run.scheduled_start_time`。
- Prefect runtime 无计划时点的纯本地直接调用可使用当前时间，但必须标记为人工运行；
  生产 Deployment 不允许走此回退。
- 人工补跑：`is_manual_retry=True` 时必须显式传入原计划 aware datetime 和原 schedule slug。
- 不得使用实际开始时间替换可用的原计划时间。
- Service 负责从该时点推导北京时间目标月份。

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
`broker-recommendation-sync/default`。周末、节假日和非交易日不暂停。

## 4. 并发与重试

- Deployment 并发限制 1、冲突策略 `ENQUEUE` 只负责资源保护。
- MySQL `run_key` 唯一约束负责最终幂等。
- Flow `retries=0`，Service 不重试。
- Tushare Adapter 对当前请求的瞬态错误最多额外重试 3 次。
- 失败补跑由运维显式发起，并复用原计划时点。

## 5. 配置

```dotenv
BROKER_RECOMMENDATION_PROVIDER=tushare
BROKER_RECOMMENDATION_TIMEZONE=Asia/Shanghai
BROKER_RECOMMENDATION_LOG_DIR=logs
BROKER_RECOMMENDATION_LOG_FILENAME=broker-recommendation-sync.jsonl
BROKER_RECOMMENDATION_FETCH_DEADLINE_SECONDS=1500
BROKER_RECOMMENDATION_TIMELINESS_TARGET_MS=1800000
BROKER_RECOMMENDATION_ROW_CAP=1000
TUSHARE_TOKEN=local-secret-only
TUSHARE_API_URL=https://api.tushare.pro
```

约束：

- 时区首期固定 `Asia/Shanghai`。
- 截止时间必须大于 0 且小于 30 分钟目标。
- row cap 默认且首期固定为当前验证契约的 1,000，不得通过调大配置绕过触顶失败。
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
flow_run_id, run_id, attempt_id, attempt_no, schedule_slug,
scheduled_at, target_month, provider_code, started_at, completed_at,
provider_request_count, provider_retry_count, received_count, valid_count,
added_count, updated_count, unchanged_count, duplicate_count,
invalid_count, conflict_count, duration_ms, schedule_lag_ms,
completed_after_schedule_ms, timeliness_target_ms, timeliness_met,
error_category, error_summary
```

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
4. 人工补跑缺少原计划时点时拒绝。
5. 3 日和 4 日产生不同 run，但目标月份相同。
6. Flow 不做 Provider 重试；瞬态调用总数由 Adapter 限定。
7. 成功/失败日志字段完整且不泄密。
8. 30 分钟及时性以原计划时点计算。
