import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { computed, ref } from "vue";

import DashboardView from "@/views/DashboardView.vue";
import LoginView from "@/views/LoginView.vue";

vi.mock("@/composables/useTaskStatus", () => ({
  taskStatuses: [
    "SUCCEEDED",
    "PARTIAL",
    "FAILED",
    "RUNNING",
    "UNKNOWN",
    "NOT_RUN",
  ],
  countTaskStatuses: (items: Array<{ status: string }>) => ({
    SUCCEEDED: items.filter((item) => item.status === "SUCCEEDED").length,
    PARTIAL: items.filter((item) => item.status === "PARTIAL").length,
    FAILED: items.filter((item) => item.status === "FAILED").length,
    RUNNING: items.filter((item) => item.status === "RUNNING").length,
    UNKNOWN: items.filter((item) => item.status === "UNKNOWN").length,
    NOT_RUN: items.filter((item) => item.status === "NOT_RUN").length,
  }),
  useTaskStatus: () => ({
    mode: ref("live"),
    businessDate: ref("2026-08-09"),
    data: computed(() => ({
      business_date: "2026-08-09",
      observed_at: "2026-08-09T12:00:00Z",
      items: [
        {
          task_key: "calendar",
          schedule_slug: "calendar",
          display_name: "交易日历同步",
          status: "SUCCEEDED",
          source_run_id: "run-1",
          started_at: "2026-08-09T01:00:00Z",
          completed_at: "2026-08-09T01:01:00Z",
          record_count: 100,
          error_category: null,
          error_summary: null,
          observed_at: "2026-08-09T12:00:00Z",
        },
      ],
    })),
    loading: ref(false),
    fetching: ref(false),
    error: computed(() => ""),
    refresh: vi.fn(),
  }),
}));

const TestHost = { template: "<div />" };

function mountGlobal() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "dashboard", component: TestHost },
      { path: "/login", name: "login", component: TestHost },
    ],
  });
  return {
    plugins: [createPinia(), router],
    stubs: { RouterLink: { template: "<a><slot /></a>" } },
  };
}

describe("login and dashboard", () => {
  it("renders an accessible login form and validation area", () => {
    const wrapper = mount(LoginView, { global: mountGlobal() });

    expect(wrapper.get('label[for="username"]').text()).toContain("账号");
    expect(wrapper.get('label[for="password"]').text()).toContain("密码");
    expect(wrapper.get('button[type="submit"]').text()).toContain("登录");
    expect(wrapper.get("form").attributes("aria-busy")).toBe("false");
  });

  it("shows every in-scope entry and truthful delivery state", () => {
    const wrapper = mount(DashboardView, { global: mountGlobal() });
    for (const label of [
      "日历",
      "股票与行情",
      "自选分组",
      "券商金股",
      "任务执行",
      "账号设置",
    ]) {
      expect(wrapper.text()).toContain(label);
    }
    expect(wrapper.text()).not.toContain("尚未交付");
    expect(wrapper.text()).toContain("当日任务摘要");
    expect(wrapper.get('[data-status="SUCCEEDED"]').text()).toContain("成功");
    expect(wrapper.get('[data-status="SUCCEEDED"] strong').text()).toBe("1");
    expect(wrapper.text()).not.toContain("等待任务状态能力交付");
    expect(wrapper.text()).not.toContain("模拟数据");
  });
});
