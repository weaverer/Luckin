export interface NavigationItem {
  routeName: string;
  label: string;
  description: string;
  icon: string;
  delivered: boolean;
}

export const navigationItems: readonly NavigationItem[] = [
  {
    routeName: "calendar",
    label: "日历",
    description: "交易日与个人重要日",
    icon: "pi-calendar",
    delivered: true,
  },
  {
    routeName: "stocks",
    label: "股票与行情",
    description: "股票搜索、详情与日线行情",
    icon: "pi-chart-line",
    delivered: true,
  },
  {
    routeName: "watchlists",
    label: "自选分组",
    description: "个人股票分组与成员",
    icon: "pi-bookmark",
    delivered: true,
  },
  {
    routeName: "broker-recommendations",
    label: "J金股",
    description: "券商金股研究驾驶舱",
    icon: "pi-sparkles",
    delivered: true,
  },
  {
    routeName: "tasks",
    label: "任务执行",
    description: "同步状态、快照与通知",
    icon: "pi-list-check",
    delivered: true,
  },
  {
    routeName: "account",
    label: "账号设置",
    description: "修改密码与安全退出",
    icon: "pi-user",
    delivered: true,
  },
] as const;
