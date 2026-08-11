<script setup lang="ts">
import { computed, ref } from "vue";
import Tab from "primevue/tab";
import TabList from "primevue/tablist";
import Tabs from "primevue/tabs";

import AsyncState from "@/components/common/AsyncState.vue";
import { useStocks } from "@/composables/useStocks";
import { listingStatusLabels, venueLabels } from "@/utils/market";

const stocks = useStocks();
const venue = ref("");
const status = ref("");
const statusTabs = [
  { value: "", label: "全部" },
  { value: "ACTIVE", label: "上市" },
  { value: "SUSPENDED", label: "暂停上市" },
  { value: "DELISTED", label: "已退市" },
  { value: "PENDING", label: "待上市" },
];
const state = computed(() =>
  stocks.loading.value
    ? "loading"
    : stocks.error.value
      ? "error"
      : stocks.items.value.length
        ? "ready"
        : "empty",
);

function apply(): void {
  stocks.applyFilters(venue.value, status.value);
  stocks.search();
}

function changeStatus(value: string | number): void {
  status.value = String(value);
  apply();
}
</script>

<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <p class="eyebrow">CN-S 股票主数据</p>
        <h1 class="page-heading">股票与行情</h1>
      </div>
      <p class="result-count numeric">
        {{ stocks.pagination.value.total }} 只股票
      </p>
    </header>

    <form class="toolbar" role="search" @submit.prevent="apply">
      <label class="search-field" for="stock-search">
        代码或名称
        <input
          id="stock-search"
          v-model.trim="stocks.draftQuery.value"
          placeholder="例如 600519 或 贵州茅台"
        />
      </label>
      <label>
        交易所
        <select v-model="venue">
          <option value="">全部交易所</option>
          <option value="XSHG">上海</option>
          <option value="XSHE">深圳</option>
          <option value="XBSE">北京</option>
        </select>
      </label>
      <button class="primary" type="submit">查询股票</button>
    </form>

    <Tabs class="status-tabs" :value="status" @update:value="changeStatus">
      <TabList>
        <Tab v-for="tab in statusTabs" :key="tab.value" :value="tab.value">
          {{ tab.label }}
        </Tab>
      </TabList>
    </Tabs>

    <AsyncState
      :state="state"
      :title="state === 'empty' ? '没有匹配的股票' : ''"
      :message="
        state === 'empty'
          ? '调整代码、名称或筛选条件后重新查询。'
          : stocks.error.value
      "
      refreshable
      @refresh="stocks.refresh"
    >
      <section class="stock-table" :aria-busy="stocks.fetching.value">
        <div class="table-head" aria-hidden="true">
          <span>代码 / 名称</span><span>交易所</span><span>状态</span><span />
        </div>
        <RouterLink
          v-for="stock in stocks.items.value"
          :key="stock.stock_id"
          class="stock-row"
          :to="{ name: 'stock-detail', params: { stockId: stock.stock_id } }"
        >
          <span class="identity">
            <strong class="numeric">{{ stock.security_code }}</strong>
            <small>{{ stock.name }}</small>
          </span>
          <span>{{ venueLabels[stock.venue_code] ?? stock.venue_code }}</span>
          <span class="status-tag" :data-status="stock.listing_status">
            {{ listingStatusLabels[stock.listing_status] ?? "未知" }}
          </span>
          <i class="pi pi-chevron-right" aria-hidden="true" />
        </RouterLink>
      </section>

      <nav class="pagination" aria-label="股票分页">
        <button
          type="button"
          :disabled="stocks.pagination.value.offset === 0"
          @click="stocks.previous"
        >
          上一页
        </button>
        <span class="pagination-copy numeric">
          第 {{ stocks.pagination.value.offset + 1 }}–{{
            Math.min(
              stocks.pagination.value.offset + stocks.items.value.length,
              stocks.pagination.value.total,
            )
          }}
          条 / 共 {{ stocks.pagination.value.total }} 条
        </span>
        <button
          type="button"
          :disabled="!stocks.pagination.value.has_more"
          @click="stocks.next"
        >
          下一页
        </button>
      </nav>
    </AsyncState>
  </div>
</template>

<style scoped>
.page-header,
.toolbar,
.pagination {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 12px;
}

.eyebrow,
.result-count {
  margin: 0 0 6px;
  color: var(--lk-text-muted);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.toolbar {
  justify-content: flex-start;
  padding: 16px;
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface-soft);
}

label {
  display: grid;
  gap: 6px;
  color: var(--lk-text-secondary);
  font-size: 0.82rem;
}

.search-field {
  flex: 1 1 320px;
}

input,
select,
button {
  min-height: 42px;
  padding: 0 12px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-text);
  background: var(--lk-surface);
}

button {
  cursor: pointer;
}

button.primary {
  border-color: var(--lk-primary);
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}

.stock-table {
  overflow: hidden;
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface);
}

.status-tabs {
  width: fit-content;
  max-width: 100%;
  overflow-x: auto;
  padding: 4px;
  border: 1px solid var(--lk-border);
  border-radius: 12px;
  background: var(--lk-surface-soft);
}
:deep(.status-tabs .p-tablist-tab-list) {
  gap: 3px;
  border: 0;
  background: transparent;
}
:deep(.status-tabs .p-tab) {
  min-height: 36px;
  padding: 7px 14px;
  border: 0;
  border-radius: 8px;
  color: var(--lk-text-secondary);
  font-weight: 700;
}
:deep(.status-tabs .p-tab-active) {
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
  box-shadow: 0 2px 8px color-mix(in srgb, var(--lk-primary) 22%, transparent);
}
:deep(.status-tabs .p-tablist-active-bar) {
  display: none;
}

.table-head,
.stock-row {
  display: grid;
  grid-template-columns: minmax(220px, 1.6fr) 0.7fr 0.7fr 24px;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
}

.table-head {
  color: var(--lk-text-muted);
  background: var(--lk-surface-soft);
  font-size: 0.75rem;
  font-weight: 700;
}

.stock-row {
  min-height: 58px;
  border-top: 1px solid var(--lk-border);
  color: var(--lk-text);
  text-decoration: none;
  transition: background 140ms ease;
}

.stock-row:hover {
  background: var(--lk-surface-hover);
}

.identity {
  display: grid;
  gap: 3px;
}

.identity small {
  color: var(--lk-text-secondary);
}

.numeric {
  font-variant-numeric: tabular-nums;
}

.status-tag {
  width: fit-content;
  padding: 4px 7px;
  border-radius: 7px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-size: 0.75rem;
  font-weight: 700;
}
.status-tag[data-status="SUSPENDED"],
.status-tag[data-status="PENDING"] {
  color: var(--lk-warning);
  background: color-mix(in srgb, var(--lk-warning) 12%, transparent);
}
.status-tag[data-status="DELISTED"] {
  color: var(--lk-danger);
  background: color-mix(in srgb, var(--lk-danger) 12%, transparent);
}

.pagination {
  align-items: center;
  justify-content: flex-end;
}
.pagination button,
.pagination-copy {
  display: inline-flex;
  min-height: 42px;
  align-items: center;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

@media (max-width: 860px) {
  .toolbar {
    flex-wrap: wrap;
  }

  .table-head,
  .stock-row {
    grid-template-columns: minmax(180px, 1fr) 90px 24px;
  }

  .table-head span:nth-child(3),
  .stock-row .status-tag {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stock-row {
    transition: none;
  }
}
</style>
