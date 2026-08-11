import { useQuery } from "@tanstack/vue-query";
import { computed, reactive, ref } from "vue";

import { apiRequest } from "@/api/client/http";
import {
  brokerRecommendationKeys,
  type BrokerRecommendationFilters,
} from "@/api/query-keys/broker-recommendations";
import type { Pagination, Stock } from "@/composables/useStocks";
import { formatShanghaiMonth } from "@/utils/date";

export interface BrokerRecommendation {
  recommendation_id: string;
  recommendation_month: string;
  broker_name: string;
  stock: Stock;
  updated_at: string;
}

interface RecommendationPage {
  items: BrokerRecommendation[];
  pagination: Pagination;
}

export function useBrokerRecommendations() {
  const draftMonth = ref(formatShanghaiMonth());
  const draftBroker = ref("");
  const filters = reactive<BrokerRecommendationFilters>({
    month: draftMonth.value,
    broker: "",
    offset: 0,
    limit: 50,
  });
  const request = useQuery({
    queryKey: computed(() => brokerRecommendationKeys.list({ ...filters })),
    queryFn: () =>
      apiRequest<RecommendationPage>({
        url: "/broker-recommendations",
        params: {
          recommendation_month: filters.month + "-01",
          broker_name: filters.broker || undefined,
          offset: filters.offset,
          limit: filters.limit,
        },
      }),
  });

  function search(): void {
    filters.month = draftMonth.value;
    filters.broker = draftBroker.value.trim();
    filters.offset = 0;
  }

  function previous(): void {
    filters.offset = Math.max(0, filters.offset - filters.limit);
  }

  function next(): void {
    if (request.data.value?.pagination.has_more)
      filters.offset += filters.limit;
  }

  return {
    draftMonth,
    draftBroker,
    items: computed(() => request.data.value?.items ?? []),
    pagination: computed(
      () =>
        request.data.value?.pagination ?? {
          limit: filters.limit,
          offset: filters.offset,
          total: 0,
          has_more: false,
        },
    ),
    updatedAt: computed(
      () =>
        request.data.value?.items
          .map((item) => item.updated_at)
          .sort()
          .at(-1) ?? null,
    ),
    loading: request.isPending,
    error: computed(() =>
      request.error.value ? "金股数据加载失败，请稍后重试" : "",
    ),
    search,
    previous,
    next,
    refresh: request.refetch,
  };
}
