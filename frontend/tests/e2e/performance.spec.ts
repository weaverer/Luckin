import { expect, test, type Page } from "@playwright/test";

const timestamp = "2026-08-08T12:00:00Z";

function envelope(data: unknown) {
  return {
    code: 0,
    message: "",
    data,
    errors: [],
    request_id: "performance-e2e",
    timestamp,
  };
}

async function installApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            user: { user_id: "u1", username: "tester", display_name: "测试员" },
            csrf_token: "csrf-token",
            expires_at: "2026-08-09T12:00:00Z",
          }),
        ),
      });
      return;
    }
    if (url.pathname.endsWith("/stocks")) {
      const query = url.searchParams.get("query") ?? "";
      const items = Array.from({ length: 50 }, (_, index) => ({
        stock_id: `stock-${index}`,
        market_code: "CN-S",
        venue_code: "XSHG",
        security_code: `${600000 + index}`,
        name: query ? `搜索结果${index}` : `股票${index}`,
        listing_status: "ACTIVE",
      }));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            items,
            pagination: { limit: 50, offset: 0, total: 10_000, has_more: true },
          }),
        ),
      });
      return;
    }
    await route.abort();
  });
}

test("万级股票数据首屏与搜索满足交互预算", async ({ page }) => {
  await installApi(page);
  const initialStarted = Date.now();
  await page.goto("/stocks");
  await expect(page.getByText("10000 只股票", { exact: true })).toBeVisible();
  expect(Date.now() - initialStarted).toBeLessThan(3_000);

  const searchStarted = Date.now();
  await page.getByLabel("代码或名称").fill("600000");
  await page.getByRole("button", { name: "查询股票" }).click();
  await expect(page.getByText("搜索结果0", { exact: true })).toBeVisible();
  expect(Date.now() - searchStarted).toBeLessThan(2_000);
});
