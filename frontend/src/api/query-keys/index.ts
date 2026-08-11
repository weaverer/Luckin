export const queryKeys = {
  session: ["session"] as const,
  calendar: (range: string) => ["calendar", range] as const,
  stocks: (filters: Readonly<Record<string, unknown>>) =>
    ["stocks", "list", filters] as const,
  stock: (stockId: string) => ["stocks", "detail", stockId] as const,
  watchlists: ["watchlists"] as const,
  brokerRecommendations: (month: string) =>
    ["broker-recommendations", month] as const,
  taskStatus: (businessDate: string) => ["task-status", businessDate] as const,
};
