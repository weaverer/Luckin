import { mount } from "@vue/test-utils";
import { computed, ref } from "vue";

import BrokerRecommendationsView from "@/views/BrokerRecommendationsView.vue";
import TaskStatusView from "@/views/TaskStatusView.vue";

const statuses = [
  "SUCCEEDED",
  "PARTIAL",
  "FAILED",
  "RUNNING",
  "UNKNOWN",
  "NOT_RUN",
] as const;

vi.mock("@/composables/useBrokerRecommendations", () => ({
  useBrokerRecommendations: () => ({
    draftMonth: ref("2026-08"),
    draftBroker: ref(""),
    items: computed(() => [
      {
        recommendation_id: "r1",
        recommendation_month: "2026-08-01",
        broker_name: "中信证券",
        stock: {
          stock_id: "s1",
          market_code: "CN-S",
          venue_code: "XSHG",
          security_code: "600000",
          name: "浦发银行",
          listing_status: "ACTIVE",
        },
        updated_at: "2026-08-08T08:00:00Z",
      },
    ]),
    pagination: computed(() => ({
      limit: 50,
      offset: 0,
      total: 1,
      has_more: false,
    })),
    updatedAt: computed(() => "2026-08-08T08:00:00Z"),
    loading: ref(false),
    error: computed(() => ""),
    search: vi.fn(),
    previous: vi.fn(),
    next: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/composables/useJGoldResearch", () => ({
  useJGoldResearch: () => ({
    draftMonth: ref("2026-08"),
    draftBroker: ref(""),
    draftIndustry: ref(""),
    filters: { sortBy: "score", sortDirection: "desc" },
    data: computed(() => ({
      metrics: {
        monthly_count: 1,
        broker_count: 1,
        industry_count: 0,
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
        average_excess_20d: null,
        excess_sample_count: 0,
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
          industry: null,
          broker_count: 1,
          brokers: ["中信证券"],
          month_delta: 1,
          consecutive_months: 1,
          excess_20d: null,
          status: "新晋",
          score: 50,
          score_components: {},
          quality: "insufficient",
          quality_explanation: "行情样本不足",
        },
      ],
      pagination: { limit: 50, offset: 0, total: 1, has_more: false },
      signals: [],
      industries: [],
      broker_ability: [],
      diffusion: [],
      quality: {
        status: "partial",
        explanation: "行业分类不可用",
        source: "券商金股同步",
        generated_at: "2026-08-08T08:00:00Z",
      },
      selected_month: "2026-08-01",
      available_months: ["2026-08-01"],
    })),
    loading: ref(false),
    refreshing: ref(false),
    error: computed(() => ""),
    applyFilters: vi.fn(),
    clearFilters: vi.fn(),
    sort: vi.fn(),
    previous: vi.fn(),
    next: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/composables/useTaskStatus", async (source) => {
  const actual = await source<typeof import("@/composables/useTaskStatus")>();
  return {
    ...actual,
    useTaskStatus: () => ({
      mode: ref("snapshot"),
      businessDate: ref("2026-08-08"),
      data: computed(() => ({
        summary_id: "summary-1",
        business_date: "2026-08-08",
        status: "READY",
        notification_status: "FAILED",
        generated_at: "2026-08-08T12:01:00Z",
        notified_at: null,
        latest_notification_attempt: {
          attempt_no: 3,
          trigger_kind: "AUTOMATIC",
          status: "FAILED",
          error_category: "NETWORK",
          error_summary: "飞书通知网络请求失败",
          started_at: "2026-08-08T12:02:00Z",
          completed_at: "2026-08-08T12:02:10Z",
          retryable: true,
        },
        counts: Object.fromEntries(statuses.map((status) => [status, 1])),
        items: statuses.map((status) => ({
          task_key: status,
          schedule_slug: status,
          display_name: status,
          status,
          source_run_id: null,
          started_at: "2026-08-08T01:00:00Z",
          completed_at: "2026-08-08T01:05:00Z",
          record_count: null,
          error_category: status === "FAILED" ? "UPSTREAM" : null,
          error_summary: status === "FAILED" ? "安全错误摘要" : null,
          observed_at: "2026-08-08T12:00:00Z",
        })),
      })),
      loading: ref(false),
      fetching: ref(false),
      error: computed(() => ""),
      refresh: vi.fn(),
    }),
  };
});

test("J金股驾驶舱展示总览、机会雷达和数据质量", () => {
  const wrapper = mount(BrokerRecommendationsView, {
    global: {
      stubs: {
        RouterLink: { template: "<a><slot /></a>" },
        AsyncState: { template: "<div><slot /></div>" },
        StockResearchDrawer: true,
      },
    },
  });
  expect(wrapper.text()).toContain("600000");
  expect(wrapper.text()).toContain("J金股");
  expect(wrapper.text()).toContain("本月金股");
  expect(wrapper.text()).toContain("行业分类不可用");
});

test("任务快照用文本和图标呈现六态及通知失败", () => {
  const wrapper = mount(TaskStatusView, {
    global: {
      stubs: {
        AsyncState: { template: "<div><slot /></div>" },
        AppSurface: { template: "<section><slot /></section>" },
      },
    },
  });
  for (const label of [
    "成功",
    "部分完成",
    "失败",
    "运行中",
    "未知",
    "未执行",
  ]) {
    expect(wrapper.text()).toContain(label);
  }
  expect(wrapper.text()).toContain("安全错误摘要");
  expect(wrapper.text()).toContain("开始");
  expect(wrapper.text()).toContain("完成");
  expect(wrapper.text()).toContain("第 3 次尝试");
  expect(wrapper.text()).toContain("飞书通知网络请求失败");
  expect(wrapper.text()).toContain("FAILED");
  expect(wrapper.find('[data-status="FAILED"]').exists()).toBe(true);
});
