import { formatShanghaiDate, formatShanghaiMonth } from "@/utils/date";

describe("Shanghai business dates", () => {
  it("does not fall back to the previous UTC date after Shanghai midnight", () => {
    const instant = new Date("2026-08-08T16:30:00Z");
    expect(formatShanghaiDate(instant)).toBe("2026-08-09");
    expect(formatShanghaiMonth(instant)).toBe("2026-08");
  });
});
