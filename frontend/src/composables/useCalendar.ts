import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, reactive } from "vue";

import { apiRequest } from "@/api/client/http";
import { calendarKeys } from "@/api/query-keys/calendar";

export interface ImportantDate {
  important_date_id: string;
  event_date: string;
  title: string;
  notes: string | null;
}
export interface CalendarDay {
  date: string;
  market_status: "OPEN" | "CLOSED" | "UNKNOWN";
  important_dates: ImportantDate[];
  calendar_updated_at: string | null;
}
interface ImportantDateInput {
  event_date: string;
  title: string;
  notes: string | null;
}

export function useCalendar() {
  const client = useQueryClient();
  const range = reactive({ start: "", end: "" });
  const fetchRange = (start: string, end: string) =>
    apiRequest<CalendarDay[]>({
      url: "/calendar",
      params: { start_date: start, end_date: end },
    });
  const query = useQuery({
    queryKey: computed(() => calendarKeys.range(range.start, range.end)),
    queryFn: () => fetchRange(range.start, range.end),
    enabled: false,
  });
  const invalidate = () =>
    client.invalidateQueries({ queryKey: calendarKeys.all });
  const createMutation = useMutation({
    mutationFn: (input: ImportantDateInput) =>
      apiRequest<ImportantDate>({
        method: "POST",
        url: "/important-dates",
        data: input,
      }),
    onSuccess: invalidate,
  });
  const updateMutation = useMutation({
    mutationFn: (input: ImportantDateInput & { id: string }) =>
      apiRequest<ImportantDate>({
        method: "PUT",
        url: `/important-dates/${input.id}`,
        data: {
          event_date: input.event_date,
          title: input.title,
          notes: input.notes,
        },
      }),
    onSuccess: invalidate,
  });
  const removeMutation = useMutation({
    mutationFn: (id: string) =>
      apiRequest<void>({ method: "DELETE", url: `/important-dates/${id}` }),
    onSuccess: invalidate,
  });
  const days = computed(() => query.data.value ?? []);
  const updatedAt = computed(
    () =>
      days.value
        .map((item) => item.calendar_updated_at)
        .filter(Boolean)
        .sort()
        .at(-1) ?? null,
  );

  async function load(start: string, end: string) {
    range.start = start;
    range.end = end;
    await client.fetchQuery({
      queryKey: calendarKeys.range(start, end),
      queryFn: () => fetchRange(start, end),
    });
  }

  return {
    days,
    loading: computed(
      () => query.isFetching.value && query.data.value === undefined,
    ),
    error: computed(() =>
      query.error.value ? "日历加载失败，请稍后重试" : "",
    ),
    updatedAt,
    load,
    create: (input: ImportantDateInput) => createMutation.mutateAsync(input),
    update: (id: string, input: ImportantDateInput) =>
      updateMutation.mutateAsync({ id, ...input }),
    remove: (id: string) => removeMutation.mutateAsync(id),
  };
}
