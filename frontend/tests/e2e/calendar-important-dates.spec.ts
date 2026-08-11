import { expect, test, type Page } from "@playwright/test";

const now = "2026-08-08T12:00:00Z";
const session = {
  user: { user_id: "owner-a", username: "majie", display_name: "超级管理员" },
  csrf_token: "csrf-token",
  expires_at: "2026-08-09T12:00:00Z",
};

function envelope(data: unknown, code = 0, message = "") {
  return {
    code,
    message,
    data,
    errors: [],
    request_id: "e2e-calendar",
    timestamp: now,
  };
}

async function installApi(
  page: Page,
  userId = "owner-a",
  storage = new Map<string, Array<Record<string, unknown>>>(),
): Promise<void> {
  const dates = storage.get(userId) ?? [];
  storage.set(userId, dates);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({ ...session, user: { ...session.user, user_id: userId } }),
        ),
      });
      return;
    }
    if (path.endsWith("/calendar")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope([
            {
              date: "2026-08-08",
              market_code: "CN-S",
              market_status: "OPEN",
              previous_open_date: "2026-08-07",
              calendar_updated_at: now,
              important_dates: dates,
            },
          ]),
        ),
      });
      return;
    }
    if (path.endsWith("/important-dates") && request.method() === "POST") {
      const input = request.postDataJSON() as {
        event_date: string;
        title: string;
        notes: string | null;
      };
      if (
        dates.some(
          (item) =>
            item.event_date === input.event_date && item.title === input.title,
        )
      ) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify(envelope(null, 400001, "重要日已存在")),
        });
        return;
      }
      dates.push({
        ...input,
        important_date_id: "date-1",
        created_at: now,
        updated_at: now,
      });
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(envelope(dates.at(-1))),
      });
      return;
    }
    if (
      path.endsWith("/important-dates/date-1") &&
      request.method() === "PUT"
    ) {
      const input = request.postDataJSON() as {
        event_date: string;
        title: string;
        notes: string | null;
      };
      Object.assign(dates[0]!, input, { updated_at: now });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(envelope(dates[0])),
      });
      return;
    }
    if (
      path.endsWith("/important-dates/date-1") &&
      request.method() === "DELETE"
    ) {
      dates.splice(0, dates.length);
      await route.fulfill({ status: 204 });
      return;
    }
    await route.abort();
  });
}

test("重要日保存刷新后仍存在，删除不改变交易日状态", async ({ page }) => {
  await installApi(page);
  await page.goto("/calendar");
  const calendar = page.locator(".calendar");
  await expect(calendar.getByText("交易日", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加重要日" }).click();
  await page.locator("#important-date-date").fill("2026-08-08");
  await page.getByLabel("标题").fill("财报发布");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(calendar.getByText("财报发布")).toBeVisible();
  await page.reload();
  await expect(calendar.getByText("财报发布")).toBeVisible();
  await page.getByRole("button", { name: "编辑重要日" }).click();
  await page.getByLabel("标题").fill("财报发布（更新）");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(calendar.getByText("财报发布（更新）")).toBeVisible();
  await page.reload();
  await expect(calendar.getByText("财报发布（更新）")).toBeVisible();
  await page.getByRole("button", { name: "添加重要日" }).click();
  await page.locator("#important-date-date").fill("2026-08-08");
  await page.getByLabel("标题").fill("财报发布（更新）");
  const conflict = page.waitForResponse(
    (response) =>
      response.url().endsWith("/important-dates") && response.status() === 409,
  );
  await page.getByRole("button", { name: "保存" }).click();
  await conflict;
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除重要日" }).click();
  await expect(page.getByText("财报发布（更新）")).toHaveCount(0);
  await expect(calendar.getByText("交易日", { exact: true })).toBeVisible();
});

test("不同用户看不到彼此的重要日", async ({ browser }) => {
  const storage = new Map<string, Array<Record<string, unknown>>>([
    [
      "owner-a",
      [
        {
          event_date: "2026-08-08",
          title: "仅用户 A 可见",
          notes: null,
          important_date_id: "private-date",
          created_at: now,
          updated_at: now,
        },
      ],
    ],
  ]);
  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  await installApi(pageA, "owner-a", storage);
  await installApi(pageB, "owner-b", storage);
  await pageA.goto("/calendar");
  await pageB.goto("/calendar");
  await expect(
    pageA.locator(".calendar").getByText("仅用户 A 可见"),
  ).toBeVisible();
  await expect(pageB.getByText("仅用户 A 可见")).toHaveCount(0);
  await contextA.close();
  await contextB.close();
});
