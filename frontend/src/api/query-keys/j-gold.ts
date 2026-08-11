export const jGoldKeys = {
  all: ["j-gold"] as const,
  research: (filters: Record<string, unknown>) =>
    [...jGoldKeys.all, "research", filters] as const,
  detail: (stockId: string, month: string) =>
    [...jGoldKeys.all, "detail", stockId, month] as const,
};
