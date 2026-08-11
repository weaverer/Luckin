import { useQuery } from "@tanstack/vue-query";
import { computed, ref } from "vue";

import { apiRequest } from "@/api/client/http";
import { taskStatusKeys } from "@/api/query-keys/task-status";
import { formatShanghaiDate } from "@/utils/date";

export type TaskExecutionStatus =
  "SUCCEEDED" | "PARTIAL" | "FAILED" | "RUNNING" | "UNKNOWN" | "NOT_RUN";

export type TaskStatusCounts = Record<TaskExecutionStatus, number>;

export interface NotificationAttempt {
  attempt_no: number;
  trigger_kind: "AUTOMATIC" | "MANUAL_RETRY";
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  error_category: string | null;
  error_summary: string | null;
  started_at: string;
  completed_at: string | null;
  retryable: boolean;
}

export interface TaskStatusItem {
  task_key: string;
  schedule_slug: string;
  display_name: string;
  status: TaskExecutionStatus;
  source_run_id: string | null;
  started_at: string | null;
  completed_at: string | null;
  record_count: number | null;
  error_category: string | null;
  error_summary: string | null;
  observed_at: string;
}

export interface LiveTaskStatus {
  business_date: string;
  observed_at: string;
  items: TaskStatusItem[];
}

export interface TaskSummary {
  summary_id: string;
  business_date: string;
  status: "BUILDING" | "READY" | "FAILED";
  notification_status: "PENDING" | "SENDING" | "SENT" | "FAILED";
  generated_at: string | null;
  notified_at: string | null;
  counts: TaskStatusCounts;
  items: TaskStatusItem[];
  latest_notification_attempt: NotificationAttempt | null;
}

export const taskStatuses: readonly TaskExecutionStatus[] = [
  "SUCCEEDED",
  "PARTIAL",
  "FAILED",
  "RUNNING",
  "UNKNOWN",
  "NOT_RUN",
];

export function countTaskStatuses(items: TaskStatusItem[]): TaskStatusCounts {
  const counts = Object.fromEntries(
    taskStatuses.map((status) => [status, 0]),
  ) as TaskStatusCounts;
  for (const item of items) counts[item.status] += 1;
  return counts;
}

export function useTaskStatus() {
  const mode = ref<"live" | "snapshot">("live");
  const businessDate = ref(formatShanghaiDate());
  const live = useQuery({
    queryKey: computed(() => taskStatusKeys.live(businessDate.value)),
    queryFn: () =>
      apiRequest<LiveTaskStatus>({
        url: "/task-status",
        params: { business_date: businessDate.value },
      }),
    enabled: computed(() => mode.value === "live"),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.status === "RUNNING")
        ? 15_000
        : false,
  });
  const snapshot = useQuery({
    queryKey: computed(() => taskStatusKeys.summary(businessDate.value)),
    queryFn: () =>
      apiRequest<TaskSummary>({
        url: `/task-summaries/${businessDate.value}`,
      }),
    enabled: computed(() => mode.value === "snapshot"),
    retry: false,
  });
  const selected = computed(() =>
    mode.value === "live" ? live.data.value : snapshot.data.value,
  );
  const active = computed(() => (mode.value === "live" ? live : snapshot));

  return {
    mode,
    businessDate,
    data: selected,
    loading: computed(() => active.value.isPending.value),
    fetching: computed(() => active.value.isFetching.value),
    error: computed(() =>
      active.value.error.value
        ? mode.value === "snapshot"
          ? "该日期尚无 20:00 汇总快照"
          : "任务状态加载失败，请稍后重试"
        : "",
    ),
    refresh: () => active.value.refetch(),
  };
}
