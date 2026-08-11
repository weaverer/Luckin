export const taskStatusKeys = {
  all: ["task-status"] as const,
  live: (businessDate: string) =>
    ["task-status", "live", businessDate] as const,
  summary: (businessDate: string) =>
    ["task-status", "summary", businessDate] as const,
};
