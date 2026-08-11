<script setup lang="ts">
defineProps<{ items: Array<Record<string, unknown>> }>();
</script>
<template>
  <section class="module">
    <header>
      <h2>券商选股能力</h2>
      <span>最近 12 个完整月份 · 最低 20 个有效样本</span>
    </header>
    <div class="list">
      <article
        v-for="item in items.slice(0, 8)"
        :key="String(item.broker_name)"
      >
        <strong>{{ item.broker_name }}</strong>
        <span>
          有效样本 {{ item.sample_count }} · 覆盖率 {{ item.coverage }}%<br />
          20 日平均超额 {{ item.average_excess_20d ?? "—" }}% · 正样本
          {{ item.positive_ratio ?? "—" }}%
        </span>
        <b>{{ item.grade ?? "样本不足" }}</b>
      </article>
      <p v-if="!items.length">暂无足够的历史行情与基准样本。</p>
      <p v-else class="basis">
        基准：沪深 300 · 后复权价格；每个“券商—股票—推荐月份”为一条样本。
      </p>
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
h2,
p {
  margin: 0;
}
h2 {
  font-size: 1rem;
}
header span,
article span,
p {
  color: var(--lk-text-secondary);
  font-size: 0.76rem;
}
.list {
  padding: 8px 18px;
}
article {
  display: grid;
  grid-template-columns: 1fr 1.4fr auto;
  gap: 12px;
  padding: 13px 0;
  border-bottom: 1px solid var(--lk-border);
}
b {
  color: var(--lk-fortune);
}
p {
  padding: 18px 0;
}
.basis {
  padding: 12px 0;
  border-top: 1px solid var(--lk-border);
}
</style>
