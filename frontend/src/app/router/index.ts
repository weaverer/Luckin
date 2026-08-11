import { createRouter, createWebHistory } from "vue-router";

import { useSessionStore } from "@/stores/session";

import { routes } from "./routes";

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
});

router.beforeEach((to) => {
  const session = useSessionStore();
  if (to.meta.requiresAuth && !session.authenticated) {
    return { name: "login", query: { redirect: to.fullPath } };
  }
  if (to.name === "login" && session.authenticated)
    return { name: "dashboard" };
  return true;
});

router.afterEach((to) => {
  document.title = `${String(to.meta.title ?? to.name ?? "工作台")} · Lucking`;
});
