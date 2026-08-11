export interface BrokerRecommendationFilters {
  month: string;
  broker: string;
  offset: number;
  limit: number;
}

export const brokerRecommendationKeys = {
  all: ["broker-recommendations"] as const,
  list: (filters: BrokerRecommendationFilters) =>
    ["broker-recommendations", "list", filters] as const,
};
