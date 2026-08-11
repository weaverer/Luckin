import { expect, test, type Page, type Route } from "@playwright/test";

const sessionData = {
  user: { user_id: "user-1", username: "admin", display_name: "管理员" },
  csrf_token: "csrf-test-token",
  expires_at: "2026-08-09T12:00:00Z",
};

function envelope(data: unknown) {
  return {
    code: 0,
    message: "",
    data,
    errors: [],
    request_id: "e2e-request-id",
    timestamp: "2026-08-08T12:00:00Z",
  };
}

async function installAuthApi(page: Page): Promise<void> {
  let authenticated = false;
  await page.route("**/api/v1/task-status**", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        envelope({
          business_date: "2026-08-09",
          observed_at: "2026-08-09T12:00:00Z",
          items: [],
        }),
      ),
    });
  });
  await page.route("**/api/v1/auth/**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/login")) {
      authenticated = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(envelope(sessionData)),
      });
      return;
    }
    if (path.endsWith("/me")) {
      await route.fulfill({
        status: authenticated ? 200 : 401,
        contentType: "application/json",
        body: JSON.stringify(
          authenticated
            ? envelope(sessionData)
            : {
                ...envelope(null),
                code: 1001,
                message: "请先登录",
                errors: [{ field: null, reason: "unauthorized" }],
              },
        ),
      });
      return;
    }
    if (path.endsWith("/logout") || path.endsWith("/password")) {
      authenticated = false;
      await route.fulfill({ status: 204 });
      return;
    }
    await route.abort();
  });
}

async function login(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("账号").fill("admin");
  await page.getByLabel("密码").fill("correct-password");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test.beforeEach(async ({ page }) => {
  await installAuthApi(page);
});

test("未登录重定向、登录后完整导航并可退出", async ({ page }) => {
  await login(page);

  for (const label of [
    "日历",
    "股票与行情",
    "自选分组",
    "券商金股",
    "任务执行",
  ]) {
    await expect(page.getByRole("link", { name: label }).first()).toBeVisible();
  }

  await page.getByRole("link", { name: "账号设置" }).first().click();
  await expect(page.getByRole("heading", { name: "账号设置" })).toBeVisible();
  await page.getByRole("button", { name: "安全退出" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/calendar");
  await expect(page).toHaveURL(/\/login\?redirect=/);
});

test("修改密码后撤销当前会话", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "账号设置" }).first().click();
  await page.getByLabel("当前密码").fill("correct-password");
  await page.getByLabel("新密码").fill("new-password-1234");
  await page.getByRole("button", { name: "保存并重新登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
});
