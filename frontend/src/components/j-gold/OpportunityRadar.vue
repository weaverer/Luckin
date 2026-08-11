<script setup lang="ts">
import type { RadarFilter, RadarItem } from "@/composables/useJGoldResearch";

withDefaults(
  defineProps<{
    items: RadarItem[];
    total: number;
    hasMore: boolean;
    offset: number;
    sortBy: string;
    sortDirection: string;
    activeFilter?: RadarFilter | "";
  }>(),
  { activeFilter: "" },
);
const emit = defineEmits<{
  sort: [field: string];
  previous: [];
  next: [];
  select: [item: RadarItem];
  clearFilter: [];
}>();
const filterLabels: Record<RadarFilter, string> = {
  monthly: "本月金股",
  new: "新晋金股",
  consensus: "高共识金股",
  warming: "推荐升温",
  breakout: "金股突破",
  excess: "20 日超额有效样本",
};
const columns = [
  ["股票", ""],
  ["行业", ""],
  ["推荐", "broker_count"],
  ["环比", "month_delta"],
  ["连续", "consecutive_months"],
  ["20日超额", "excess_20d"],
  ["状态", ""],
  ["评分", "score"],
] as const;
</script>

<template>
  <section class="radar-panel">
    <header>
      <div>
        <h2>机会雷达 · 综合排名</h2>
        <p>评分用于安排研究优先级，不代表收益预测。</p>
      </div>
      <div class="panel-meta">
        <span v-if="activeFilter" class="active-filter"
          >当前明细：{{ filterLabels[activeFilter] }}</span
        ><button v-if="activeFilter" @click="emit('clearFilter')">
          查看全部
</button
        ><span>共 {{ total }} 只</span>
      </div>
    </header>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th v-for="column in columns" :key="column[0]">
              <button v-if="column[1]" @click="emit('sort', column[1])">
                {{ column[0] }}
                <i
                  v-if="sortBy === column[1]"
                  class="pi"
                  :class="
                    sortDirection === 'desc' ? 'pi-sort-down' : 'pi-sort-up'
                  "
                />
</button
              ><span v-else>{{ column[0] }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.stock.stock_id"
            tabindex="0"
            @click="emit('select', item)"
            @keydown.enter="emit('select', item)"
          >
            <td data-label="股票">
              <div class="cell-stack">
                <strong>{{ item.stock.name }}</strong
                ><small>{{ item.stock.security_code }}</small>
              </div>
            </td>
            <td data-label="行业">{{ item.industry ?? "未分类" }}</td>
            <td data-label="推荐">{{ item.broker_count }} 家</td>
            <td class="numeric" data-label="环比">
              {{
                item.month_delta == null
                  ? "—"
                  : `${item.month_delta > 0 ? "+" : ""}${item.month_delta}`
              }}
            </td>
            <td data-label="连续">{{ item.consecutive_months }} 月</td>
            <td data-label="20日超额">
              {{
                item.excess_20d == null
                  ? "—"
                  : `${item.excess_20d > 0 ? "+" : ""}${item.excess_20d}%`
              }}
            </td>
            <td data-label="状态">
              <div class="cell-stack">
                <span class="status">{{ item.status }}</span
                ><small>{{ item.quality_explanation }}</small>
              </div>
            </td>
            <td data-label="评分">
              <strong class="score">{{ item.score ?? "—" }}</strong>
            </td>
          </tr>
          <tr v-if="!items.length" class="empty-row">
            <td :colspan="columns.length">当前分类没有符合条件的金股。</td>
          </tr>
        </tbody>
      </table>
    </div>
    <nav aria-label="机会雷达分页">
      <button :disabled="offset === 0" @click="emit('previous')">上一页</button
      ><button :disabled="!hasMore" @click="emit('next')">下一页</button>
    </nav>
  </section>
</template>

<style scoped>
.radar-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  height: var(--j-gold-primary-panel-height, auto);
  min-width: 0;
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface);
  overflow: hidden;
}
header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
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
p,
header span,
small {
  color: var(--lk-text-secondary);
  font-size: 0.76rem;
}
header p {
  margin-top: 5px;
}
.panel-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.panel-meta button {
  min-height: 32px;
  padding: 0 9px;
  border: 1px solid var(--lk-border);
  border-radius: 7px;
  color: var(--lk-primary);
  background: var(--lk-surface-soft);
  cursor: pointer;
}
.active-filter {
  color: var(--lk-primary);
  font-weight: 700;
}
.table-scroll {
  overflow-x: auto;
  overflow-y: auto;
  min-height: 0;
}
table {
  width: 100%;
  min-width: 850px;
  border-collapse: collapse;
}
th,
td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--lk-border);
  text-align: left;
  font-size: 0.82rem;
}
th {
  color: var(--lk-text-muted);
  background: var(--lk-surface-soft);
  font-size: 0.72rem;
}
th button {
  padding: 0;
  border: 0;
  color: inherit;
  background: none;
  font: inherit;
  cursor: pointer;
}
tbody tr {
  cursor: pointer;
}
tbody tr:hover,
tbody tr:focus {
  background: var(--lk-surface-hover);
}
tbody tr:focus-visible {
  outline: 3px solid var(--lk-focus);
  outline-offset: -3px;
}
.cell-stack {
  display: grid;
  gap: 4px;
}
.empty-row td {
  height: 160px;
  color: var(--lk-text-secondary);
  text-align: center;
}
.status {
  width: fit-content;
  padding: 4px 7px;
  border-radius: 6px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-weight: 700;
}
.score {
  color: var(--lk-fortune);
  font-variant-numeric: tabular-nums;
}
nav {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
}
nav button {
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--lk-border);
  border-radius: 8px;
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}
@media (max-width: 620px) {
  header {
    display: grid;
  }
  .panel-meta {
    justify-content: flex-start;
  }
  .table-scroll {
    max-height: 900px;
    overflow-x: visible;
    overflow-y: auto;
  }
  table {
    min-width: 0;
  }
  thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    clip-path: inset(50%);
  }
  tbody {
    display: grid;
    gap: 10px;
    padding: 10px;
  }
  tbody tr {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 14px;
    padding: 14px;
    border: 1px solid var(--lk-border);
    border-radius: 12px;
    background: var(--lk-surface-soft);
  }
  td {
    display: grid;
    gap: 4px;
    padding: 0;
    border: 0;
  }
  td::before {
    content: attr(data-label);
    color: var(--lk-text-muted);
    font-size: 0.68rem;
  }
  td:first-child,
  td:nth-child(7) {
    grid-column: 1 / -1;
  }
  .empty-row {
    display: table-row;
    padding: 0;
    border: 0;
    background: transparent;
  }
  .empty-row td {
    display: table-cell;
  }
}
</style>
