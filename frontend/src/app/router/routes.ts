import type { RouteRecordRaw } from "vue-router";

import AppLayout from "@/app/layouts/AppLayout.vue";

export const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/views/LoginView.vue"),
    meta: { title: "登录", public: true },
  },
  {
    path: "/",
    component: AppLayout,
    meta: { requiresAuth: true },
    children: [
      {
        path: "",
        name: "dashboard",
        component: () => import("@/views/DashboardView.vue"),
      },
      {
        path: "calendar",
        name: "calendar",
        component: () => import("@/views/CalendarView.vue"),
      },
      {
        path: "stocks",
        name: "stocks",
        component: () => import("@/views/StocksView.vue"),
      },
      {
        path: "stocks/:stockId",
        name: "stock-detail",
        component: () => import("@/views/StockDetailView.vue"),
      },
      {
        path: "watchlists",
        name: "watchlists",
        component: () => import("@/views/WatchlistsView.vue"),
      },
      {
        path: "broker-recommendations",
        name: "broker-recommendations",
        component: () => import("@/views/BrokerRecommendationsView.vue"),
      },
      {
        path: "tasks",
        name: "tasks",
        component: () => import("@/views/TaskStatusView.vue"),
      },
      {
        path: "account",
        name: "account",
        component: () => import("@/views/AccountView.vue"),
      },
    ],
  },
];
