<script setup lang="ts">
defineProps<{ points: Array<Record<string, unknown>> }>();
</script>
<template>
  <section class="module">
    <header>
      <h2>市场推荐扩散</h2>
      <span>最近 8 个完整月份</span>
    </header>
    <div
      class="chart"
      role="img"
      :aria-label="
        points
          .map((p) =>
            p.stock_count == null
              ? `${String(p.month).slice(0, 7)} 数据不足`
              : `${String(p.month).slice(0, 7)} ${p.stock_count}只`,
          )
          .join('，')
      "
    >
      <div v-for="point in points" :key="String(point.month)" class="bar">
        <span>{{ point.stock_count ?? "—" }}</span
        ><i
          :style="{ height: `${Math.max(8, Number(point.stock_count))}px` }"
        /><small>{{ String(point.month).slice(5, 7) }}月</small
        ><em>{{
          point.month_delta == null
            ? "不可比"
            : `${Number(point.month_delta) > 0 ? "+" : ""}${point.month_delta}`
        }}</em>
      </div>
    </div>
  </section>
</template>
<style scoped>
.module {
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface);
  overflow: hidden;
}
header {
  display: flex;
  justify-content: space-between;
  padding: 18px 20px;
  border-bottom: 1px solid var(--lk-border);
}
h2 {
  margin: 0;
  font-size: 1rem;
}
header span,
small,
.bar > span,
em {
  color: var(--lk-text-secondary);
  font-size: 0.72rem;
}
em {
  font-style: normal;
}
.chart {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-height: 190px;
  padding: 20px;
}
.bar {
  display: grid;
  flex: 1;
  align-items: end;
  gap: 6px;
  text-align: center;
}
.bar i {
  display: block;
  max-height: 110px;
  border-radius: 5px 5px 0 0;
  background: var(--lk-primary);
}
</style>
