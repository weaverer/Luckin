import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";

import { useStockPicker, type StockPage } from "@/composables/useStocks";

const { apiRequest } = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("@/api/client/http", () => ({ apiRequest }));

test("自选股票选择器自动读取全部服务端分页", async () => {
  apiRequest.mockImplementation(
    ({ params }: { params: { limit: number; offset: number } }) => {
      const total = 2500;
      const end = Math.min(params.offset + params.limit, total);
      const data: StockPage = {
        items: Array.from({ length: end - params.offset }, (_, index) => {
          const sequence = params.offset + index;
          return {
            stock_id: `stock-${sequence}`,
            market_code: "CN-S",
            venue_code: "XSHG",
            security_code: String(600000 + sequence),
            name: `股票${sequence}`,
            listing_status: "ACTIVE",
          };
        }),
        pagination: {
          limit: params.limit,
          offset: params.offset,
          total,
          has_more: end < total,
        },
      };
      return Promise.resolve(data);
    },
  );
  const Harness = defineComponent({
    setup() {
      return useStockPicker();
    },
    template: '<span data-testid="count">{{ items.length }}</span>',
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = mount(Harness, {
    global: { plugins: [[VueQueryPlugin, { queryClient }]] },
  });
  await flushPromises();

  expect(wrapper.get('[data-testid="count"]').text()).toBe("2500");
  expect(
    apiRequest.mock.calls.map(([request]) => request.params.offset),
  ).toEqual([0, 1000, 2000]);
});
