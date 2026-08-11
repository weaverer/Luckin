<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import DailyQuoteChart from "@/components/charts/DailyQuoteChart.vue";
import AppSurface from "@/components/common/AppSurface.vue";
import AsyncState from "@/components/common/AsyncState.vue";
import DataFreshness from "@/components/common/DataFreshness.vue";
import { useStockDetail } from "@/composables/useStocks";
import {
  listingStatusLabels,
  marketDataStatusLabels,
  venueLabels,
} from "@/utils/market";

const route = useRoute();
const stockId = computed(() => String(route.params.stockId));
const market = useStockDetail(stockId);
const state = computed(() =>
  market.loading.value
    ? "loading"
    : market.error.value
      ? "error"
      : market.stock.value
        ? "ready"
        : "empty",
);
const statusCopy = {
  CURRENT: {
    icon: "pi-check-circle",
    label: marketDataStatusLabels.CURRENT,
    tone: "current",
  },
  STALE: {
    icon: "pi-history",
    label: marketDataStatusLabels.STALE,
    tone: "stale",
  },
  MISSING: {
    icon: "pi-minus-circle",
    label: marketDataStatusLabels.MISSING,
    tone: "missing",
  },
} as const;
</script>

<template>
  <div class="page-stack">
    <RouterLink class="back-link" :to="{ name: 'stocks' }">
      <i class="pi pi-arrow-left" aria-hidden="true" /> 返回股票列表
    </RouterLink>
    <AsyncState
      :state="state"
      :message="market.error.value"
      refreshable
      @refresh="market.refresh"
    >
      <template v-if="market.stock.value">
        <header class="instrument-header">
          <div>
            <p class="eyebrow">
              {{ venueLabels[market.stock.value.venue_code] }} ·
              {{ listingStatusLabels[market.stock.value.listing_status] }}
            </p>
            <h1 class="page-heading">
              <span class="numeric">{{
                market.stock.value.security_code
              }}</span>
              · {{ market.stock.value.name }}
            </h1>
          </div>
          <span
            class="market-status"
            :class="statusCopy[market.stock.value.market_data_status].tone"
          >
            <i
              class="pi"
              :class="statusCopy[market.stock.value.market_data_status].icon"
              aria-hidden="true"
            />
            {{ statusCopy[market.stock.value.market_data_status].label }}
          </span>
        </header>

        <AppSurface
          v-if="market.stock.value.latest_quote"
          class="quote-summary"
          as="section"
        >
          <div>
            <span>最新收盘</span>
            <strong class="numeric">{{
              market.stock.value.latest_quote.close
            }}</strong>
          </div>
          <div>
            <span>涨跌</span>
            <strong class="numeric">
              {{ market.stock.value.latest_quote.change }} /
              {{ market.stock.value.latest_quote.pct_chg }}%
            </strong>
          </div>
          <div>
            <span>当日区间</span>
            <strong class="numeric">
              {{ market.stock.value.latest_quote.low }} —
              {{ market.stock.value.latest_quote.high }}
            </strong>
          </div>
          <DataFreshness
            :updated-at="market.stock.value.latest_quote.updated_at"
            label="行情更新时间"
          />
        </AppSurface>

        <AppSurface v-if="market.quotes.value.length" as="section">
          <div class="section-heading">
            <div>
              <p class="eyebrow">近 120 个交易记录</p>
              <h2>后复权日线走势与成交量</h2>
            </div>
            <span class="chart-hint"
              >价格为后复权，成交量为原始值 · 拖动或滚轮缩放</span
            >
          </div>
          <DailyQuoteChart :quotes="market.quotes.value" />
        </AppSurface>
        <AsyncState
          v-else
          state="empty"
          title="暂无日线行情"
          message="该股票可能处于停牌期，或行情同步尚未完成。"
        />
      </template>
    </AsyncState>
  </div>
</template>

<style scoped>
.back-link {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  min-height: 40px;
  gap: 8px;
  color: var(--lk-text-secondary);
  text-decoration: none;
}

.instrument-header,
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--lk-text-muted);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.market-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border-radius: 7px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-size: 0.8rem;
  font-weight: 700;
}

.market-status.stale {
  color: var(--lk-warning);
}

.market-status.missing {
  color: var(--lk-text-secondary);
}

.quote-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(130px, 1fr)) minmax(200px, auto);
  align-items: center;
  gap: 16px;
}

.quote-summary > div {
  display: grid;
  gap: 6px;
}

.quote-summary span,
.chart-hint {
  color: var(--lk-text-muted);
  font-size: 0.78rem;
}

.quote-summary strong {
  font-size: 1.15rem;
}

.numeric {
  font-variant-numeric: tabular-nums;
}

h2 {
  margin: 0;
  font-size: 1rem;
}

@media (max-width: 860px) {
  .quote-summary {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 620px) {
  .instrument-header,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .quote-summary {
    grid-template-columns: 1fr;
  }
}
</style>
