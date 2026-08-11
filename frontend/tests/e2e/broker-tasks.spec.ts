import { expect, test, type Page } from "@playwright/test";

const now = "2026-08-08T12:00:00Z";
const statuses = [
  "SUCCEEDED",
  "PARTIAL",
  "FAILED",
  "RUNNING",
  "UNKNOWN",
  "NOT_RUN",
] as const;

function envelope(data: unknown) {
  return {
    code: 0,
    message: "",
    data,
    errors: [],
    request_id: "e2e-broker-tasks",
    timestamp: now,
  };
}

async function installApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            user: { user_id: "u1", username: "majie", display_name: "管理员" },
            csrf_token: "csrf-token",
            expires_at: "2026-08-09T12:00:00Z",
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/j-gold/research")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            metrics: {
              monthly_count: 1,
              broker_count: 1,
              industry_count: 1,
              new_count: 1,
              new_change: 1,
              consensus_count: 0,
              warming_count: 1,
              warming_three_months: [
                { month: "2026-06-01", count: 0 },
                { month: "2026-07-01", count: 0 },
                { month: "2026-08-01", count: 1 },
              ],
              breakout_count: 0,
              average_excess_20d: 1.2,
              excess_sample_count: 1,
            },
            items: [
              {
                stock: {
                  stock_id: "s1",
                  market_code: "CN-S",
                  security_code: "600000",
                  name: "浦发银行",
                  listing_status: "ACTIVE",
                },
                industry: "银行",
                broker_count: 1,
                brokers: ["中信证券"],
                month_delta: 1,
                consecutive_months: 1,
                excess_20d: 1.2,
                status: "新晋",
                score: 62,
                score_components: { consensus: 20, warming: 20 },
                quality: "ready",
                quality_explanation: "数据可用",
              },
            ],
            pagination: { limit: 50, offset: 0, total: 1, has_more: false },
            signals: [],
            industries: [],
            broker_ability: [],
            diffusion: [],
            quality: {
              status: "ready",
              explanation: "数据完整",
              source: "券商金股同步、股票主数据、日线行情",
              generated_at: now,
            },
            selected_month: "2026-08-01",
            available_months: ["2026-08-01"],
          }),
        ),
      });
      return;
    }
    const items = statuses.map((status) => ({
      task_key: status,
      schedule_slug: status,
      display_name: status,
      status,
      source_run_id: null,
      started_at: "2026-08-08T11:30:00Z",
      completed_at: status === "RUNNING" ? null : now,
      record_count: status === "SUCCEEDED" ? 100 : null,
      error_category: status === "FAILED" ? "UPSTREAM" : null,
      error_summary: status === "FAILED" ? "安全错误摘要" : null,
      observed_at: now,
    }));
    if (path.endsWith("/task-status")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({ business_date: "2026-08-08", observed_at: now, items }),
        ),
      });
      return;
    }
    if (path.endsWith("/task-summaries/2026-08-08")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            summary_id: "summary-1",
            business_date: "2026-08-08",
            status: "READY",
            notification_status: "FAILED",
            generated_at: now,
            notified_at: null,
            counts: Object.fromEntries(statuses.map((status) => [status, 1])),
            items,
            latest_notification_attempt: {
              attempt_no: 3,
              trigger_kind: "MANUAL_RETRY",
              status: "FAILED",
              error_category: "NETWORK",
              error_summary: "飞书通知网络请求失败",
              started_at: "2026-08-08T12:00:00Z",
              completed_at: "2026-08-08T12:01:00Z",
              retryable: true,
            },
          }),
        ),
      });
      return;
    }
    await route.abort();
  });
}

test.beforeEach(async ({ page }) => installApi(page));

test("J金股按月份展示总览与机会雷达", async ({ page }) => {
  await page.goto("/broker-recommendations");
  await expect(page.getByRole("heading", { name: "J金股" })).toBeVisible();
  await expect(page.getByText("本月金股")).toBeVisible();
  await expect(page.getByText("600000", { exact: true })).toBeVisible();
  await expect(page.getByText("浦发银行", { exact: false })).toBeVisible();
});

test("实时任务与快照均展示六态及通知失败", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-08-08T12:00:00+08:00"));
  await page.goto("/tasks");
  for (const label of [
    "成功",
    "部分完成",
    "失败",
    "运行中",
    "未知",
    "未执行",
  ]) {
    await expect(
      page.locator(".status-counts").getByText(label, { exact: true }),
    ).toBeVisible();
  }
  await expect(page.getByText("安全错误摘要")).toBeVisible();
  await expect(page.getByText(/^开始 /).first()).toBeVisible();
  await expect(page.getByText(/^完成 /).first()).toBeVisible();
  await page.getByRole("button", { name: "20:00 快照" }).click();
  await expect(page.getByText("通知状态")).toBeVisible();
  await expect(page.getByText("通知失败", { exact: true })).toBeVisible();
  await expect(page.getByText("第 3 次尝试", { exact: false })).toBeVisible();
  await expect(page.getByText("飞书通知网络请求失败")).toBeVisible();
  await page.screenshot({
    path: "test-results/task-status-phase11.png",
    fullPage: true,
  });
});
