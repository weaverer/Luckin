# 交易日历同步验证记录

**验证日期**：2026-07-26  
**环境**：WSL2 Linux 6.18.33.2、Python 3.12.13、MySQL 8.4

## 自动化质量门禁

| 检查 | 结果 |
|---|---|
| `uv run ruff check .` | 通过 |
| `uv run mypy src` | 通过，19 个源文件无问题 |
| `uv run pytest` | 通过，41 项测试通过 |
| `uv run alembic upgrade head` | 通过，MySQL 已升级到 `001` |

测试覆盖窗口计算、Provider Registry 与替身、通用 Tushare 协议、`trade_cal` Adapter、
MySQL 幂等 upsert、`created_at` 不变、`sync_mode/updated_at` 覆盖、完整性校验、
查询语义、重试分类、JSONL 脱敏、及时性公式及人工运行排除。

## MySQL 性能基准

执行：

```bash
uv run python scripts/benchmark_trading_calendar.py
```

隔离数据范围为 2080-01-01 至 2089-12-31，共 3653 行；执行结束后仅删除
`source=benchmark` 的该范围数据。

| 指标 | 样本量 | p95 | 目标 | 结果 |
|---|---:|---:|---:|---|
| 十年范围批量 upsert | 20 | 308.454 ms | ≤ 10 分钟 | 通过 |
| 单日数据库查询 | 500 | 2.265 ms | ≤ 100 ms | 通过 |

该基准测量本机 MySQL 的数据库路径，不包含真实 Tushare 网络耗时、Prefect Worker
排队或业务重试；计划端到端指标由生产 JSONL 中的
`schedule_to_completion_ms` 持续测量。

## 计划及时性

当前尚无完成的真实月度或年末计划样本：

| Schedule | 样本量 | 状态 | 达标率 |
|---|---:|---|---|
| `monthly-current-year` | 0 | 暂定 | N/A |
| `year-end-next-year` | 0 | 暂定 | N/A |

满 20 个计划终态后，日志按 `schedule_slug` 取最近 20 次，至少 19 次
`timeliness_met=true` 才正式达标。人工运行不进入该统计。

## 快速验证演练

| 场景 | 验证方式 | 结果 |
|---|---|---|
| 月度/年末窗口与跨年 | 固定业务日期单元测试 | 通过 |
| 人工补数参数及授权范围 | Flow 集成测试 | 通过 |
| 重复同步与元数据幂等 | SQLite 与真实 MySQL 集成测试 | 通过 |
| 完整、未来连续尾部、历史/内部缺口 | Service 单元测试 | 通过 |
| 网络、429、5xx、额度与凭据分类 | Client/Flow 契约测试 | 通过 |
| Provider 替换及未知配置拒绝 | Registry 与内存 Provider 契约测试 | 通过 |
| 日志白名单、轮转与秘密脱敏 | JSONL 单元测试 | 通过 |

为避免消耗真实账户额度，本次验证没有调用真实 Tushare Token；外部协议和字段转换使用
HTTPX `MockTransport` 固定响应验证。实际部署前应按
`quickstart.md` 使用本机秘密执行一次受控人工范围同步。

