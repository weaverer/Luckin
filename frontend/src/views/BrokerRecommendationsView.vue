<script setup lang="ts">
import { computed, ref } from "vue";

import BrokerAbility from "@/components/j-gold/BrokerAbility.vue";
import GoldSignals from "@/components/j-gold/GoldSignals.vue";
import IndustryConsensus from "@/components/j-gold/IndustryConsensus.vue";
import MarketDiffusion from "@/components/j-gold/MarketDiffusion.vue";
import OpportunityRadar from "@/components/j-gold/OpportunityRadar.vue";
import OverviewMetrics from "@/components/j-gold/OverviewMetrics.vue";
import StockResearchDrawer from "@/components/j-gold/StockResearchDrawer.vue";
import {
  type RadarItem,
  type RadarFilter,
  useJGoldResearch,
} from "@/composables/useJGoldResearch";

const research = useJGoldResearch();
const selected = ref<RadarItem | null>(null);
const monthOptions = computed(
  () => research.data.value?.available_months ?? [],
);
const pageState = computed(() =>
  research.loading.value
    ? "loading"
    : research.error.value
      ? "error"
      : research.data.value
        ? "ready"
        : "empty",
);

function selectIndustry(name: string): void {
  research.draftIndustry.value = name;
  research.applyFilters();
}

function drillToRadar(filter: RadarFilter): void {
  research.drillRadar(filter);
  document.getElementById("j-gold-radar")?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
    block: "start",
  });
}
</script>

<template>
  <div class="j-gold-page">
    <header class="command-header">
      <div>
        <h1>J金股</h1>
        <p>券商金股研究驾驶舱 · 月度推荐与市场表现的可追溯研究</p>
      </div>
      <div v-if="research.data.value" class="data-stamp">
        <span :class="research.data.value.quality.status">{{
          research.data.value.quality.status === "ready"
            ? "数据完整"
            : "部分覆盖"
        }}</span
        ><time :datetime="research.data.value.quality.generated_at"
          >更新于
          {{
            new Date(research.data.value.quality.generated_at).toLocaleString(
              "zh-CN",
            )
          }}</time
        >
      </div>
    </header>

    <form class="filter-rail" @submit.prevent="research.applyFilters">
      <label
        >数据月份<select v-model="research.draftMonth.value">
          <option
            v-for="month in monthOptions"
            :key="month"
            :value="month.slice(0, 7)"
          >
            {{ month.slice(0, 7) }}
          </option>
          <option
            v-if="!monthOptions.length"
            :value="research.draftMonth.value"
          >
            {{ research.draftMonth.value }}
          </option>
        </select></label
      >
      <label
        >券商<input
          v-model="research.draftBroker.value"
          maxlength="160"
          placeholder="全部券商"
      /></label>
      <label
        >行业<input
          v-model="research.draftIndustry.value"
          maxlength="160"
          placeholder="全部行业"
      /></label>
      <button
        class="primary"
        type="submit"
        :disabled="research.refreshing.value"
      >
        应用筛选
      </button>
      <button
        type="button"
        :disabled="research.refreshing.value"
        @click="research.clearFilters"
      >
        清除
      </button>
      <button
        type="button"
        :disabled="research.refreshing.value"
        @click="() => research.refresh()"
      >
        <i class="pi pi-refresh" aria-hidden="true" /> 刷新
      </button>
    </form>

    <div v-if="pageState === 'loading'" class="page-state" role="status">
      正在建立研究视图…
    </div>
    <div
      v-else-if="pageState === 'error'"
      class="page-state error"
      role="alert"
    >
      <strong>驾驶舱加载失败</strong><span>{{ research.error.value }}</span
      ><button @click="() => research.refresh()">重试</button>
    </div>
    <template v-else-if="research.data.value">
      <div class="quality-line" :class="research.data.value.quality.status">
        <i class="pi pi-info-circle" aria-hidden="true" /><span>{{
          research.data.value.quality.explanation
        }}</span
        ><small
          >来源：{{ research.data.value.quality.source }} ·
          数据粒度：月度推荐与日线行情</small
        >
      </div>
      <OverviewMetrics
        :metrics="research.data.value.metrics"
        @drill="drillToRadar"
      />
      <div class="primary-grid">
        <OpportunityRadar
          id="j-gold-radar"
          :items="research.data.value.items"
          :total="research.data.value.pagination.total"
          :has-more="research.data.value.pagination.has_more"
          :offset="research.data.value.pagination.offset"
          :sort-by="research.filters.sortBy"
          :sort-direction="research.filters.sortDirection"
          :active-filter="research.filters.radarFilter"
          @sort="research.sort"
          @clear-filter="research.clearRadarFilter"
          @previous="research.previous"
          @next="research.next"
          @select="selected = $event"
        />
        <GoldSignals :signals="research.data.value.signals" />
      </div>
      <div class="secondary-grid">
        <IndustryConsensus
          :industries="research.data.value.industries"
          @select="selectIndustry"
        /><BrokerAbility
          :items="research.data.value.broker_ability"
        /><MarketDiffusion :points="research.data.value.diffusion" />
      </div>
    </template>
    <StockResearchDrawer
      :item="selected"
      :month="
        research.data.value?.selected_month.slice(0, 7) ??
        research.draftMonth.value
      "
      @close="selected = null"
    />
  </div>
</template>

<style scoped>
.j-gold-page {
  display: grid;
  gap: 16px;
  padding-bottom: 32px;
}
.command-header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 20px;
  padding: 14px 2px;
}
.command-header h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 3.3rem);
  letter-spacing: -0.04em;
}
.command-header p {
  margin: 7px 0 0;
  color: var(--lk-text-secondary);
}
.data-stamp {
  display: grid;
  justify-items: end;
  gap: 6px;
  color: var(--lk-text-secondary);
  font-size: 0.76rem;
}
.data-stamp span {
  padding: 5px 8px;
  border-radius: 6px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-weight: 700;
}
.filter-rail {
  display: flex;
  align-items: end;
  gap: 10px;
  padding: 14px 16px;
  border-block: 1px solid var(--lk-border);
  background: var(--lk-surface-soft);
}
label {
  display: grid;
  gap: 6px;
  min-width: 160px;
  color: var(--lk-text-secondary);
  font-size: 0.76rem;
}
input,
select,
.filter-rail button {
  min-height: 44px;
  padding: 0 11px;
  border: 1px solid var(--lk-border);
  border-radius: 9px;
  color: var(--lk-text);
  background: var(--lk-surface);
}
.filter-rail button {
  cursor: pointer;
}
.filter-rail .primary {
  margin-left: auto;
  border-color: var(--lk-primary);
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.quality-line {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 14px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-text-secondary);
  background: var(--lk-surface);
}
.quality-line small {
  margin-left: auto;
}
.quality-line.partial i,
.quality-line.delayed i {
  color: var(--lk-warning);
}
.primary-grid {
  --j-gold-primary-panel-height: 760px;

  display: grid;
  align-items: stretch;
  grid-template-columns: minmax(0, 1.75fr) minmax(300px, 0.75fr);
  gap: 12px;
}
.secondary-grid {
  display: grid;
  grid-template-columns: 1.15fr 1fr 1fr;
  gap: 12px;
}
.page-state {
  display: grid;
  place-items: center;
  min-height: 300px;
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  color: var(--lk-text-secondary);
  background: var(--lk-surface);
}
.page-state.error {
  gap: 10px;
}
.page-state button {
  padding: 8px 14px;
  border: 1px solid var(--lk-border);
  border-radius: 8px;
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}
@media (max-width: 1100px) {
  .primary-grid,
  .secondary-grid {
    grid-template-columns: 1fr;
  }
  .filter-rail {
    flex-wrap: wrap;
  }
  .filter-rail .primary {
    margin-left: 0;
  }
}
@media (max-width: 860px) {
  .command-header {
    align-items: start;
  }
  .quality-line {
    align-items: start;
    flex-wrap: wrap;
  }
  .quality-line small {
    width: 100%;
    margin-left: 0;
  }
  .filter-rail label {
    flex: 1;
  }
}
@media (max-width: 620px) {
  .command-header {
    display: grid;
  }
  .data-stamp {
    justify-items: start;
  }
  .filter-rail {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .filter-rail label {
    grid-column: 1/-1;
  }
  .quality-line {
    font-size: 0.8rem;
  }
}
@media (prefers-reduced-motion: reduce) {
  * {
    scroll-behavior: auto !important;
  }
}
</style>
