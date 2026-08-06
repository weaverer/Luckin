"""手动回补脚本：按接口回补股东数据（幂等，已成功日期自动跳过）。

直接调用 008 的三个回补 Flow（`flows/shareholder_data.py`），不经过
Prefect 调度；重复执行同一 batch 时已成功日期自动 SKIP，可安全重跑。
回补日期展开按接口语义：`TOP10`/`TOP10_FLOAT` 按报告期季度末、
`HOLDER_COUNT` 按公告日逐日（research 决策 1）。

用法：
    # 单个接口回补
    python scripts/backfill_shareholder_data.py --kind TOP10 --start 20240101
    python scripts/backfill_shareholder_data.py --kind HOLDER_COUNT --start 20240101 --end 20260805

    # 全部三个接口串行回补
    python scripts/backfill_shareholder_data.py --all --start 20240101 --end 20260805

    # 仅预览将处理的日期（不调用来源）
    python scripts/backfill_shareholder_data.py --kind TOP10 --start 20240101 --dry-run
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lucking.config import Settings
from lucking.flows.shareholder_data import (
    _expansion,
    holder_count_backfill,
    top10_float_holders_backfill,
    top10_holders_backfill,
)

# 接口 kind（与 services/shareholder_data.py 的 KIND_TO_DATA_KIND 一致；
# HOLDER_COUNT 是接口级 kind，不是 HolderKind 名单类型成员）
KIND_TOP10 = "TOP10"
KIND_TOP10_FLOAT = "TOP10_FLOAT"
KIND_HOLDER_COUNT = "HOLDER_COUNT"

KIND_FLOWS: dict[str, object] = {
    KIND_TOP10: top10_holders_backfill,
    KIND_TOP10_FLOAT: top10_float_holders_backfill,
    KIND_HOLDER_COUNT: holder_count_backfill,
}

BACKFILL_START = date(2024, 1, 1)


def _parse_date(value: str) -> date:
    raw = value.strip().replace("-", "")
    if len(raw) != 8 or not raw.isdigit():
        raise argparse.ArgumentTypeError(f"日期必须是 YYYYMMDD 或 YYYY-MM-DD：{value}")
    return datetime.strptime(raw, "%Y%m%d").date()


def _validate_range(start: date, end: date, today: date) -> None:
    if start > end:
        raise SystemExit("错误：开始日期不得晚于结束日期")
    if start < BACKFILL_START:
        raise SystemExit("错误：回补不得早于 2024-01-01")
    if end > today:
        raise SystemExit(f"错误：回补不得包含未来日期（今天是 {today.isoformat()}）")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="backfill_shareholder_data",
        description="手动回补股东数据（前十大股东 / 前十大流通股东 / 股东人数，幂等可重跑）",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--kind",
        choices=[
            KIND_TOP10,
            KIND_TOP10_FLOAT,
            KIND_HOLDER_COUNT,
        ],
        help="回补的接口：TOP10 / TOP10_FLOAT / HOLDER_COUNT",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="按 TOP10 → TOP10_FLOAT → HOLDER_COUNT 串行回补全部",
    )
    parser.add_argument(
        "--start", type=_parse_date, default=BACKFILL_START,
        help="开始日期（含），默认 20240101",
    )
    parser.add_argument("--end", type=_parse_date, help="结束日期（含），默认今天")
    parser.add_argument("--batch", help="回补批次标识（幂等键）；默认 manual-<接口>-<起>-<止>")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅列出将处理的日期，不调用来源、不写入任何数据",
    )
    args = parser.parse_args()

    settings = Settings()
    today = datetime.now(ZoneInfo(settings.shareholder_data_timezone)).date()
    end = args.end or today
    _validate_range(args.start, end, today)

    kinds = (
        [KIND_TOP10, KIND_TOP10_FLOAT, KIND_HOLDER_COUNT]
        if args.all
        else [args.kind]
    )

    failed_any = False
    for kind in kinds:
        flow = KIND_FLOWS[kind]
        days = _expansion(kind, args.start, end)
        batch = args.batch or f"manual-{kind}-{args.start:%Y%m%d}-{end:%Y%m%d}"
        print(
            f"== {kind} == {len(days)} 个处理日期"
            f"（{'季度末' if kind != 'HOLDER_COUNT' else '公告日逐日'}），"
            f"批次：{batch}"
        )
        if args.dry_run:
            print("  dry-run（不执行）：")
            for day in days[:5]:
                print(f"    - {day.isoformat()}")
            if len(days) > 5:
                print(f"    ... 共 {len(days)} 个日期")
            continue
        result = flow(start_date=args.start, end_date=end, backfill_batch_id=batch)
        print(
            f"  成功 {result['succeeded_day_count']} 天，跳过 {result['skipped_day_count']} 天，"
            f"进行中 {result['in_progress_day_count']} 天，失败 {result['failed_day_count']} 天"
        )
        if result["failed_dates"]:
            failed_any = True
            print(f"  失败日期：{result['failed_dates']}（修复后重跑同一 --batch 只补失败日期）")
    return 1 if failed_any else 0


if __name__ == "__main__":
    raise SystemExit(main())
