<script setup lang="ts">
import type { EChartsOption } from "echarts";
import { computed } from "vue";

import BaseChart from "@/components/charts/BaseChart.vue";
import type { DailyQuote } from "@/composables/useStocks";
import { useThemeStore } from "@/stores/theme";

const props = defineProps<{ quotes: DailyQuote[] }>();
const theme = useThemeStore();

function token(name: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

const summary = computed(() => {
  const first = props.quotes.at(0);
  const latest = props.quotes.at(-1);
  if (!first || !latest) return "暂无可绘制的日线行情";
  return [
    first.trade_date,
    "至",
    latest.trade_date + "，最新收盘",
    latest.close + "，涨跌幅",
    latest.pct_chg + "%",
  ].join(" ");
});

const option = computed<EChartsOption>(() => {
  const gridOpacity = theme.mode === "dark" ? 0.5 : 0.45;
  return {
    animationDuration:
      (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false)
        ? 0
        : 180,
    grid: [
      { left: 56, right: 24, top: 24, height: "58%" },
      { left: 56, right: 24, top: "76%", height: "12%" },
    ],
    tooltip: {
      trigger: "axis",
      backgroundColor: token("--lk-surface"),
      borderColor: token("--lk-border"),
      textStyle: { color: token("--lk-text") },
    },
    xAxis: [
      {
        type: "category",
        data: props.quotes.map((item) => item.trade_date),
        boundaryGap: false,
        axisLabel: { color: token("--lk-text-muted"), hideOverlap: true },
        axisLine: { lineStyle: { color: token("--lk-border") } },
      },
      {
        type: "category",
        gridIndex: 1,
        data: props.quotes.map((item) => item.trade_date),
        axisLabel: { show: false },
        axisLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: "value",
        scale: true,
        axisLabel: { color: token("--lk-text-muted") },
        splitLine: {
          lineStyle: { color: token("--lk-border"), opacity: gridOpacity },
        },
      },
      {
        type: "value",
        gridIndex: 1,
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 0, end: 100 }],
    series: [
      {
        name: "后复权收盘价",
        type: "line",
        showSymbol: false,
        smooth: 0.15,
        data: props.quotes.map((item) => Number(item.close)),
        lineStyle: { color: token("--lk-primary"), width: 2 },
        itemStyle: { color: token("--lk-fortune") },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: props.quotes.map((item) => Number(item.vol)),
        itemStyle: { color: token("--lk-accent"), opacity: 0.65 },
      },
    ],
  };
});
</script>

<template>
  <figure>
    <BaseChart :option="option" :label="summary" />
    <figcaption>{{ summary }} · 价格为后复权，成交量为原始成交量</figcaption>
  </figure>
</template>

<style scoped>
figure {
  margin: 0;
}

figcaption {
  margin-top: 8px;
  color: var(--lk-text-muted);
  font-size: 0.8rem;
}
</style>
