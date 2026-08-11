export const venueLabels: Record<string, string> = {
  XSHG: "上海证券交易所",
  XSHE: "深圳证券交易所",
  XBSE: "北京证券交易所",
};

export const listingStatusLabels: Record<string, string> = {
  ACTIVE: "上市",
  SUSPENDED: "暂停上市",
  DELISTED: "已退市",
  PENDING: "待上市",
};

export const marketDataStatusLabels = {
  CURRENT: "行情已更新",
  STALE: "行情待更新",
  MISSING: "暂无行情",
} as const;
