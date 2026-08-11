export function parseIsoDate(value: string): Date {
  const [year = 1970, month = 1, day = 1] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

export function formatIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function parseIsoMonth(value: string): Date {
  return parseIsoDate(`${value}-01`);
}

export function formatIsoMonth(value: Date): string {
  return formatIsoDate(value).slice(0, 7);
}

const shanghaiDateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function formatShanghaiDate(value: Date = new Date()): string {
  const parts = Object.fromEntries(
    shanghaiDateFormatter
      .formatToParts(value)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function formatShanghaiMonth(value: Date = new Date()): string {
  return formatShanghaiDate(value).slice(0, 7);
}
