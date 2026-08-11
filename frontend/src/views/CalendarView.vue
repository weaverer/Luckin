<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import DatePicker from "primevue/datepicker";

import { WorkbenchApiError } from "@/api/client/errors";
import ImportantDateDialog from "@/components/calendar/ImportantDateDialog.vue";
import AppSurface from "@/components/common/AppSurface.vue";
import AsyncState from "@/components/common/AsyncState.vue";
import DataFreshness from "@/components/common/DataFreshness.vue";
import {
  type CalendarDay,
  type ImportantDate,
  useCalendar,
} from "@/composables/useCalendar";
import {
  formatIsoDate,
  formatIsoMonth,
  formatShanghaiDate,
  formatShanghaiMonth,
  parseIsoMonth,
} from "@/utils/date";

interface CalendarCell {
  date: string;
  dayNumber: number;
  currentMonth: boolean;
  day: CalendarDay | null;
}

const calendar = useCalendar();
const month = ref(parseIsoMonth(formatShanghaiMonth()));
const dialog = ref(false);
const dialogInitialDate = ref("");
const editing = ref<ImportantDate | null>(null);
const saving = ref(false);
const saveError = ref("");
const fieldErrors = ref<Record<string, string>>({});
const monthKey = computed(() => formatIsoMonth(month.value));
const start = computed(() => `${monthKey.value}-01`);
const end = computed(() =>
  formatIsoDate(
    new Date(month.value.getFullYear(), month.value.getMonth() + 1, 0),
  ),
);
const weekdayLabels = ["一", "二", "三", "四", "五", "六", "日"];
const labels = {
  OPEN: "交易日",
  CLOSED: "非交易日",
  UNKNOWN: "待确认",
} as const;
const today = formatShanghaiDate();
const daysByDate = computed(
  () => new Map(calendar.days.value.map((day) => [day.date, day])),
);
const calendarCells = computed<CalendarCell[]>(() => {
  const year = month.value.getFullYear();
  const monthIndex = month.value.getMonth();
  const first = new Date(year, monthIndex, 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(year, monthIndex, index - mondayOffset + 1);
    const isoDate = formatIsoDate(date);
    return {
      date: isoDate,
      dayNumber: date.getDate(),
      currentMonth: date.getMonth() === monthIndex,
      day: daysByDate.value.get(isoDate) ?? null,
    };
  });
});
const importantDates = computed(() =>
  calendar.days.value
    .flatMap((day) => day.important_dates)
    .sort((left, right) => left.event_date.localeCompare(right.event_date)),
);
const state = computed(() =>
  calendar.loading.value
    ? "loading"
    : calendar.error.value
      ? "error"
      : calendar.days.value.length
        ? "ready"
        : "empty",
);

async function refresh() {
  await calendar.load(start.value, end.value);
}
async function save(value: {
  event_date: string;
  title: string;
  notes: string | null;
}) {
  saving.value = true;
  saveError.value = "";
  fieldErrors.value = {};
  try {
    if (editing.value)
      await calendar.update(editing.value.important_date_id, value);
    else await calendar.create(value);
    dialog.value = false;
    editing.value = null;
    await refresh();
  } catch (error) {
    if (error instanceof WorkbenchApiError) {
      fieldErrors.value = Object.fromEntries(
        error.details
          .filter((item) => item.field)
          .map((item) => [item.field!, item.message]),
      );
      saveError.value =
        error.code === 400001 ? "同一天已存在相同标题的重要日" : error.message;
    } else saveError.value = "重要日保存失败，请稍后重试";
  } finally {
    saving.value = false;
  }
}
function openCreate(date = start.value) {
  editing.value = null;
  dialogInitialDate.value = date;
  saveError.value = "";
  fieldErrors.value = {};
  dialog.value = true;
}
function openEdit(item: ImportantDate) {
  editing.value = item;
  dialogInitialDate.value = item.event_date;
  saveError.value = "";
  fieldErrors.value = {};
  dialog.value = true;
}
function changeMonth(offset: number) {
  month.value = new Date(
    month.value.getFullYear(),
    month.value.getMonth() + offset,
    1,
  );
}
function goToday() {
  month.value = new Date();
}
function formatOverviewDate(value: string) {
  const date = new Date(`${value}T00:00:00`);
  return `${date.getMonth() + 1}月${date.getDate()}日 · 周${weekdayLabels[(date.getDay() + 6) % 7]}`;
}
async function remove(id: string) {
  if (confirm("确认删除这个重要日？")) {
    await calendar.remove(id);
    await refresh();
  }
}
watch(month, refresh);
onMounted(refresh);
</script>

<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <p class="eyebrow">CN-S 市场</p>
        <h1 class="page-heading">交易日历</h1>
      </div>
      <button class="primary" aria-label="添加重要日" @click="openCreate()">
        <i class="pi pi-plus" aria-hidden="true" /> 添加重要日
      </button>
    </header>
    <DataFreshness :updated-at="calendar.updatedAt.value" />
    <AsyncState
      :state="state"
      :message="calendar.error.value"
      refreshable
      @refresh="refresh"
    >
      <div class="calendar-workspace">
        <AppSurface class="calendar-board">
          <header class="calendar-toolbar">
            <div class="month-navigation">
              <button
                type="button"
                aria-label="上个月"
                @click="changeMonth(-1)"
              >
                <i class="pi pi-chevron-left" aria-hidden="true" />
              </button>
              <DatePicker
                v-model="month"
                input-id="calendar-month"
                aria-label="选择月份"
                view="month"
                date-format="yy年mm月"
                :show-on-focus="false"
                show-icon
              />
              <button type="button" aria-label="下个月" @click="changeMonth(1)">
                <i class="pi pi-chevron-right" aria-hidden="true" />
              </button>
            </div>
            <button type="button" class="today-button" @click="goToday">
              今天
            </button>
          </header>

          <div class="calendar" aria-label="月历">
            <div
              v-for="weekday in weekdayLabels"
              :key="weekday"
              class="weekday"
              aria-hidden="true"
            >
              周{{ weekday }}
            </div>
            <article
              v-for="cell in calendarCells"
              :key="cell.date"
              class="calendar-day"
              :class="[
                cell.currentMonth ? 'current-month' : 'outside-month',
                cell.day?.market_status.toLowerCase() ?? 'unknown',
                { today: cell.date === today },
              ]"
              :data-date="cell.date"
              :tabindex="cell.currentMonth ? 0 : -1"
              :aria-label="
                cell.currentMonth
                  ? `${cell.date}，${labels[cell.day?.market_status ?? 'UNKNOWN']}，点击添加重要日`
                  : undefined
              "
              @click="cell.currentMonth && openCreate(cell.date)"
              @keydown.enter="cell.currentMonth && openCreate(cell.date)"
              @keydown.space.prevent="
                cell.currentMonth && openCreate(cell.date)
              "
            >
              <header class="day-header">
                <time :datetime="cell.date">{{ cell.dayNumber }}</time>
                <span v-if="cell.currentMonth && cell.day" class="market-label">
                  <i aria-hidden="true" />{{ labels[cell.day.market_status] }}
                </span>
              </header>
              <div v-if="cell.day?.important_dates.length" class="event-list">
                <div
                  v-for="item in cell.day.important_dates.slice(0, 3)"
                  :key="item.important_date_id"
                  class="event"
                >
                  <button
                    class="event-copy"
                    type="button"
                    aria-label="编辑重要日"
                    @click.stop="openEdit(item)"
                  >
                    <i aria-hidden="true" />
                    <b>{{ item.title }}</b>
                  </button>
                  <button
                    class="event-delete"
                    type="button"
                    aria-label="删除重要日"
                    @click.stop="remove(item.important_date_id)"
                  >
                    <i class="pi pi-times" aria-hidden="true" />
                  </button>
                </div>
                <small
                  v-if="cell.day.important_dates.length > 3"
                  class="more-events"
                >
                  另有 {{ cell.day.important_dates.length - 3 }} 项
                </small>
              </div>
            </article>
          </div>
          <footer class="calendar-legend">
            <span class="open"><i aria-hidden="true" />交易日</span>
            <span class="closed"><i aria-hidden="true" />非交易日</span>
            <span class="unknown"><i aria-hidden="true" />待确认</span>
            <small>点击日期可添加重要日</small>
          </footer>
        </AppSurface>

        <AppSurface class="important-overview">
          <header>
            <div>
              <p class="eyebrow">本月安排</p>
              <h2>重要日概览</h2>
            </div>
            <span class="event-count">{{ importantDates.length }}</span>
          </header>
          <div v-if="importantDates.length" class="overview-list">
            <button
              v-for="item in importantDates"
              :key="item.important_date_id"
              type="button"
              class="overview-event"
              @click="openEdit(item)"
            >
              <time :datetime="item.event_date">{{
                formatOverviewDate(item.event_date)
              }}</time>
              <b>{{ item.title }}</b>
              <small v-if="item.notes">{{ item.notes }}</small>
            </button>
          </div>
          <div v-else class="overview-empty">
            <i class="pi pi-calendar-plus" aria-hidden="true" />
            <b>本月暂无重要日</b>
            <small>点击左侧日期即可快速添加</small>
          </div>
        </AppSurface>
      </div>
    </AsyncState>
    <ImportantDateDialog
      v-if="dialog"
      :initial-date="dialogInitialDate"
      :important-date="editing"
      :field-errors="fieldErrors"
      :error="saveError"
      :busy="saving"
      @save="save"
      @cancel="
        dialog = false;
        editing = null;
      "
    />
  </div>
</template>

<style scoped>
.page-header,
.calendar-toolbar,
.month-navigation,
.calendar-legend,
.important-overview > header,
.event-copy {
  display: flex;
  align-items: center;
}
.page-header,
.calendar-toolbar,
.important-overview > header {
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
button {
  min-height: 40px;
  padding: 0 12px;
  border: 1px solid var(--lk-border);
  border-radius: 9px;
  color: var(--lk-text);
  background: var(--lk-surface);
  cursor: pointer;
}
button.primary {
  border-color: var(--lk-primary);
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}
.calendar-workspace {
  display: grid;
  grid-template-columns: minmax(0, 2.4fr) minmax(260px, 0.75fr);
  align-items: start;
  gap: 16px;
}
:deep(.calendar-board) {
  min-width: 0;
  padding: 0;
  overflow: hidden;
  box-shadow: none;
}
.calendar-toolbar {
  padding: 14px 16px;
  border-bottom: 1px solid var(--lk-border);
}
.month-navigation {
  gap: 8px;
}
.month-navigation > button {
  width: 40px;
  padding: 0;
}
:deep(.month-navigation .p-datepicker) {
  width: 178px;
}
:deep(.month-navigation .p-datepicker-input) {
  font-weight: 750;
  text-align: center;
}
.today-button {
  color: var(--lk-primary);
  font-weight: 700;
}
.calendar {
  display: grid;
  grid-template-columns: repeat(7, minmax(94px, 1fr));
  border-left: 1px solid var(--lk-primary);
  background: var(--lk-border);
  gap: 1px;
}
.weekday {
  padding: 9px 8px;
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
  font-size: 0.72rem;
  font-weight: 750;
  text-align: center;
}
.calendar-day {
  position: relative;
  min-width: 0;
  min-height: 128px;
  padding: 9px;
  outline: 0;
  background: var(--lk-surface);
  cursor: pointer;
}
.calendar-day:hover,
.calendar-day:focus-visible {
  z-index: 1;
  box-shadow: inset 0 0 0 2px var(--lk-primary);
}
.calendar-day.closed {
  background: color-mix(in srgb, var(--lk-surface-soft) 88%, var(--lk-border));
}
.calendar-day.closed::after {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 14px;
  height: 14px;
  background: linear-gradient(135deg, transparent 50%, var(--lk-border) 50%);
  content: "";
}
.calendar-day.outside-month {
  color: var(--lk-text-muted);
  background: var(--lk-surface-soft);
  cursor: default;
  opacity: 0.5;
}
.calendar-day.today time {
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}
.day-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}
.day-header time {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 50%;
  font-weight: 800;
}
.market-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--lk-text-muted);
  font-size: 0.66rem;
}
.market-label i,
.calendar-legend i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--lk-text-muted);
}
.open .market-label i,
.calendar-legend .open i {
  background: var(--lk-success);
}
.closed .market-label i,
.calendar-legend .closed i {
  background: var(--lk-text-muted);
}
.unknown .market-label i,
.calendar-legend .unknown i {
  background: var(--lk-warning);
}
.event-list {
  display: grid;
  gap: 4px;
  margin-top: 8px;
}
.event {
  display: flex;
  min-width: 0;
  align-items: center;
  border-radius: 6px;
  background: var(--lk-selection);
}
.event-copy {
  min-width: 0;
  min-height: 27px;
  flex: 1;
  gap: 6px;
  padding: 0 6px;
  border: 0;
  text-align: left;
  background: transparent;
}
.event-copy > i {
  width: 5px;
  min-width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--lk-fortune);
}
.event-copy b {
  overflow: hidden;
  font-size: 0.7rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-delete {
  width: 26px;
  min-height: 27px;
  padding: 0;
  border: 0;
  color: var(--lk-text-muted);
  background: transparent;
}
.more-events {
  padding-left: 8px;
  color: var(--lk-text-muted);
  font-size: 0.66rem;
}
.calendar-legend {
  flex-wrap: wrap;
  gap: 14px;
  padding: 12px 16px;
  border-top: 1px solid var(--lk-border);
  color: var(--lk-text-secondary);
  font-size: 0.72rem;
}
.calendar-legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.calendar-legend small {
  margin-left: auto;
  color: var(--lk-text-muted);
}
:deep(.important-overview) {
  position: sticky;
  top: 82px;
  padding: 20px;
  box-shadow: none;
}
.important-overview h2 {
  margin: 0;
  font-size: 1.05rem;
}
.event-count {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 50%;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-weight: 800;
}
.overview-list {
  display: grid;
  max-height: calc(100vh - 190px);
  gap: 10px;
  margin-top: 16px;
  overflow-y: auto;
}
.overview-event {
  display: grid;
  height: auto;
  gap: 5px;
  padding: 12px;
  border-left: 3px solid var(--lk-fortune);
  text-align: left;
}
.overview-event time,
.overview-event small {
  color: var(--lk-text-muted);
  font-size: 0.7rem;
}
.overview-empty {
  display: grid;
  min-height: 260px;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: var(--lk-text-muted);
  text-align: center;
}
.overview-empty i {
  margin-bottom: 8px;
  color: var(--lk-primary);
  font-size: 1.7rem;
}
@media (max-width: 1100px) {
  .calendar-workspace {
    grid-template-columns: 1fr;
  }
  :deep(.important-overview) {
    position: static;
  }
  .overview-list {
    max-height: none;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .calendar {
    grid-template-columns: repeat(7, minmax(72px, 1fr));
    overflow-x: auto;
  }
  .calendar-day {
    min-height: 108px;
  }
  .market-label {
    display: none;
  }
  .overview-list {
    grid-template-columns: 1fr;
  }
}
</style>
