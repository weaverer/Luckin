<script setup lang="ts">
import { computed } from "vue";
import DatePicker from "primevue/datepicker";

import AppSurface from "@/components/common/AppSurface.vue";
import AsyncState from "@/components/common/AsyncState.vue";
import DataFreshness from "@/components/common/DataFreshness.vue";
import {
  countTaskStatuses,
  taskStatuses,
  useTaskStatus,
  type TaskExecutionStatus,
  type TaskSummary,
} from "@/composables/useTaskStatus";
import { formatIsoDate, parseIsoDate } from "@/utils/date";

const tasks = useTaskStatus();
const businessDate = computed({
  get: () => parseIsoDate(tasks.businessDate.value),
  set: (value: Date) => {
    tasks.businessDate.value = formatIsoDate(value);
  },
});
const labels: Record<TaskExecutionStatus, { label: string; icon: string }> = {
  SUCCEEDED: { label: "成功", icon: "pi-check-circle" },
  PARTIAL: { label: "部分完成", icon: "pi-exclamation-circle" },
  FAILED: { label: "失败", icon: "pi-times-circle" },
  RUNNING: { label: "运行中", icon: "pi-spin pi-spinner" },
  UNKNOWN: { label: "未知", icon: "pi-question-circle" },
  NOT_RUN: { label: "未执行", icon: "pi-minus-circle" },
};
const summaryStatusLabels = {
  BUILDING: "生成中",
  READY: "已生成",
  FAILED: "生成失败",
} as const;
const notificationStatusLabels = {
  PENDING: "等待发送",
  SENDING: "发送中",
  SENT: "发送成功",
  FAILED: "通知失败",
} as const;
const state = computed(() =>
  tasks.loading.value
    ? "loading"
    : tasks.error.value
      ? "error"
      : tasks.data.value
        ? "ready"
        : "empty",
);
const summary = computed(() =>
  tasks.mode.value === "snapshot"
    ? (tasks.data.value as TaskSummary | undefined)
    : undefined,
);
const observedAt = computed(() => {
  const data = tasks.data.value;
  if (!data) return null;
  return "observed_at" in data ? data.observed_at : data.generated_at;
});
const counts = computed(() => {
  const data = tasks.data.value;
  if (!data) return countTaskStatuses([]);
  return "counts" in data ? data.counts : countTaskStatuses(data.items);
});
const latestAttempt = computed(
  () => summary.value?.latest_notification_attempt,
);

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
</script>

<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <p class="eyebrow">计划任务运行审计</p>
        <h1 class="page-heading">任务执行情况</h1>
      </div>
      <DataFreshness
        :updated-at="observedAt"
        :label="tasks.mode.value === 'live' ? '观察时间' : '快照生成时间'"
      />
    </header>

    <section class="toolbar">
      <div class="mode-switch" aria-label="任务数据视图">
        <button
          type="button"
          :aria-pressed="tasks.mode.value === 'live'"
          @click="tasks.mode.value = 'live'"
        >
          实时状态
        </button>
        <button
          type="button"
          :aria-pressed="tasks.mode.value === 'snapshot'"
          @click="tasks.mode.value = 'snapshot'"
        >
          20:00 快照
        </button>
      </div>
      <label for="task-business-date">
        业务日期
        <DatePicker
          v-model="businessDate"
          input-id="task-business-date"
          date-format="yy-mm-dd"
          show-icon
        />
      </label>
      <button type="button" @click="tasks.refresh">
        <i
          class="pi"
          :class="tasks.fetching.value ? 'pi-spin pi-spinner' : 'pi-refresh'"
          aria-hidden="true"
        />
        刷新
      </button>
    </section>

    <AppSurface v-if="tasks.data.value" as="section" class="status-overview">
      <header>
        <div>
          <h2>整体汇总</h2>
          <p>
            {{
              tasks.mode.value === "live" ? "当前归一状态" : "20:00 不可变快照"
            }}
          </p>
        </div>
        <span class="total-count numeric">
          共
          {{ Object.values(counts).reduce((total, value) => total + value, 0) }}
          项
        </span>
      </header>
      <div class="status-counts">
        <div
          v-for="status in taskStatuses"
          :key="status"
          class="status-count"
          :data-status="status"
        >
          <i class="pi" :class="labels[status].icon" aria-hidden="true" />
          <span>{{ labels[status].label }}</span>
          <strong class="numeric">{{ counts[status] }}</strong>
        </div>
      </div>
    </AppSurface>

    <AppSurface v-if="summary" as="section" class="notification-audit">
      <div>
        <span>快照状态</span>
        <strong>{{ summaryStatusLabels[summary.status] }}</strong>
      </div>
      <div>
        <span>通知状态</span>
        <strong :class="{ failed: summary.notification_status === 'FAILED' }">
          {{ notificationStatusLabels[summary.notification_status] }}
        </strong>
      </div>
      <div>
        <span>最近成功通知</span>
        <strong>{{ formatTime(summary.notified_at) }}</strong>
      </div>
      <div v-if="latestAttempt" class="attempt-detail">
        <span>
          第 {{ latestAttempt.attempt_no }} 次尝试 ·
          {{
            latestAttempt.trigger_kind === "MANUAL_RETRY"
              ? "人工补发"
              : "自动发送"
          }}
        </span>
        <strong :class="{ failed: latestAttempt.status === 'FAILED' }">
          {{
            latestAttempt.status === "RUNNING"
              ? "进行中"
              : latestAttempt.status === "SUCCEEDED"
                ? "已成功"
                : latestAttempt.retryable
                  ? "失败，可重试或补发"
                  : "失败，需检查配置"
          }}
        </strong>
        <small v-if="latestAttempt.error_summary">{{
          latestAttempt.error_summary
        }}</small>
        <time
          :datetime="latestAttempt.completed_at ?? latestAttempt.started_at"
        >
          {{
            formatTime(latestAttempt.completed_at ?? latestAttempt.started_at)
          }}
        </time>
      </div>
    </AppSurface>

    <AsyncState
      :state="state"
      :title="state === 'empty' ? '当前没有计划任务' : ''"
      :message="tasks.error.value"
      refreshable
      @refresh="tasks.refresh"
    >
      <section class="task-list" aria-live="polite">
        <article
          v-for="item in tasks.data.value?.items ?? []"
          :key="item.task_key"
          class="task-row"
        >
          <div class="task-copy">
            <strong>{{ item.display_name }}</strong>
            <span class="muted">{{ item.schedule_slug }}</span>
            <p v-if="item.error_summary">
              <i class="pi pi-info-circle" aria-hidden="true" />
              {{ item.error_summary }}
            </p>
            <div class="task-times">
              <span>开始 {{ formatTime(item.started_at) }}</span>
              <span>完成 {{ formatTime(item.completed_at) }}</span>
            </div>
          </div>
          <div class="task-meta">
            <span v-if="item.record_count !== null" class="numeric"
              >{{ item.record_count }} 条</span
            >
            <span class="status-tag" :data-status="item.status">
              <i
                class="pi"
                :class="labels[item.status].icon"
                aria-hidden="true"
              />
              {{ labels[item.status].label }}
            </span>
          </div>
        </article>
      </section>
    </AsyncState>
  </div>
</template>

<style scoped>
.page-header,
.toolbar,
.mode-switch,
.status-overview > header,
.notification-audit,
.task-row,
.task-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--lk-text-muted);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.toolbar {
  justify-content: flex-start;
  padding: 12px 16px;
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface-soft);
}

.mode-switch {
  padding: 4px;
  border-radius: 12px;
  background: var(--lk-surface);
}

button,
input {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-text);
  background: var(--lk-surface);
}

button {
  cursor: pointer;
}

.mode-switch button {
  border-color: transparent;
}

.mode-switch button[aria-pressed="true"] {
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}

label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--lk-text-secondary);
  font-size: 0.82rem;
}

.status-overview {
  box-shadow: none;
}
.status-overview > header {
  margin-bottom: 16px;
}
.status-overview h2,
.status-overview p {
  margin: 0;
}
.status-overview p,
.total-count {
  margin-top: 4px;
  color: var(--lk-text-muted);
  font-size: 0.78rem;
}
.status-counts {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}
.status-count {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 6px;
  padding: 10px;
  border-radius: 10px;
  background: var(--lk-surface-soft);
}
.status-count > i,
.status-count > span {
  color: var(--lk-text-muted);
  font-size: 0.72rem;
}
.status-count strong {
  grid-column: 1 / -1;
  font-size: 1.25rem;
}
.notification-audit {
  display: grid;
  grid-template-columns: repeat(3, minmax(130px, 0.7fr)) minmax(240px, 1.4fr);
  box-shadow: none;
}
.notification-audit > div {
  display: grid;
  align-content: start;
  gap: 5px;
}
.notification-audit span,
.notification-audit small,
.notification-audit time {
  color: var(--lk-text-muted);
  font-size: 0.75rem;
}
.attempt-detail {
  padding-inline-start: 16px;
  border-inline-start: 1px solid var(--lk-border);
}

.failed {
  color: var(--lk-danger);
}

.task-list {
  overflow: hidden;
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface);
}

.task-row {
  min-height: 68px;
  padding: 12px 16px;
  border-top: 1px solid var(--lk-border);
}

.task-row:first-child {
  border-top: 0;
}

.task-copy {
  display: grid;
  gap: 3px;
}

.task-copy p {
  margin: 5px 0 0;
  color: var(--lk-danger);
  font-size: 0.8rem;
}
.task-times {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
  color: var(--lk-text-muted);
  font-size: 0.72rem;
}

.task-meta {
  justify-content: flex-end;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  min-width: 96px;
  gap: 7px;
  padding: 6px 8px;
  border-radius: 7px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-size: 0.78rem;
  font-weight: 700;
}

[data-status="FAILED"],
[data-status="NOT_RUN"] {
  color: var(--lk-danger);
}

[data-status="PARTIAL"] {
  color: var(--lk-warning);
}

[data-status="UNKNOWN"] {
  color: var(--lk-text-secondary);
}

.numeric {
  font-variant-numeric: tabular-nums;
}

@media (max-width: 860px) {
  .status-counts {
    grid-template-columns: repeat(3, 1fr);
  }
  .notification-audit {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 620px) {
  .page-header,
  .toolbar,
  .task-row {
    align-items: stretch;
    flex-direction: column;
  }

  .status-counts,
  .notification-audit {
    grid-template-columns: 1fr;
  }
  .attempt-detail {
    padding-inline-start: 0;
    padding-top: 10px;
    border-inline-start: 0;
    border-top: 1px solid var(--lk-border);
  }
}
</style>
