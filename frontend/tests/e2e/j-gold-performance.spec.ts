import { expect, test, type Page } from "@playwright/test";

const now = "2026-08-08T12:00:00Z";
const envelope = (data: unknown) => ({
  code: 0,
  message: "",
  data,
  errors: [],
  request_id: "j-gold-e2e",
  timestamp: now,
});

async function api(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            user: { user_id: "u1", username: "tester", display_name: "研究员" },
            csrf_token: "csrf",
            expires_at: "2026-08-09T12:00:00Z",
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/watchlists")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope([
            {
              group_id: "group-1",
              name: "研究观察",
              notes: "",
              tags: [],
              sort_order: 0,
              members: [],
            },
          ]),
        ),
      });
      return;
    }
    if (path.endsWith("/j-gold/stocks/s0")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            stock: {
              stock_id: "s0",
              security_code: "600000",
              name: "研究股票0",
              market_code: "CN-S",
              listing_status: "ACTIVE",
            },
            industry: "科技",
            recommendations: [
              {
                broker_name: "研究券商",
                recommendation_month: "2026-08-01",
                updated_at: now,
              },
            ],
            history: [{ month: "2026-08-01", broker_count: 1 }],
            latest_quote_date: "2026-08-08",
            price_basis: "后复权收盘价；成交量与成交额为原始口径",
            source: "测试夹具数据",
            generated_at: now,
            quality: "ready",
          }),
        ),
      });
      return;
    }
    if (path.endsWith("/watchlists/group-1/members")) {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({ member_id: "member-1", stock: {}, sort_order: 0 }),
        ),
      });
      return;
    }
    if (path.endsWith("/j-gold/research")) {
      const allItems = Array.from({ length: 20 }, (_, index) => ({
        stock: {
          stock_id: `s${index}`,
          security_code: `${600000 + index}`,
          name: `研究股票${index}`,
          market_code: "CN-S",
          listing_status: "ACTIVE",
        },
        industry: index % 2 ? "银行" : "科技",
        broker_count: (index % 6) + 1,
        brokers: ["研究券商"],
        month_delta: index % 3,
        is_new: index < 4,
        three_month_peak: index % 3 === 2,
        breakout: index < 2,
        consecutive_months: (index % 5) + 1,
        excess_20d: index / 10,
        status: index % 4 ? "持续" : "高共识",
        score: 50 + index,
        score_components: { consensus: 60, warming: 40 },
        quality: "ready",
        quality_explanation: "数据可用",
      }));
      const radarFilter = url.searchParams.get("radar_filter");
      const items = allItems.filter((item) => {
        if (!radarFilter || radarFilter === "monthly") return true;
        if (radarFilter === "new") return item.is_new;
        if (radarFilter === "consensus") return item.broker_count >= 5;
        if (radarFilter === "warming") return item.month_delta > 0;
        if (radarFilter === "breakout") return item.breakout;
        if (radarFilter === "excess") return item.excess_20d != null;
        return false;
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          envelope({
            metrics: {
              monthly_count: 20,
              broker_count: 6,
              industry_count: 2,
              new_count: 4,
              new_change: 2,
              consensus_count: 3,
              warming_count: 8,
              warming_three_months: [
                { month: "2026-06-01", count: 4 },
                { month: "2026-07-01", count: 6 },
                { month: "2026-08-01", count: 8 },
              ],
              breakout_count: 2,
              average_excess_20d: 1.35,
              excess_sample_count: 20,
            },
            items,
            pagination: {
              limit: 50,
              offset: 0,
              total: items.length,
              has_more: false,
            },
            signals: Array.from({ length: 24 }, (_, index) => ({
              stock: { name: `异动股票${index}` },
              type: "推荐升温",
              summary: "本月新增多家券商推荐",
              comparison_period: "2026-07 至 2026-08",
              trigger_rule: "推荐券商数较上月增加",
              data_time: "2026-08-08",
              quality: "ready",
            })),
            industries: [
              {
                industry: "银行",
                recommendation_records: 10,
                stock_count: 10,
                broker_count: 4,
                month_delta: 2,
                heat_rank: 1,
                quality: "ready",
              },
            ],
            broker_ability: [
              {
                broker_name: "研究券商",
                sample_count: 24,
                coverage: 92,
                average_excess_20d: 1.2,
                positive_ratio: 58.3,
                grade: "A",
              },
            ],
            diffusion: Array.from({ length: 8 }, (_, i) => ({
              month: `2026-${String(i + 1).padStart(2, "0")}-01`,
              stock_count: 12 + i,
              month_delta: i ? 1 : null,
              quality: "ready",
            })),
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
    await route.abort();
  });
}

test("桌面与窄屏在三秒内呈现驾驶舱结构", async ({ page }) => {
  await api(page);
  await page.goto("/broker-recommendations", { waitUntil: "commit" });
  await Promise.all([
    expect(page.getByRole("heading", { name: "J金股" })).toBeVisible({
      timeout: 3_000,
    }),
    expect(page.locator(".page-state, .quality-line").first()).toBeVisible({
      timeout: 3_000,
    }),
  ]);
  await expect(page.getByText("机会雷达 · 综合排名")).toBeVisible();
  await page.evaluate(() => {
    const marker = document.createElement("p");
    marker.textContent = "测试夹具数据，仅用于界面验收";
    marker.setAttribute("data-testid", "fixture-marker");
    marker.style.cssText =
      "position:fixed;right:12px;bottom:12px;z-index:99;padding:8px 12px;margin:0;border-radius:8px;background:#25344d;color:#fff;font:12px sans-serif";
    document.body.append(marker);
  });
  await page.screenshot({
    path: "test-results/j-gold-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "应用筛选" })).toBeVisible();
  await page.screenshot({
    path: "test-results/j-gold-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "切换暗色主题" }).click();
  await page.screenshot({
    path: "test-results/j-gold-dark.png",
    fullPage: true,
  });
});

test("股票详情展示事实依据并可加入自选而不触发交易", async ({ page }) => {
  await api(page);
  await page.goto("/broker-recommendations");
  const firstResearchRow = page
    .getByText("研究股票0", { exact: true })
    .locator("xpath=ancestor::tr");
  await firstResearchRow.focus();
  await firstResearchRow.press("Enter");
  await expect(page.getByRole("heading", { name: "研究股票0" })).toBeVisible();
  await expect(
    page.getByText("推荐月 2026-08", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("后复权收盘价", { exact: false })).toBeVisible();
  await page.getByLabel("选择自选分组").selectOption("group-1");
  const memberRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith("/watchlists/group-1/members"),
  );
  await page.getByRole("button", { name: "加入自选" }).click();
  await memberRequest;
  await expect(page.getByText("不会产生任何交易行为")).toBeVisible();
  expect(
    await page.locator('a[href*="trade"], button:has-text("买入")').count(),
  ).toBe(0);
});

test("指标明细正确筛选且主面板等高、表格分隔线对齐", async ({ page }) => {
  await api(page);
  await page.goto("/broker-recommendations");

  const request = page.waitForRequest((candidate) => {
    const url = new URL(candidate.url());
    return (
      url.pathname.endsWith("/j-gold/research") &&
      url.searchParams.get("radar_filter") === "new"
    );
  });
  await page
    .getByText("新晋金股", { exact: true })
    .locator("xpath=ancestor::article")
    .getByRole("button", { name: "查看明细" })
    .click();
  await request;
  await expect(page.getByText("当前明细：新晋金股")).toBeVisible();
  await expect(page.getByText("共 4 只", { exact: true })).toBeVisible();
  await expect(page.getByText("研究股票4", { exact: true })).toHaveCount(0);

  const panelMeasurements = await page.evaluate(() => {
    const radar = document.querySelector<HTMLElement>(".radar-panel");
    const signals = document.querySelector<HTMLElement>(
      ".primary-grid .module",
    );
    const list = signals?.querySelector<HTMLElement>("ul");
    const cells = Array.from(
      document.querySelectorAll<HTMLElement>(
        ".radar-panel tbody tr:first-child td",
      ),
    );
    return {
      radarHeight: radar?.getBoundingClientRect().height ?? 0,
      signalsHeight: signals?.getBoundingClientRect().height ?? 0,
      signalsScrolls: !!list && list.scrollHeight > list.clientHeight,
      cellBottoms: cells.map((cell) =>
        Math.round(cell.getBoundingClientRect().bottom),
      ),
    };
  });
  expect(
    Math.abs(panelMeasurements.radarHeight - panelMeasurements.signalsHeight),
  ).toBeLessThan(1);
  expect(panelMeasurements.signalsScrolls).toBe(true);
  expect(new Set(panelMeasurements.cellBottoms).size).toBe(1);
});
