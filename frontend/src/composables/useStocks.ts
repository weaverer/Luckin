import { useQuery } from "@tanstack/vue-query";
import { computed, reactive, ref, toValue, type MaybeRef } from "vue";

import { apiRequest } from "@/api/client/http";
import { stockKeys, type StockListFilters } from "@/api/query-keys/stocks";

export interface Stock {
  stock_id: string;
  market_code: string;
  venue_code: string;
  security_code: string;
  name: string;
  listing_status: string;
}

export interface Pagination {
  limit: number;
  offset: number;
  total: number;
  has_more: boolean;
}

export interface StockPage {
  items: Stock[];
  pagination: Pagination;
}

export interface DailyQuote {
  trade_date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  pre_close: string;
  change: string;
  pct_chg: string;
  vol: string;
  amount: string;
  updated_at: string;
}

export interface StockDetail extends Stock {
  latest_quote: DailyQuote | null;
  market_data_status: "CURRENT" | "STALE" | "MISSING";
}

const EMPTY_PAGE: StockPage = {
  items: [],
  pagination: { limit: 50, offset: 0, total: 0, has_more: false },
};

export function useStocks() {
  const draftQuery = ref("");
  const filters = reactive<StockListFilters>({
    query: "",
    venueCode: "",
    listingStatus: "",
    limit: 50,
    offset: 0,
  });
  const request = useQuery({
    queryKey: computed(() => stockKeys.list({ ...filters })),
    queryFn: () =>
      apiRequest<StockPage>({
        url: "/stocks",
        params: {
          query: filters.query || undefined,
          venue_code: filters.venueCode || undefined,
          listing_status: filters.listingStatus || undefined,
          limit: filters.limit,
          offset: filters.offset,
        },
      }),
  });

  function search(query = draftQuery.value): void {
    draftQuery.value = query.trim();
    filters.query = draftQuery.value;
    filters.offset = 0;
  }

  function applyFilters(venueCode: string, listingStatus: string): void {
    filters.venueCode = venueCode;
    filters.listingStatus = listingStatus;
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
    draftQuery,
    filters,
    items: computed(() => request.data.value?.items ?? EMPTY_PAGE.items),
    pagination: computed(
      () => request.data.value?.pagination ?? EMPTY_PAGE.pagination,
    ),
    loading: request.isPending,
    fetching: request.isFetching,
    error: computed(() =>
      request.error.value ? "股票加载失败，请稍后重试" : "",
    ),
    search,
    applyFilters,
    previous,
    next,
    refresh: request.refetch,
  };
}

export function useStockPicker() {
  const draftQuery = ref("");
  const query = ref("");
  const request = useQuery({
    queryKey: computed(() => stockKeys.picker(query.value)),
    queryFn: async () => {
      const items: Stock[] = [];
      let offset = 0;
      const limit = 1000;
      for (;;) {
        const page = await apiRequest<StockPage>({
          url: "/stocks",
          params: { query: query.value || undefined, limit, offset },
        });
        items.push(...page.items);
        if (!page.pagination.has_more) return items;
        offset += limit;
        if (offset >= 20_000) throw new Error("股票候选数量超过安全上限");
      }
    },
  });

  function search(): void {
    const next = draftQuery.value.trim();
    if (next === query.value) void request.refetch();
    else query.value = next;
  }

  return {
    draftQuery,
    items: computed(() => request.data.value ?? []),
    fetching: request.isFetching,
    error: computed(() =>
      request.error.value ? "股票候选加载失败，请稍后重试" : "",
    ),
    search,
  };
}

export function useStockDetail(stockId: MaybeRef<string>) {
  const detail = useQuery({
    queryKey: computed(() => stockKeys.detail(toValue(stockId))),
    queryFn: () =>
      apiRequest<StockDetail>({ url: `/stocks/${toValue(stockId)}` }),
  });
  const quotes = useQuery({
    queryKey: computed(() => stockKeys.quotes(toValue(stockId), 120)),
    queryFn: () =>
      apiRequest<DailyQuote[]>({
        url: `/stocks/${toValue(stockId)}/daily-quotes`,
        params: { limit: 120 },
      }),
  });
  return {
    stock: detail.data,
    quotes: computed(() => quotes.data.value ?? []),
    loading: computed(() => detail.isPending.value || quotes.isPending.value),
    error: computed(() =>
      detail.error.value || quotes.error.value
        ? "行情加载失败，请稍后重试"
        : "",
    ),
    refresh: async () => {
      await Promise.all([detail.refetch(), quotes.refetch()]);
    },
  };
}
