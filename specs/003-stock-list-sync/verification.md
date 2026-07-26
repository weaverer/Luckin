# 003-stock-list-sync 实施验证

**验证日期**：2026-07-26  
**范围**：固定 `CN-S` 股票列表同步，仅调用 Tushare `stock_basic`

## 结论

实现、迁移、自动化测试、范围审计、性能测试和运行手册均已通过。真实外部 Tushare
调用未作为验收前提；外部请求使用 `httpx.MockTransport` 做确定性契约验证，未消耗 Token
或额度。

## 迁移验证

使用本机 Docker MySQL 8.4.10 完成以下验证：

1. 已有交易日历数据库 `lucking`：从 revision `001` 执行
   `uv run alembic upgrade head`，成功升级到 `002`。
2. 隔离空库 `lucking_verify_003`：成功执行 `base → 001 → 002`。
3. 在隔离库执行 `002 → 001 → 002`，降级和重新升级均成功。
4. 两个数据库的 `alembic_version` 均为 `002`。
5. 两个数据库均同时存在 `trading_calendar`、`stock_current`、
   `stock_provider_mapping`、`stock_list_sync_run` 和
   `stock_list_sync_issue`，证明升级保留已有交易日历表。
6. 隔离 MySQL 上的仓储测试验证：唯一约束失败时，候选当前值全部回滚，同步运行不被
   错误地标记为成功；既有交易日历 MySQL 原子 upsert 测试也保持通过。

MySQL 专项命令：

```bash
TEST_DATABASE_URL=本机隔离库连接串 uv run pytest --capture=no -m mysql \
  tests/integration/test_stock_list_repository.py \
  tests/integration/test_trading_calendar_repository.py
```

结果：`2 passed, 8 deselected`。

## 最终质量门禁

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q
```

结果：

- Ruff：`All checks passed!`
- mypy：`Success: no issues found in 25 source files`
- pytest：`77 passed, 2 skipped in 7.76s`
- 两个跳过项均为未给普通全量命令注入 `TEST_DATABASE_URL` 的 MySQL 用例；它们已在上述
  隔离 MySQL 专项命令中单独通过。

范围与性能专项：

```bash
uv run pytest -q \
  tests/integration/test_stock_list_performance.py \
  tests/contract/test_stock_list_scope.py
```

结果：`4 passed in 6.65s`。

## 快速验收场景

以下命令覆盖固定三 venue、正常同步、及时性、重复触发、完整性、来源失败、范围和性能：

```bash
uv run pytest -q \
  tests/contract/test_tushare_stock_basic.py \
  tests/contract/test_stock_list_provider.py \
  tests/contract/test_stock_list_scope.py \
  tests/unit/test_stock_list_service.py \
  tests/unit/test_stock_list_identity.py \
  tests/unit/test_stock_list_logging.py \
  tests/integration/test_stock_list_flow.py \
  tests/integration/test_stock_list_performance.py
```

结果：`27 passed in 7.35s`。

场景证据：

- **固定范围**：请求对象只有 `scope_code`；Adapter 恰好执行
  `SSE/SZSE/BSE × L/D/P/G` 12 个分区，不存在 venue 子集入口。
- **外部请求审计**：唯一端点为 `stock_basic`，字段精确为 8 个白名单字段；源码审计
  排除交易日历、行情、成交、财务和公司端点。
- **正常同步**：Memory Provider 和 Tushare Adapter golden cases 产生相同规范语义；
  Flow 返回 `SUCCEEDED` 并写结构化终态日志。
- **最近 30 次计划及时率**：从 31 条计划终态中仅选择最近 30 条，30/30 达标时结果为
  formal；`manual-stock-list` 明确排除。单次阈值为 1,800,000 ms。
- **连续 30 次重复/补跑**：相同计划参数连续触发 30 次只产生同一 `run_id/run_key`
  权威结果；仓储测试另行证明成功周期短路、失败周期必须显式补跑且复用 run_key。
- **完整性保护**：空聚合、缺失分区、触及 6,000 行上限、非法字段/日期/枚举、
  双键冲突和基线 Provider 身份缺失均整批失败，不调用发布。
- **来源失败**：网络、429 和 5xx 只重试当前分区，退避为 30/120/300 秒；鉴权、额度、
  载荷和 deadline 失败不进入整批重试，异常摘要经过脱敏。
- **查询性能**：发布 10,000 条候选，预热后连续执行 100 次无筛选、代码、venue、
  名称和状态查询，断言至少 95 次在 1 秒内完成；本次专项整体 6.65 秒。
- **数据安全**：日志字段白名单、标识哈希、Token 脱敏和轮转测试通过；当前值模型不含
  Provider ID 或禁止的 `stock_basic` 扩展字段。

## 五分钟运维排障演练

已核对 `README.md` 的股票列表运行指引可在五分钟内按固定顺序执行：

1. `docker compose ps` 检查 MySQL 和 Prefect Server，检查 Worker 与 Deployment 计划。
2. `tail`/`rg` 检查 `logs/stock-list-sync.jsonl`，以 Flow Run ID 关联运行终态。
3. 查看 `error_category`、安全摘要、segment 计数和质量问题，不接触 Token 或原始行。
4. 区分当前分区可重试来源错误与不可重试的鉴权、额度、载荷、完整性和数据库错误。
5. 修复后对原计划时点设置 `is_manual_retry=true`，复用同一 run_key 补跑。

安全停止方式为暂停计划或停止 Worker；已开始的数据库发布由单事务提交或回滚。运行手册
明确警告不要把 `docker compose down -v` 当作普通停止方式。

