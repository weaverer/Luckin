<script setup lang="ts">
defineProps<{ signals: Array<Record<string, unknown>> }>();
function stockName(signal: Record<string, unknown>): string {
  const stock = signal.stock as Record<string, unknown> | undefined;
  if (typeof stock?.name === "string") return stock.name;
  return typeof signal.industry === "string" ? signal.industry : "研究对象";
}
</script>
<template>
  <section class="module">
    <header>
      <h2>金股异动</h2>
      <span>事实触发，不生成无依据结论</span>
    </header>
    <ul v-if="signals.length">
      <li v-for="(signal, i) in signals" :key="i">
        <i class="pi pi-bolt" aria-hidden="true" />
        <div>
          <strong>{{ stockName(signal) }} · {{ signal.type }}</strong>
          <p>{{ signal.summary }}</p>
          <small
            >{{ signal.comparison_period }} · {{ signal.trigger_rule }} ·
            数据时间 {{ String(signal.data_time).slice(0, 10) }} ·
            {{ signal.quality }}</small
          >
        </div>
      </li>
    </ul>
    <p v-else class="empty">当前范围没有满足规则且依据完整的异动。</p>
  </section>
</template>
<style scoped>
.module {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: var(--j-gold-primary-panel-height, auto);
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
h2,
p {
  margin: 0;
}
h2 {
  font-size: 1rem;
}
header span,
small,
p {
  color: var(--lk-text-secondary);
  font-size: 0.76rem;
}
ul {
  overflow-y: auto;
  min-height: 0;
  list-style: none;
  margin: 0;
  padding: 0;
}
li {
  display: flex;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--lk-border);
}
li:last-child {
  border: 0;
}
li i {
  color: var(--lk-fortune);
}
li div {
  display: grid;
  gap: 5px;
}
.empty {
  padding: 24px;
}
</style>
