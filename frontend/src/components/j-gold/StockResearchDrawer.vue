<script setup lang="ts">
import { useQuery } from "@tanstack/vue-query";
import { computed } from "vue";

import { apiRequest } from "@/api/client/http";
import { jGoldKeys } from "@/api/query-keys/j-gold";
import type { RadarItem, StockIdentity } from "@/composables/useJGoldResearch";
import AddToWatchlistAction from "./AddToWatchlistAction.vue";

interface Detail {
  stock: StockIdentity;
  industry: string | null;
  recommendations: Array<{
    broker_name: string;
    recommendation_month: string;
    updated_at: string;
  }>;
  history: Array<{ month: string; broker_count: number }>;
  latest_quote_date: string | null;
  price_basis: string;
  source: string;
  generated_at: string;
  quality: string;
}

const props = defineProps<{ item: RadarItem | null; month: string }>();
const emit = defineEmits<{ close: [] }>();
const stockId = computed(() => props.item?.stock.stock_id ?? "");
const detail = useQuery({
  queryKey: computed(() => jGoldKeys.detail(stockId.value, props.month)),
  enabled: computed(() => Boolean(stockId.value)),
  queryFn: () =>
    apiRequest<Detail>({
      url: `/j-gold/stocks/${encodeURIComponent(stockId.value)}`,
      params: { recommendation_month: `${props.month}-01` },
    }),
});
</script>

<template>
  <div v-if="item" class="drawer-backdrop" @click.self="emit('close')">
    <aside aria-label="股票研究详情">
      <header>
        <div>
          <small>{{ item.stock.security_code }}</small>
          <h2>{{ item.stock.name }}</h2>
        </div>
        <button aria-label="关闭详情" @click="emit('close')">
          <i class="pi pi-times" />
        </button>
      </header>
      <div v-if="detail.isPending.value" class="state">正在加载可追溯详情…</div>
      <div v-else-if="detail.error.value" class="state">
        详情加载失败，请关闭后重试。
      </div>
      <template v-else-if="detail.data.value">
        <section>
          <h3>本月推荐券商</h3>
          <ul class="recommendations">
            <li
              v-for="rec in detail.data.value.recommendations"
              :key="`${rec.broker_name}-${rec.updated_at}`"
            >
              <strong>{{ rec.broker_name }}</strong>
              <small
                >推荐月 {{ rec.recommendation_month.slice(0, 7) }} · 记录更新
                {{ new Date(rec.updated_at).toLocaleString("zh-CN") }}</small
              >
            </li>
          </ul>
        </section>
        <section>
          <h3>综合评分依据</h3>
          <dl class="score-components">
            <div v-for="(value, name) in item.score_components" :key="name">
              <dt>{{ name }}</dt>
              <dd>{{ value ?? "数据不足" }}</dd>
            </div>
          </dl>
          <p>评分仅用于研究优先级排序，不预测收益。</p>
        </section>
        <section>
          <h3>历史推荐趋势</h3>
          <ol>
            <li v-for="point in detail.data.value.history" :key="point.month">
              <time>{{ point.month.slice(0, 7) }}</time
              ><strong>{{ point.broker_count }} 家券商</strong>
            </li>
          </ol>
        </section>
        <section>
          <h3>口径与来源</h3>
          <p>行业：{{ detail.data.value.industry ?? "未分类" }}</p>
          <p>
            研究状态：{{ item.status }} · {{ item.quality_explanation }}（{{
              detail.data.value.quality
            }}）
          </p>
          <p>{{ detail.data.value.price_basis }}</p>
          <p>来源：{{ detail.data.value.source }}</p>
          <p>
            最新行情：{{ detail.data.value.latest_quote_date ?? "数据不足" }}
          </p>
          <p>
            生成时间：{{
              new Date(detail.data.value.generated_at).toLocaleString("zh-CN")
            }}
          </p>
        </section>
        <section>
          <h3>加入自选</h3>
          <AddToWatchlistAction :stock-id="stockId" />
        </section>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 30;
  display: flex;
  justify-content: flex-end;
  background: rgb(10 18 32 / 45%);
}
aside {
  width: min(480px, 100%);
  height: 100%;
  overflow-y: auto;
  padding: 24px;
  background: var(--lk-surface);
  box-shadow: -18px 0 48px rgb(0 0 0 / 18%);
}
header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--lk-border);
}
h2,
h3,
p {
  margin: 0;
}
h2 {
  font-size: 1.5rem;
}
h3 {
  margin-bottom: 12px;
  font-size: 0.9rem;
}
header small,
p {
  color: var(--lk-text-secondary);
  font-size: 0.8rem;
}
header button {
  width: 44px;
  height: 44px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}
section {
  padding: 20px 0;
  border-bottom: 1px solid var(--lk-border);
}
.recommendations {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.recommendations li {
  display: grid;
  gap: 3px;
  padding: 6px 9px;
  border-radius: 7px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-size: 0.78rem;
}
.recommendations small {
  color: var(--lk-text-secondary);
  font-size: 0.72rem;
}
.score-components {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin: 0 0 10px;
}
.score-components div {
  padding: 10px;
  border: 1px solid var(--lk-border);
  border-radius: 8px;
}
.score-components dt {
  color: var(--lk-text-secondary);
  font-size: 0.72rem;
}
.score-components dd {
  margin: 4px 0 0;
  font-weight: 700;
}
ol {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
li {
  display: flex;
  justify-content: space-between;
}
.state {
  padding: 32px 0;
  color: var(--lk-text-secondary);
}
</style>
