import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { computed, ref } from "vue";

import DailyQuoteChart from "@/components/charts/DailyQuoteChart.vue";
import WatchlistsView from "@/views/WatchlistsView.vue";

const mocks = vi.hoisted(() => ({
  create: vi.fn().mockResolvedValue(undefined),
  update: vi.fn().mockResolvedValue(undefined),
  reorder: vi.fn().mockResolvedValue(undefined),
  deleteGroup: vi.fn().mockResolvedValue(undefined),
  add: vi.fn().mockResolvedValue(undefined),
  remove: vi.fn().mockResolvedValue(undefined),
}));

async function settleTabs(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 250));
}

vi.mock("@/composables/useWatchlists", () => ({
  useWatchlists: () => ({
    groups: ref([
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
    ]),
    loading: ref(false),
    error: computed(() => ""),
    saving: computed(() => false),
    refresh: vi.fn(),
    ...mocks,
  }),
}));

vi.mock("@/composables/useStocks", async (source) => {
  const actual = await source<typeof import("@/composables/useStocks")>();
  return {
    ...actual,
    useStockPicker: () => ({
      draftQuery: ref(""),
      items: computed(() => [
        {
          stock_id: "stock-1",
          market_code: "CN-S",
          venue_code: "XSHG",
          security_code: "600000",
          name: "浦发银行",
          listing_status: "ACTIVE",
        },
      ]),
      fetching: ref(false),
      error: computed(() => ""),
      search: vi.fn(),
    }),
  };
});

test("行情图提供不依赖图形的文本摘要", () => {
  setActivePinia(createPinia());
  const wrapper = mount(DailyQuoteChart, {
    props: {
      quotes: [
        {
          trade_date: "2026-08-07",
          open: "10.00",
          high: "10.30",
          low: "9.90",
          close: "10.20",
          pre_close: "10.00",
          change: "0.20",
          pct_chg: "2.00",
          vol: "1000",
          amount: "10000",
          updated_at: "2026-08-07T08:00:00Z",
        },
      ],
    },
    global: { stubs: { BaseChart: true } },
  });
  expect(wrapper.get("figcaption").text()).toContain("最新收盘 10.20");
  expect(wrapper.get("figcaption").text()).toContain("涨跌幅 2.00%");
});

test("自选页通过弹窗创建完整分组并添加成员", async () => {
  const wrapper = mount(WatchlistsView, {
    global: { stubs: { RouterLink: true, AppSurface: false } },
  });
  await wrapper.get('[aria-label="添加分组"]').trigger("click");
  await wrapper.get("#group-name").setValue("  短线机会  ");
  await wrapper.get("#group-notes").setValue("  事件驱动观察  ");
  await wrapper.get('[aria-label="新标签"]').setValue("催化剂");
  await wrapper
    .findAll("button")
    .find((button) => button.text() === "添加标签")!
    .trigger("click");
  await wrapper.get(".group-form").trigger("submit");
  await flushPromises();
  expect(mocks.create).toHaveBeenCalledWith({
    name: "短线机会",
    notes: "事件驱动观察",
    tags: ["催化剂"],
  });

  await wrapper.get("select").setValue("stock-1");
  await wrapper.get(".member-toolbar").trigger("submit");
  await flushPromises();
  expect(mocks.add).toHaveBeenCalledWith("group-1", "stock-1");
  await settleTabs();
  wrapper.unmount();
});

test("自选分组可通过键盘快捷键调整竖向顺序", async () => {
  const wrapper = mount(WatchlistsView, {
    global: { stubs: { RouterLink: true, AppSurface: false } },
  });
  await wrapper.get('[aria-label="下移长线观察"]').trigger("click");
  await flushPromises();
  expect(mocks.reorder).toHaveBeenCalledWith(["group-2", "group-1"]);
  await settleTabs();
  wrapper.unmount();
});
