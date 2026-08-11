export const calendarKeys = {
  all: ["calendar"] as const,
  range: (start: string, end: string) => ["calendar", start, end] as const,
};
