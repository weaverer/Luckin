import { expect, test, type Page } from "@playwright/test";

const now = "2026-08-08T12:00:00Z";
const stock = {
  stock_id: "stock-1",
  market_code: "CN-S",
  venue_code: "XSHG",
  security_code: "600000",
  name: "浦发银行",
  listing_status: "ACTIVE",
};
const quote = {
  trade_date: "2026-08-08",
  open: "10.00",
  high: "10.30",
  low: "9.90",
  close: "10.20",
  pre_close: "10.00",
  change: "0.20",
  pct_chg: "2.00",
  vol: "1000",
  amount: "10200",
  updated_at: "2026-08-05T08:00:00Z",
};

function envelope(data: unknown) {
  return {
    code: 0,
    message: "",
    data,
    errors: [],
    request_id: "e2e-stocks-watchlists",
    timestamp: now,
  };
}

interface Group {
  group_id: string;
  name: string;
  notes: string;
  tags: string[];
  sort_order: number;
  members: Array<{
    member_id: string;
    stock: typeof stock;
    sort_order: number;
  }>;
}

async function installApi(
  page: Page,
  userId = "owner-a",
  storage = new Map<string, Group[]>(),
): Promise<void> {
  const groups = storage.get(userId) ?? [];
  storage.set(userId, groups);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            user: { user_id: userId, username: userId, display_name: userId },
            csrf_token: "csrf-token",
            expires_at: "2026-08-09T12:00:00Z",
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/stocks") && request.method() === "GET") {
      const query = url.searchParams.get("query") ?? "";
      const items =
        !query ||
        stock.security_code.includes(query) ||
        stock.name.includes(query)
          ? [stock]
          : [];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            items,
            pagination: {
              limit: 50,
              offset: 0,
              total: items.length,
              has_more: false,
            },
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/stocks/stock-1/daily-quotes")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(envelope([quote])),
      });
      return;
    }
    if (path.endsWith("/stocks/stock-1")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            ...stock,
            latest_quote: quote,
            market_data_status: "STALE",
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/watchlists") && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(envelope(groups)),
      });
      return;
    }
    if (path.endsWith("/watchlists/order") && request.method() === "PUT") {
      const body = request.postDataJSON() as { group_ids: string[] };
      const byId = new Map(groups.map((group) => [group.group_id, group]));
      groups.splice(
        0,
        groups.length,
        ...body.group_ids.map((groupId, index) => ({
          ...byId.get(groupId)!,
          sort_order: index,
        })),
      );
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(envelope(groups)),
      });
      return;
    }
    if (path.endsWith("/watchlists") && request.method() === "POST") {
      const body = request.postDataJSON() as {
        name: string;
        notes: string;
        tags: string[];
      };
      groups.push({
        group_id: "group-1",
        name: body.name,
        notes: body.notes,
        tags: body.tags,
        sort_order: 0,
        members: [],
      });
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(envelope(groups[0])),
      });
      return;
    }
    if (
      path.endsWith("/watchlists/group-1/members") &&
      request.method() === "POST"
    ) {
      groups[0]?.members.push({
        member_id: "member-1",
        stock,
        sort_order: 0,
      });
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(envelope(groups[0]?.members[0])),
      });
      return;
    }
    if (
      path.endsWith("/watchlists/group-1/members/stock-1") &&
      request.method() === "DELETE"
    ) {
      groups[0]?.members.splice(0);
      await route.fulfill({ status: 204 });
      return;
    }
    await route.abort();
  });
}

test("搜索股票并展示可辨识的过期行情状态", async ({ page }) => {
  await installApi(page);
  await page.goto("/stocks");
  await page.getByLabel("代码或名称").fill("600000");
  await page.getByRole("button", { name: "查询股票" }).click();
  await expect(page.getByText("600000", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: /600000/ }).click();
  await expect(page.getByText("行情待更新")).toBeVisible();
  await expect(page.getByText(/最新收盘 10.20/)).toBeVisible();
});

test("自选分组及成员刷新后持久化，其他用户不可见", async ({ browser }) => {
  const storage = new Map<string, Group[]>();
  const contextA = await browser.newContext();
  const pageA = await contextA.newPage();
  await installApi(pageA, "owner-a", storage);
  await pageA.goto("/watchlists");
  await pageA.getByRole("button", { name: "添加分组" }).click();
  await pageA.getByLabel(/分组名称/).fill("长线观察");
  await pageA.getByLabel(/备注/).fill("长期价值观察");
  await pageA.getByLabel("新标签").fill("价值");
  await pageA.getByRole("button", { name: "添加标签" }).click();
  await pageA.getByRole("button", { name: "保存分组" }).click();
  await expect(pageA.getByRole("heading", { name: "长线观察" })).toBeVisible();
  const stockSelect = pageA.locator("#stock-group-1");
  await stockSelect.selectOption("stock-1");
  await expect(stockSelect).toHaveValue("stock-1");
  const added = pageA.waitForResponse(
    (response) =>
      response.url().endsWith("/watchlists/group-1/members") &&
      response.request().method() === "POST",
  );
  await pageA.getByRole("button", { name: "加入分组" }).click();
  await added;
  await expect(pageA.getByText("浦发银行", { exact: true })).toBeVisible();
  await pageA.reload();
  await expect(pageA.getByText("浦发银行", { exact: true })).toBeVisible();

  const contextB = await browser.newContext();
  const pageB = await contextB.newPage();
  await installApi(pageB, "owner-b", storage);
  await pageB.goto("/watchlists");
  await expect(pageB.getByText("长线观察")).toHaveCount(0);

  pageA.once("dialog", (dialog) => dialog.accept());
  await pageA.getByRole("button", { name: "移出股票" }).click();
  await expect(pageA.getByText("浦发银行", { exact: true })).toHaveCount(0);
  await contextA.close();
  await contextB.close();
});

test("竖向自选分组支持键盘下移并持久化顺序", async ({ page }) => {
  const storage = new Map<string, Group[]>([
    [
      "owner-a",
      [
        {
          group_id: "group-1",
          name: "长线观察",
          notes: "长期价值观察",
          tags: ["价值"],
          sort_order: 0,
          members: [],
        },
        {
          group_id: "group-2",
          name: "事件观察",
          notes: "事件驱动观察",
          tags: ["事件"],
          sort_order: 1,
          members: [],
        },
      ],
    ],
  ]);
  await installApi(page, "owner-a", storage);
  await page.goto("/watchlists");

  const reordered = page.waitForRequest(
    (request) =>
      request.url().endsWith("/watchlists/order") && request.method() === "PUT",
  );
  await page.getByRole("tab", { name: /长线观察/ }).press("Alt+ArrowDown");
  expect((await reordered).postDataJSON()).toEqual({
    group_ids: ["group-2", "group-1"],
  });
  await expect(page.getByRole("tab")).toHaveText([/事件观察/, /长线观察/]);
});
