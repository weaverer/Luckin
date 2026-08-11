<script setup lang="ts">
import { BarChart, LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import type { EChartsOption } from "echarts";
import { use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import VChart from "vue-echarts";

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
]);

defineProps<{ option: EChartsOption; label: string }>();
</script>

<template>
  <VChart class="base-chart" :option="option" autoresize :aria-label="label" />
</template>

<style scoped>
.base-chart {
  width: 100%;
  min-height: 420px;
}

@media (max-width: 620px) {
  .base-chart {
    min-height: 340px;
  }
}
</style>
