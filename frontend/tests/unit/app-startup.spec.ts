import { mount } from "@vue/test-utils";

import App from "@/App.vue";

describe("application startup", () => {
  it("provides the router outlet", () => {
    const wrapper = mount(App, { global: { stubs: ["RouterView"] } });
    expect(wrapper.findComponent({ name: "RouterView" }).exists()).toBe(true);
  });
});
