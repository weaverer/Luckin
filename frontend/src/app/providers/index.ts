import {
  VueQueryPlugin,
  type VueQueryPluginOptions,
} from "@tanstack/vue-query";
import type { App } from "vue";
import { createPinia } from "pinia";
import PrimeVue from "primevue/config";

import { luckingTheme } from "@/styles/theme";

const queryOptions: VueQueryPluginOptions = {
  queryClientConfig: {
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  },
};

export function installAppProviders(app: App): void {
  app.use(createPinia());
  app.use(PrimeVue, {
    locale: {
      firstDayOfWeek: 1,
      dayNames: [
        "星期日",
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
      ],
      dayNamesShort: ["周日", "周一", "周二", "周三", "周四", "周五", "周六"],
      dayNamesMin: ["日", "一", "二", "三", "四", "五", "六"],
      monthNames: [
        "一月",
        "二月",
        "三月",
        "四月",
        "五月",
        "六月",
        "七月",
        "八月",
        "九月",
        "十月",
        "十一月",
        "十二月",
      ],
      monthNamesShort: [
        "1月",
        "2月",
        "3月",
        "4月",
        "5月",
        "6月",
        "7月",
        "8月",
        "9月",
        "10月",
        "11月",
        "12月",
      ],
      today: "今天",
      clear: "清除",
      chooseDate: "选择日期",
      chooseMonth: "选择月份",
      chooseYear: "选择年份",
      prevMonth: "上个月",
      nextMonth: "下个月",
      prevYear: "上一年",
      nextYear: "下一年",
      dateFormat: "yy-mm-dd",
      weekHeader: "周",
      emptyMessage: "暂无数据",
      emptySearchMessage: "没有匹配结果",
      searchMessage: "{0} 个结果可用",
      selectionMessage: "已选择 {0} 项",
      emptySelectionMessage: "未选择项目",
    },
    theme: {
      preset: luckingTheme,
      options: {
        darkModeSelector: ".app-dark",
      },
    },
  });
  app.use(VueQueryPlugin, queryOptions);
}
