import { mount } from "@vue/test-utils";

import AsyncState from "@/components/common/AsyncState.vue";
import FeaturePendingState from "@/components/common/FeaturePendingState.vue";

describe("shared asynchronous states", () => {
  it.each([
    ["loading", "正在加载"],
    ["empty", "暂无数据"],
    ["error", "加载失败"],
    ["stale", "数据可能已过期"],
  ] as const)("renders %s with a textual label", (state, label) => {
    const wrapper = mount(AsyncState, { props: { state } });
    expect(wrapper.text()).toContain(label);
  });

  it("emits refresh only through explicit user action", async () => {
    const wrapper = mount(AsyncState, {
      props: { state: "error", refreshable: true },
    });
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("refresh")).toHaveLength(1);
  });

  it("marks unfinished features without presenting fake empty data", () => {
    const wrapper = mount(FeaturePendingState, {
      props: { title: "尚未交付", description: "等待真实 API" },
    });
    expect(wrapper.text()).toContain("尚未交付");
    expect(wrapper.text()).toContain("不会使用静态业务假数据");
  });
});
