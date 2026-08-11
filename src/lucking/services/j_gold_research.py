"""J金股研究聚合、评分和可信降级规则。"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from statistics import fmean
from typing import Any

from lucking.models.j_gold import (
    QualityKind,
    QualityStatus,
    RecommendationFact,
    ResearchContext,
    ResearchSnapshot,
)
from lucking.ports.j_gold_data import JGoldDataReader

SCORE_WEIGHTS = {"consensus": 0.30, "warming": 0.25, "continuity": 0.20, "excess": 0.25}
MIN_BROKER_SAMPLES = 20


def previous_month(value: date, count: int = 1) -> date:
    year, month = value.year, value.month
    for _ in range(count):
        month -= 1
        if month == 0:
            year -= 1
            month = 12
    return date(year, month, 1)


class JGoldResearchService:
    def __init__(self, reader: JGoldDataReader) -> None:
        self._reader = reader

    def resolve_month(self, requested: date | None) -> tuple[date | None, list[date], bool]:
        months = sorted(set(self._reader.available_months()), reverse=True)
        if not months:
            return None, [], False
        if requested in months:
            return requested, months, False
        eligible = [month for month in months if requested is None or month <= requested]
        return (eligible[0] if eligible else months[0]), months, True

    def research(self, context: ResearchContext) -> ResearchSnapshot:
        if context.recommendation_month.day != 1:
            raise ValueError("推荐月份必须为月首")
        if not 1 <= context.limit <= 100 or context.offset < 0:
            raise ValueError("分页参数非法")
        selected, months, fell_back = self.resolve_month(context.recommendation_month)
        now = datetime.now(UTC)
        if selected is None:
            quality = QualityStatus(
                QualityKind.EMPTY, "没有可用的券商金股月份", "券商金股同步", now
            )
            return ResearchSnapshot(
                self._empty_metrics(),
                [],
                self._page(0, context),
                [],
                [],
                [],
                [],
                quality,
                context.recommendation_month,
                [],
            )

        start = previous_month(selected, 11)
        facts = self._reader.recommendations(start, selected)
        candidates = [
            fact
            for fact in facts
            if (not context.broker_name or context.broker_name == fact.broker_name)
            and (not context.industry or context.industry == fact.industry)
        ]
        deduplicated: dict[tuple[date, str, str], RecommendationFact] = {}
        for fact in candidates:
            key = (fact.recommendation_month, fact.broker_name, fact.stock_id)
            if key not in deduplicated or fact.updated_at > deduplicated[key].updated_at:
                deduplicated[key] = fact
        filtered = list(deduplicated.values())
        by_month: dict[date, list[RecommendationFact]] = defaultdict(list)
        for fact in filtered:
            by_month[fact.recommendation_month].append(fact)
        current = by_month[selected]
        prior = by_month[previous_month(selected)]
        current_by_stock = self._by_stock(current)
        prior_by_stock = self._by_stock(prior)
        benchmark_history = self._safe_benchmark(400)
        benchmark = benchmark_history[-80:]
        quotes_by_stock = self._safe_quotes_batch(sorted({fact.stock_id for fact in filtered}), 400)
        month_set = set(months)
        prior_available = previous_month(selected) in month_set
        rows = [
            self._stock_row(
                stock_id,
                group,
                current_by_stock,
                prior_by_stock,
                by_month,
                benchmark,
                selected,
                month_set,
                quotes_by_stock.get(stock_id, [])[-80:],
            )
            for stock_id, group in current_by_stock.items()
        ]
        rows.sort(
            key=lambda row: self._sort_key(row, context.sort_by),
            reverse=context.sort_direction == "desc",
        )
        metrics = self._metrics(rows, current, prior, selected, by_month, month_set)
        quality = self._quality(rows, current, fell_back, prior_available, now)
        radar_rows = self._filter_radar_rows(rows, context.radar_filter)
        page_rows = radar_rows[context.offset : context.offset + context.limit]
        return ResearchSnapshot(
            metrics=metrics,
            items=page_rows,
            pagination=self._page(len(radar_rows), context),
            signals=self._signals(rows, selected),
            industries=self._industries(current, prior, now, prior_available),
            broker_ability=self._broker_ability(by_month, benchmark_history, quotes_by_stock, now),
            diffusion=self._diffusion(by_month, selected, now),
            quality=quality,
            selected_month=selected,
            available_months=months,
        )

    def stock_detail(self, stock_id: str, month: date) -> dict[str, Any] | None:
        facts = self._reader.recommendations(previous_month(month, 11), month)
        selected = [fact for fact in facts if fact.stock_id == stock_id]
        if not selected:
            return None
        current = [fact for fact in selected if fact.recommendation_month == month]
        identity = selected[-1]
        history = []
        for point_month in sorted({fact.recommendation_month for fact in selected}):
            brokers = {
                fact.broker_name for fact in selected if fact.recommendation_month == point_month
            }
            history.append({"month": point_month, "broker_count": len(brokers)})
        quotes = self._safe_quotes(stock_id)
        quality = (
            "insufficient"
            if not quotes
            else "partial"
            if identity.industry is None or identity.listing_status != "ACTIVE"
            else "ready"
        )
        return {
            "stock": self._stock_identity(identity),
            "industry": identity.industry,
            "recommendations": [
                {
                    "broker_name": fact.broker_name,
                    "recommendation_month": fact.recommendation_month,
                    "updated_at": fact.updated_at,
                }
                for fact in current
            ],
            "history": history,
            "latest_quote_date": quotes[-1].trade_date if quotes else None,
            "price_basis": "后复权收盘价；成交量与成交额为原始口径",
            "source": "券商金股同步、股票主数据、日线行情",
            "generated_at": datetime.now(UTC),
            "quality": quality if current else "partial",
        }

    @staticmethod
    def _by_stock(facts: list[RecommendationFact]) -> dict[str, list[RecommendationFact]]:
        result: dict[str, list[RecommendationFact]] = defaultdict(list)
        for fact in facts:
            result[fact.stock_id].append(fact)
        return result

    def _stock_row(
        self,
        stock_id: str,
        group: list[RecommendationFact],
        current_by_stock: dict[str, list[RecommendationFact]],
        prior_by_stock: dict[str, list[RecommendationFact]],
        by_month: dict[date, list[RecommendationFact]],
        benchmark: list[Any],
        selected: date,
        available_months: set[date],
        quotes: list[Any],
    ) -> dict[str, Any]:
        identity = group[0]
        brokers = {fact.broker_name for fact in group}
        prior_count = len({fact.broker_name for fact in prior_by_stock.get(stock_id, [])})
        broker_count = len(brokers)
        consecutive = 0
        probe = selected
        while stock_id in self._by_stock(by_month.get(probe, [])):
            consecutive += 1
            probe = previous_month(probe)
        excess = self._excess(quotes, benchmark, 20)
        breakout = len(quotes) >= 60 and quotes[-1].close >= max(
            point.close for point in quotes[-60:]
        )
        later_market_days = (
            sum(point.trade_date > quotes[-1].trade_date for point in benchmark) if quotes else 0
        )
        delayed = later_market_days > 1
        prior_available = previous_month(selected) in available_months
        is_new = stock_id not in prior_by_stock if prior_available else None
        month_delta = broker_count - prior_count if prior_available else None
        recent_counts = [
            len(
                {
                    fact.broker_name
                    for fact in by_month.get(previous_month(selected, offset), [])
                    if fact.stock_id == stock_id
                }
            )
            for offset in (1, 2)
        ]
        three_month_complete = all(
            previous_month(selected, offset) in available_months for offset in (1, 2)
        )
        three_month_peak = broker_count > max(recent_counts) if three_month_complete else None
        breakout = breakout and not delayed and identity.listing_status == "ACTIVE"
        status = self._status(breakout, broker_count, prior_count, consecutive, is_new, quotes)
        if identity.listing_status != "ACTIVE":
            status = "数据不足"
        components: dict[str, float | None] = {
            "consensus": min(broker_count / 5, 1) * 100,
            "warming": None if month_delta is None else min(max(month_delta, 0) / 5, 1) * 100,
            "continuity": min(consecutive / 6, 1) * 100,
            "excess": None if excess is None else max(0, min(100, 50 + excess * 5)),
        }
        score = None if delayed or identity.listing_status != "ACTIVE" else self._score(components)
        return {
            "stock": self._stock_identity(identity),
            "industry": identity.industry,
            "broker_count": broker_count,
            "brokers": sorted(brokers),
            "month_delta": month_delta,
            "is_new": is_new,
            "three_month_peak": three_month_peak,
            "breakout": breakout,
            "consecutive_months": consecutive,
            "excess_20d": excess,
            "status": status,
            "score": score,
            "score_components": components,
            "quality": "delayed"
            if delayed
            else (
                "insufficient"
                if (
                    len(quotes) < 60
                    or len(benchmark) < 21
                    or not prior_available
                    or identity.listing_status != "ACTIVE"
                )
                else "ready"
            ),
            "quality_explanation": "行情超过一个交易日窗口未更新"
            if delayed
            else (
                "缺少可用后复权行情"
                if not quotes
                else "历史比较、60日行情、基准样本或上市状态不足"
                if (
                    len(quotes) < 60
                    or len(benchmark) < 21
                    or not prior_available
                    or identity.listing_status != "ACTIVE"
                )
                else "数据可用"
            ),
        }

    @staticmethod
    def _filter_radar_rows(
        rows: list[dict[str, Any]], radar_filter: str | None
    ) -> list[dict[str, Any]]:
        if radar_filter in (None, "monthly"):
            return rows
        if radar_filter == "new":
            return [row for row in rows if row["is_new"] is True]
        if radar_filter == "consensus":
            return [row for row in rows if row["broker_count"] >= 5]
        if radar_filter == "warming":
            return [
                row for row in rows if row["month_delta"] is not None and row["month_delta"] > 0
            ]
        if radar_filter == "breakout":
            return [row for row in rows if row["breakout"] is True]
        if radar_filter == "excess":
            return [row for row in rows if row["excess_20d"] is not None]
        raise ValueError("机会雷达明细分类非法")

    @staticmethod
    def _score(components: dict[str, float | None]) -> float | None:
        available = {key: value for key, value in components.items() if value is not None}
        if len(available) < 2:
            return None
        weight = sum(SCORE_WEIGHTS[key] for key in available)
        return round(
            sum(float(value) * SCORE_WEIGHTS[key] for key, value in available.items()) / weight, 1
        )

    @staticmethod
    def _status(
        breakout: bool,
        count: int,
        prior: int,
        consecutive: int,
        new: bool | None,
        quotes: list[Any],
    ) -> str:
        if len(quotes) < 21 or new is None:
            return "数据不足"
        if breakout:
            return "突破"
        if new:
            return "新晋"
        if count >= 5:
            return "高共识"
        if count < prior:
            return "降温"
        if consecutive >= 3:
            return "持续"
        return "趋势强" if count > prior else "持续"

    def _metrics(
        self,
        rows: list[dict[str, Any]],
        current: list[RecommendationFact],
        prior: list[RecommendationFact],
        selected: date,
        by_month: dict[date, list[RecommendationFact]],
        available_months: set[date],
    ) -> dict[str, Any]:
        valid_excess = [row["excess_20d"] for row in rows if row["excess_20d"] is not None]
        prior_month = previous_month(selected)
        prior_prior_month = previous_month(selected, 2)
        comparison_available = prior_month in available_months
        prior_new_change_available = comparison_available and prior_prior_month in available_months
        current_new_count = sum(row["is_new"] is True for row in rows)
        prior_stock_ids = {fact.stock_id for fact in prior}
        prior_prior_stock_ids = {fact.stock_id for fact in by_month.get(prior_prior_month, [])}
        prior_new_count = len(prior_stock_ids - prior_prior_stock_ids)
        warming_three_months = []
        for offset in (2, 1, 0):
            month = previous_month(selected, offset)
            comparison_month = previous_month(month)
            count: int | None = None
            if month in available_months and comparison_month in available_months:
                month_stocks = self._by_stock(by_month.get(month, []))
                comparison_stocks = self._by_stock(by_month.get(comparison_month, []))
                count = sum(
                    len({fact.broker_name for fact in facts})
                    > len({fact.broker_name for fact in comparison_stocks.get(stock_id, [])})
                    for stock_id, facts in month_stocks.items()
                )
            warming_three_months.append({"month": month, "count": count})
        return {
            "monthly_count": len(rows),
            "broker_count": len({fact.broker_name for fact in current}),
            "industry_count": len({fact.industry for fact in current if fact.industry}),
            "new_count": current_new_count if comparison_available else None,
            "new_change": current_new_count - prior_new_count
            if prior_new_change_available
            else None,
            "consensus_count": sum(row["broker_count"] >= 5 for row in rows),
            "warming_count": sum(row["month_delta"] > 0 for row in rows)
            if comparison_available
            else None,
            "warming_three_months": warming_three_months,
            "breakout_count": sum(row["breakout"] for row in rows),
            "average_excess_20d": round(fmean(valid_excess), 2) if valid_excess else None,
            "excess_sample_count": len(valid_excess),
            "benchmark": "沪深300",
            "recommendation_month": selected,
        }

    def _signals(self, rows: list[dict[str, Any]], selected: date) -> list[dict[str, Any]]:
        ranked: list[tuple[int, dict[str, Any]]] = []
        period = f"{previous_month(selected):%Y-%m} 至 {selected:%Y-%m}"
        for row in rows:
            common = {
                "subject_type": "stock",
                "stock": row["stock"],
                "industry": None,
                "comparison_period": period,
                "data_time": selected,
                "quality": row["quality"],
            }
            if row["is_new"] is True and row["broker_count"] >= 2:
                ranked.append(
                    (
                        90,
                        common
                        | {
                            "type": "新增多家推荐",
                            "summary": f"本月新晋并获 {row['broker_count']} 家券商推荐",
                            "trigger_rule": "上月未入选且本月至少 2 家不同券商推荐",
                        },
                    )
                )
            if row["three_month_peak"] is True:
                ranked.append(
                    (
                        80,
                        common
                        | {
                            "type": "三个月新高",
                            "summary": f"推荐券商数升至 {row['broker_count']} 家",
                            "trigger_rule": "本月推荐券商数严格高于前两个月",
                        },
                    )
                )
            if row["breakout"]:
                ranked.append(
                    (
                        70,
                        common
                        | {
                            "type": "60日新高",
                            "summary": "最新可用后复权收盘价达到 60 个交易日新高",
                            "trigger_rule": (
                                "最新后复权收盘价不低于最近 60 个交易日最高值且行情未延迟"
                            ),
                        },
                    )
                )
            if row["consecutive_months"] >= 3:
                ranked.append(
                    (
                        60,
                        common
                        | {
                            "type": "连续入选",
                            "summary": f"已连续入选 {row['consecutive_months']} 个月",
                            "trigger_rule": "连续至少 3 个完整月份存在有效推荐",
                        },
                    )
                )
            if row["month_delta"] is not None and row["month_delta"] <= -2:
                ranked.append(
                    (
                        50,
                        common
                        | {
                            "type": "推荐下降",
                            "summary": f"本月推荐券商数较上月 {row['month_delta']:+d}",
                            "trigger_rule": "本月推荐券商数较上月减少至少 2 家",
                        },
                    )
                )
            excess = row["excess_20d"]
            if (
                excess is not None
                and row["month_delta"] is not None
                and (
                    (row["month_delta"] >= 2 and excess < 0)
                    or (row["month_delta"] <= -2 and excess > 0)
                )
            ):
                ranked.append(
                    (
                        40,
                        common
                        | {
                            "type": "热度表现背离",
                            "summary": (
                                f"推荐变化 {row['month_delta']:+d} 家，20 日超额 {excess:+.2f}%"
                            ),
                            "trigger_rule": "推荐券商数变化至少 2 家且与 20 日超额收益方向相反",
                        },
                    )
                )

        industries: dict[str, dict[str, int]] = defaultdict(
            lambda: {"broker_delta": 0, "stock_count": 0}
        )
        for row in rows:
            if row["industry"] and row["month_delta"] is not None:
                industries[row["industry"]]["broker_delta"] += row["month_delta"]
                industries[row["industry"]]["stock_count"] += 1
        for industry, values in industries.items():
            if values["broker_delta"] >= 2:
                ranked.append(
                    (
                        65,
                        {
                            "subject_type": "industry",
                            "stock": None,
                            "industry": industry,
                            "type": "行业升温",
                            "summary": f"行业内股票推荐券商净增合计 {values['broker_delta']} 家次",
                            "comparison_period": period,
                            "trigger_rule": "同一行业股票推荐券商数的环比净增合计至少 2 家次",
                            "data_time": selected,
                            "quality": "ready",
                        },
                    )
                )
        ranked.sort(
            key=lambda item: (
                -item[0],
                (item[1].get("stock") or {}).get("security_code", ""),
                item[1].get("industry") or "",
            )
        )
        return [item for _, item in ranked[:12]]

    @staticmethod
    def _industries(
        current: list[RecommendationFact],
        prior: list[RecommendationFact],
        now: datetime,
        prior_available: bool,
    ) -> list[dict[str, Any]]:
        names = {fact.industry for fact in current if fact.industry}
        result: list[dict[str, Any]] = []
        for name in names:
            current_facts = [fact for fact in current if fact.industry == name]
            prior_facts = [fact for fact in prior if fact.industry == name]
            result.append(
                {
                    "industry": name,
                    "recommendation_records": len(current_facts),
                    "stock_count": len({f.stock_id for f in current_facts}),
                    "broker_count": len({f.broker_name for f in current_facts}),
                    "month_delta": len(current_facts) - len(prior_facts)
                    if prior_available
                    else None,
                    "heat_rank": 0,
                    "quality": "ready" if prior_available else "insufficient",
                    "generated_at": now,
                }
            )
        result.sort(key=lambda item: item["recommendation_records"], reverse=True)
        for rank, item in enumerate(result, 1):
            item["heat_rank"] = rank
        return result

    def _broker_ability(
        self,
        by_month: dict[date, list[RecommendationFact]],
        benchmark: list[Any],
        quotes_by_stock: dict[str, list[Any]],
        now: datetime,
    ) -> list[dict[str, Any]]:
        samples: dict[str, list[float]] = defaultdict(list)
        possible: dict[str, int] = defaultdict(int)
        seen: set[tuple[str, str, date]] = set()
        for month, facts in by_month.items():
            month_benchmark = [point for point in benchmark if point.trade_date >= month][:21]
            for fact in facts:
                key = (fact.broker_name, fact.stock_id, month)
                if key in seen:
                    continue
                seen.add(key)
                possible[fact.broker_name] += 1
                month_quotes = [
                    point
                    for point in quotes_by_stock.get(fact.stock_id, [])
                    if point.trade_date >= month
                ][:21]
                excess = self._excess(
                    month_quotes,
                    month_benchmark,
                    20,
                )
                if excess is not None:
                    samples[fact.broker_name].append(excess)
        result = []
        for broker in sorted(possible):
            values = samples[broker]
            count = len(values)
            enough = count >= MIN_BROKER_SAMPLES
            average = fmean(values) if values else None
            grade = (
                None
                if not enough
                else (
                    "A"
                    if average is not None and average >= 3
                    else "B"
                    if average is not None and average >= 0
                    else "C"
                )
            )
            result.append(
                {
                    "broker_name": broker,
                    "sample_count": count,
                    "average_excess_20d": round(average, 2) if average is not None else None,
                    "positive_ratio": round(sum(v > 0 for v in values) / count * 100, 1)
                    if count
                    else None,
                    "coverage": round(count / possible[broker] * 100, 1) if possible[broker] else 0,
                    "minimum_sample_count": MIN_BROKER_SAMPLES,
                    "grade": grade,
                    "period_start": min(by_month) if by_month else None,
                    "period_end": max(by_month) if by_month else None,
                    "benchmark": "沪深300",
                    "return_basis": "推荐月首后首个共同交易日至第 20 个共同交易日的后复权超额收益",
                    "quality": "ready" if enough else "insufficient",
                    "generated_at": now,
                }
            )
        return result

    @staticmethod
    def _diffusion(
        by_month: dict[date, list[RecommendationFact]], selected: date, now: datetime
    ) -> list[dict[str, Any]]:
        points = []
        previous: int | None = None
        for offset in range(7, -1, -1):
            month = previous_month(selected, offset)
            available = month in by_month
            count = len({fact.stock_id for fact in by_month[month]}) if available else None
            points.append(
                {
                    "month": month,
                    "stock_count": count,
                    "month_delta": None if previous is None or count is None else count - previous,
                    "quality": "ready" if available else "insufficient",
                    "count_basis": "推荐月份内按规范股票身份去重",
                    "generated_at": now,
                }
            )
            previous = count
        return points

    def _quality(
        self,
        rows: list[dict[str, Any]],
        current: list[RecommendationFact],
        fell_back: bool,
        prior_available: bool,
        now: datetime,
    ) -> QualityStatus:
        if not current:
            return QualityStatus(QualityKind.EMPTY, "当前筛选范围无推荐记录", "券商金股同步", now)
        if (
            fell_back
            or not prior_available
            or any(row["quality"] != "ready" for row in rows)
            or any(fact.industry is None for fact in current)
        ):
            reason = "部分股票行情或行业分类不可用"
            if fell_back:
                reason += "；已回退到最近可用月份"
            if not prior_available:
                reason += "；上月比较数据不足"
            return QualityStatus(
                QualityKind.PARTIAL, reason, "券商金股同步、股票主数据、日线行情", now
            )
        return QualityStatus(
            QualityKind.READY, "数据完整", "券商金股同步、股票主数据、日线行情", now
        )

    def _safe_quotes(
        self, stock_id: str, limit: int = 80, start_date: date | None = None
    ) -> list[Any]:
        try:
            return self._reader.stock_quotes(stock_id, limit, start_date)
        except Exception:
            return []

    def _safe_quotes_batch(self, stock_ids: list[str], limit: int = 400) -> dict[str, list[Any]]:
        try:
            return self._reader.stock_quotes_batch(stock_ids, limit)
        except Exception:
            return {}

    def _safe_benchmark(self, limit: int = 80, start_date: date | None = None) -> list[Any]:
        try:
            return self._reader.benchmark_quotes(limit, start_date)
        except Exception:
            return []

    @staticmethod
    def _excess(quotes: list[Any], benchmark: list[Any], window: int) -> float | None:
        stock_by_date = {point.trade_date: point.close for point in quotes}
        benchmark_by_date = {point.trade_date: point.close for point in benchmark}
        common_dates = sorted(stock_by_date.keys() & benchmark_by_date.keys())
        if len(common_dates) <= window:
            return None
        start, end = common_dates[-window - 1], common_dates[-1]
        stock_return = (stock_by_date[end] / stock_by_date[start] - 1) * 100
        benchmark_return = (benchmark_by_date[end] / benchmark_by_date[start] - 1) * 100
        return float(round(stock_return - benchmark_return, 2))

    @staticmethod
    def _stock_identity(fact: RecommendationFact) -> dict[str, str]:
        return {
            "stock_id": fact.stock_id,
            "security_code": fact.security_code,
            "name": fact.stock_name,
            "market_code": "CN-S",
            "listing_status": fact.listing_status,
        }

    @staticmethod
    def _sort_key(row: dict[str, Any], field: str) -> tuple[Any, str]:
        value = row.get(field)
        return ((-1 if value is None else value), row["stock"]["security_code"])

    @staticmethod
    def _page(total: int, context: ResearchContext) -> dict[str, Any]:
        return {
            "limit": context.limit,
            "offset": context.offset,
            "total": total,
            "has_more": context.offset + context.limit < total,
        }

    @staticmethod
    def _empty_metrics() -> dict[str, Any]:
        return {
            "monthly_count": 0,
            "broker_count": 0,
            "industry_count": 0,
            "new_count": None,
            "new_change": None,
            "consensus_count": 0,
            "warming_count": None,
            "warming_three_months": [],
            "breakout_count": 0,
            "average_excess_20d": None,
            "excess_sample_count": 0,
            "benchmark": "沪深300",
        }
