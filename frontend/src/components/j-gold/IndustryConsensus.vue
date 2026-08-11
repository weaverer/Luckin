<script setup lang="ts">
defineProps<{ industries: Array<Record<string, unknown>> }>();
const emit = defineEmits<{ select: [name: string] }>();
</script>
<template>
  <section class="module">
    <header>
      <h2>行业共识温度</h2>
      <span>记录数、股票数与券商数分别统计</span>
    </header>
    <div v-if="industries.length" class="industries">
      <button
        v-for="item in industries"
        :key="String(item.industry)"
        @click="emit('select', String(item.industry))"
      >
        <strong>#{{ item.heat_rank }} {{ item.industry }}</strong
        ><span
          >{{ item.recommendation_records }} 条推荐 ·
          {{ item.stock_count }} 只股票 · {{ item.broker_count }} 家券商</span
        ><small
          >较上月
          {{
            item.month_delta == null
              ? "—"
              : `${Number(item.month_delta) > 0 ? "+" : ""}${item.month_delta}`
          }}
          · {{ item.quality }}</small
        >
      </button>
    </div>
    <p v-else>当前股票主数据尚无可验证行业分类，行业模块按“部分覆盖”降级。</p>
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
.industries {
  display: grid;
  padding: 8px 18px;
}
button {
  display: grid;
  grid-template-columns: 1fr 2fr auto;
  gap: 12px;
  padding: 13px 0;
  border: 0;
  border-bottom: 1px solid var(--lk-border);
  color: var(--lk-text);
  background: none;
  text-align: left;
  cursor: pointer;
}
p {
  padding: 24px;
}
</style>
