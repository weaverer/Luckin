import { createPinia, setActivePinia } from "pinia";

import { useSessionStore } from "@/stores/session";

describe("session store", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("stores a restored session without persisting credentials", () => {
    const store = useSessionStore();
    store.establish(
      { userId: "user-1", username: "analyst", displayName: "分析员" },
      "csrf-token",
      "2026-08-08T12:00:00Z",
    );

    expect(store.authenticated).toBe(true);
    expect(store.user?.username).toBe("analyst");
    expect(store.csrfToken).toBe("csrf-token");
    expect(localStorage.getItem("session-token")).toBeNull();
  });

  it("clears all client session state after logout or password change", () => {
    const store = useSessionStore();
    store.establish(
      { userId: "user-1", username: "analyst", displayName: "分析员" },
      "csrf-token",
      "2026-08-08T12:00:00Z",
    );

    store.clear();

    expect(store.authenticated).toBe(false);
    expect(store.user).toBeNull();
    expect(store.csrfToken).toBeNull();
    expect(store.initialized).toBe(true);
  });
});
