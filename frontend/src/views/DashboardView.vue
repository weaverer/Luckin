<script setup lang="ts">
import { computed } from "vue";

import AppSurface from "@/components/common/AppSurface.vue";
import AsyncState from "@/components/common/AsyncState.vue";
import DataFreshness from "@/components/common/DataFreshness.vue";
import { navigationItems } from "@/app/navigation";
import {
  countTaskStatuses,
  taskStatuses,
  useTaskStatus,
  type TaskExecutionStatus,
} from "@/composables/useTaskStatus";

const tasks = useTaskStatus();
const taskState = computed(() =>
  tasks.loading.value
    ? "loading"
    : tasks.error.value
      ? "error"
      : tasks.data.value?.items.length
        ? "ready"
        : "empty",
);
const counts = computed(() => countTaskStatuses(tasks.data.value?.items ?? []));
const taskObservedAt = computed(() => {
  const data = tasks.data.value;
  return data && "observed_at" in data ? data.observed_at : null;
});
const statusLabels: Record<TaskExecutionStatus, string> = {
  SUCCEEDED: "成功",
  PARTIAL: "部分完成",
  FAILED: "失败",
  RUNNING: "运行中",
  UNKNOWN: "未知",
  NOT_RUN: "未执行",
};
</script>

<template>
  <div class="page-stack">
    <header class="heading-row">
      <div>
        <p class="muted">统一入口</p>
        <h1 class="page-heading">工作台</h1>
      </div>
      <DataFreshness />
    </header>
    <section class="entry-grid" aria-label="功能入口">
      <RouterLink
        v-for="item in navigationItems"
        :key="item.routeName"
        :to="{ name: item.routeName }"
      >
        <AppSurface class="entry-card">
          <i class="pi" :class="item.icon" aria-hidden="true" />
          <div>
            <h2>{{ item.label }}</h2>
            <p>{{ item.description }}</p>
          </div>
          <span :class="{ delivered: item.delivered }">{{
            item.delivered ? "已交付" : "尚未交付"
          }}</span>
        </AppSurface>
      </RouterLink>
    </section>
    <AppSurface class="task-summary">
      <header>
        <div>
          <h2>当日任务摘要</h2>
          <DataFreshness :updated-at="taskObservedAt" label="观察时间" />
        </div>
        <RouterLink :to="{ name: 'tasks' }">查看任务详情</RouterLink>
      </header>
      <AsyncState
        :state="taskState"
        title="今日暂无计划任务"
        :message="tasks.error.value"
        refreshable
        @refresh="tasks.refresh"
      >
        <div class="summary-counts" aria-label="当日任务状态汇总">
          <div
            v-for="status in taskStatuses"
            :key="status"
            :data-status="status"
          >
            <span>{{ statusLabels[status] }}</span>
            <strong class="numeric">{{ counts[status] }}</strong>
          </div>
        </div>
      </AsyncState>
    </AppSurface>
  </div>
</template>

<style scoped>
.heading-row {
  display: flex;
  align-items: end;
  justify-content: space-between;
}

.heading-row p,
h1,
h2,
.entry-card p {
  margin: 0;
}

.entry-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 14px;
}

.entry-grid > a {
  color: inherit;
  text-decoration: none;
}

.entry-card {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 14px;
  height: 100%;
}

.entry-card > .pi {
  color: var(--lk-primary);
  font-size: 1.35rem;
}

.entry-card p {
  margin-top: 6px;
  color: var(--lk-text-secondary);
}

.entry-card span {
  grid-column: 2;
  color: var(--lk-warning);
  font-size: 0.85rem;
}

.entry-card span.delivered {
  color: var(--lk-primary);
}
.task-summary {
  box-shadow: none;
}
.task-summary > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.task-summary > header h2 {
  margin-bottom: 6px;
}
.task-summary > header > a {
  min-height: 40px;
  padding: 10px 12px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-primary);
  text-decoration: none;
}
.summary-counts {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
}
.summary-counts > div {
  display: grid;
  gap: 5px;
  padding: 10px;
  border-radius: 10px;
  background: var(--lk-surface-soft);
}
.summary-counts span {
  color: var(--lk-text-muted);
  font-size: 0.72rem;
}
.summary-counts strong {
  font-size: 1.2rem;
}
.numeric {
  font-variant-numeric: tabular-nums;
}
@media (max-width: 860px) {
  .summary-counts {
    grid-template-columns: repeat(3, 1fr);
  }
}
@media (max-width: 620px) {
  .task-summary > header {
    flex-direction: column;
  }
  .summary-counts {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
