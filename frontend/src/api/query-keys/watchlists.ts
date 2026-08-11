export const watchlistKeys = {
  all: ["watchlists"] as const,
  list: () => ["watchlists", "list"] as const,
};
