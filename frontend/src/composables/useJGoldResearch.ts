import { keepPreviousData, useQuery } from "@tanstack/vue-query";
import { computed, reactive, ref } from "vue";

import { apiRequest } from "@/api/client/http";
import { jGoldKeys } from "@/api/query-keys/j-gold";
import { formatShanghaiMonth } from "@/utils/date";

export interface QualityStatus {
  status: "ready" | "empty" | "delayed" | "insufficient" | "partial" | "error";
  explanation: string;
  source: string;
  generated_at: string;
}

export interface StockIdentity {
  stock_id: string;
  security_code: string;
  name: string;
  market_code: string;
  listing_status: string;
}

export interface RadarItem {
  stock: StockIdentity;
  industry: string | null;
  broker_count: number;
  brokers: string[];
  month_delta: number | null;
  is_new: boolean | null;
  three_month_peak: boolean | null;
  breakout: boolean;
  consecutive_months: number;
  excess_20d: number | null;
  status: string;
  score: number | null;
  score_components: Record<string, number | null>;
  quality: string;
  quality_explanation: string;
}

export type RadarFilter =
  "monthly" | "new" | "consensus" | "warming" | "breakout" | "excess";

export interface ResearchData {
  metrics: {
    monthly_count: number;
    broker_count: number;
    industry_count: number;
    new_count: number | null;
    new_change: number | null;
    consensus_count: number;
    warming_count: number | null;
    warming_three_months: Array<{ month: string; count: number | null }>;
    breakout_count: number;
    average_excess_20d: number | null;
    excess_sample_count: number;
    benchmark: string;
    recommendation_month?: string;
  };
  items: RadarItem[];
  pagination: {
    limit: number;
    offset: number;
    total: number;
    has_more: boolean;
  };
  signals: Array<Record<string, unknown>>;
  industries: Array<Record<string, unknown>>;
  broker_ability: Array<Record<string, unknown>>;
  diffusion: Array<Record<string, unknown>>;
  quality: QualityStatus;
  selected_month: string;
  available_months: string[];
}

export function useJGoldResearch() {
  const draftMonth = ref(formatShanghaiMonth());
  const draftBroker = ref("");
  const draftIndustry = ref("");
  const filters = reactive<{
    month: string;
    broker: string;
    industry: string;
    limit: number;
    offset: number;
    sortBy: string;
    sortDirection: string;
    radarFilter: RadarFilter | "";
  }>({
    month: draftMonth.value,
    broker: "",
    industry: "",
    limit: 50,
    offset: 0,
    sortBy: "score",
    sortDirection: "desc",
    radarFilter: "",
  });
  const request = useQuery({
    queryKey: computed(() => jGoldKeys.research({ ...filters })),
    queryFn: () =>
      apiRequest<ResearchData>({
        url: "/j-gold/research",
        params: {
          recommendation_month: `${filters.month}-01`,
          broker_name: filters.broker || undefined,
          industry: filters.industry || undefined,
          limit: filters.limit,
          offset: filters.offset,
          sort_by: filters.sortBy,
          sort_direction: filters.sortDirection,
          radar_filter: filters.radarFilter || undefined,
        },
      }),
    placeholderData: keepPreviousData,
  });

  function applyFilters(): void {
    filters.month = draftMonth.value;
    filters.broker = draftBroker.value.trim();
    filters.industry = draftIndustry.value.trim();
    filters.offset = 0;
    filters.radarFilter = "";
  }

  function clearFilters(): void {
    draftBroker.value = "";
    draftIndustry.value = "";
    const latest = request.data.value?.selected_month?.slice(0, 7);
    if (latest) draftMonth.value = latest;
    applyFilters();
  }

  function sort(field: string): void {
    if (filters.sortBy === field) {
      filters.sortDirection = filters.sortDirection === "desc" ? "asc" : "desc";
    } else {
      filters.sortBy = field;
      filters.sortDirection = "desc";
    }
    filters.offset = 0;
  }

  function drillRadar(filter: RadarFilter): void {
    filters.radarFilter = filter;
    filters.offset = 0;
  }

  function clearRadarFilter(): void {
    filters.radarFilter = "";
    filters.offset = 0;
  }

  return {
    draftMonth,
    draftBroker,
    draftIndustry,
    filters,
    data: computed(() => request.data.value ?? null),
    loading: request.isPending,
    refreshing: request.isFetching,
    error: computed(() =>
      request.error.value ? "J金股数据加载失败，请稍后重试" : "",
    ),
    applyFilters,
    clearFilters,
    sort,
    drillRadar,
    clearRadarFilter,
    previous: () =>
      (filters.offset = Math.max(0, filters.offset - filters.limit)),
    next: () => {
      if (request.data.value?.pagination.has_more)
        filters.offset += filters.limit;
    },
    refresh: request.refetch,
  };
}
