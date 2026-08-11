import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createPinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { vi } from "vitest";

import { WorkbenchApiError } from "@/api/client/errors";
import ImportantDateDialog from "@/components/calendar/ImportantDateDialog.vue";
import CalendarView from "@/views/CalendarView.vue";

const { apiRequest } = vi.hoisted(() => ({ apiRequest: vi.fn() }));
vi.mock("@/api/client/http", () => ({ apiRequest }));

function mountCalendar() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return mount(CalendarView, {
    global: { plugins: [createPinia(), [VueQueryPlugin, { queryClient }]] },
  });
}

describe("calendar workspace", () => {
  beforeEach(() => apiRequest.mockReset());

  it("renders open, closed and unknown textual market states", async () => {
    apiRequest.mockResolvedValue([
      {
        date: "2026-08-03",
        market_status: "OPEN",
        important_dates: [
          {
            important_date_id: "event-1",
            event_date: "2026-08-03",
            title: "业绩预告",
            notes: null,
          },
          {
            important_date_id: "event-2",
            event_date: "2026-08-03",
            title: "股东大会",
            notes: null,
          },
        ],
        calendar_updated_at: null,
      },
      {
        date: "2026-08-04",
        market_status: "CLOSED",
        important_dates: [],
        calendar_updated_at: null,
      },
      {
        date: "2026-08-05",
        market_status: "UNKNOWN",
        important_dates: [],
        calendar_updated_at: null,
      },
    ]);
    const wrapper = mountCalendar();
    await flushPromises();
    expect(wrapper.text()).toContain("交易日");
    expect(wrapper.text()).toContain("非交易日");
    expect(wrapper.text()).toContain("待确认");
    expect(wrapper.text()).toContain("业绩预告");
    expect(wrapper.text()).toContain("股东大会");
  });

  it("shows a real empty state when the API has no calendar rows", async () => {
    apiRequest.mockResolvedValue([]);
    const wrapper = mountCalendar();
    await flushPromises();
    expect(wrapper.text()).toContain("暂无数据");
  });

  it("validates important-date fields before sending", async () => {
    const wrapper = mount(ImportantDateDialog, { props: { initialDate: "" } });
    await wrapper.get("form").trigger("submit");

    expect(wrapper.text()).toContain("请选择日期");
    expect(wrapper.text()).toContain("请输入标题");
    expect(wrapper.emitted("save")).toBeUndefined();
  });

  it("opens the important-date dialog with the clicked calendar date", async () => {
    apiRequest.mockResolvedValue([
      {
        date: "2026-08-03",
        market_status: "OPEN",
        important_dates: [],
        calendar_updated_at: null,
      },
    ]);
    const wrapper = mountCalendar();
    await flushPromises();
    await wrapper.get('[data-date="2026-08-03"]').trigger("click");

    expect(wrapper.get("#important-date-date").attributes("value")).toBe(
      "2026-08-03",
    );
  });

  it("edits an important date and keeps the refreshed API value", async () => {
    const original = {
      date: "2026-08-08",
      market_status: "OPEN",
      calendar_updated_at: null,
      important_dates: [
        {
          important_date_id: "event-1",
          event_date: "2026-08-08",
          title: "财报发布",
          notes: null,
        },
      ],
    };
    apiRequest
      .mockResolvedValueOnce([original])
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce([
        {
          ...original,
          important_dates: [
            { ...original.important_dates[0], title: "财报发布（更新）" },
          ],
        },
      ]);
    const wrapper = mountCalendar();
    await flushPromises();
    await wrapper.get('[aria-label="编辑重要日"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    await dialog.get('input:not([type="date"])').setValue("财报发布（更新）");
    await dialog.get("form").trigger("submit");
    await flushPromises();

    expect(apiRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "PUT",
        url: "/important-dates/event-1",
      }),
    );
    expect(wrapper.text()).toContain("财报发布（更新）");
  });

  it("maps duplicate conflict and server field errors into the dialog", async () => {
    apiRequest.mockResolvedValueOnce([
      {
        date: "2026-08-08",
        market_status: "OPEN",
        calendar_updated_at: null,
        important_dates: [],
      },
    ]);
    apiRequest.mockRejectedValueOnce(
      new WorkbenchApiError(409, {
        code: 400001,
        message: "重要日冲突",
        data: null,
        errors: [{ field: "title", code: "conflict", message: "标题已存在" }],
        request_id: "component-calendar",
        timestamp: "2026-08-08T12:00:00Z",
      }),
    );
    const wrapper = mountCalendar();
    await flushPromises();
    await wrapper.get('[aria-label="添加重要日"]').trigger("click");
    const dialog = wrapper.get('[role="dialog"]');
    await dialog.get("#important-date-date").setValue("2026-08-08");
    await dialog
      .get('input:not([id="important-date-date"])')
      .setValue("财报发布");
    await dialog.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("同一天已存在相同标题的重要日");
    expect(wrapper.text()).toContain("标题已存在");
  });
});
