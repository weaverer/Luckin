export interface StockListFilters {
  query: string;
  venueCode: string;
  listingStatus: string;
  limit: number;
  offset: number;
}

export const stockKeys = {
  all: ["stocks"] as const,
  list: (filters: StockListFilters) => ["stocks", "list", filters] as const,
  picker: (query: string) => ["stocks", "picker", query] as const,
  detail: (stockId: string) => ["stocks", "detail", stockId] as const,
  quotes: (stockId: string, limit: number) =>
    ["stocks", "detail", stockId, "daily-quotes", limit] as const,
};
