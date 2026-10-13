// Adapted from PricePilot native source 2026-10-12; see docs/pricepilot-integration.md.
export const MS_PER_DAY = 86_400_000;

export interface ReplenishConfig {

  seaLeadTimeDays: number;

  airLeadTimeDays: number;

  safetyStockDays: number;

  windowDays: number;

  recentWindowDays: number;

  burstCapRatio: number;

  urgentCoverDays: number;

  watchCoverDays: number;

  netMarginRate: number;
}

export const DEFAULT_REPLENISH_CONFIG: ReplenishConfig = {
  seaLeadTimeDays: 45,
  airLeadTimeDays: 15,
  safetyStockDays: 14,
  windowDays: 30,
  recentWindowDays: 7,
  burstCapRatio: 3,
  urgentCoverDays: 15,
  watchCoverDays: 30,
  netMarginRate: 0.25
};

export type ReplenishLevel =
  | "out_of_stock" // 已断货且有销速
  | "urgent" // 覆盖 ≤ urgentCoverDays
  | "watch" // 覆盖 ≤ watchCoverDays
  | "healthy" // 覆盖充足
  | "slow_moving" // 有库存但窗口内零销量
  | "no_data"; // 无销量且无库存，无法判断

export type ReplenishTrend = "rising" | "falling" | "flat" | "none";

export interface SalesPoint {
  orderDate: Date;
  quantity: number;
  sellingPrice: number;
}

export interface ReplenishInput {
  sku: string;
  name: string;
  url?: string | null;

  price: number;
  minPrice?: number | null;
  stockTotal: number;
  stockByDc?: Record<string, number> | null;
  onWay: number;
  status?: string | null;

  platformCoverDays?: number | null;

  sales: SalesPoint[];
  now: Date;
  config?: Partial<ReplenishConfig>;
}

export interface ReplenishResult {
  sku: string;
  name: string;
  url: string | null;
  price: number;
  minPrice: number | null;
  stockTotal: number;
  stockByDc: Record<string, number>;
  onWay: number;
  status: string | null;


  unitsWindow: number;

  unitsRecent: number;

  spanDays: number | null;

  daysSinceLastSale: number | null;


  v30: number;

  v7: number;

  vspan: number;

  vUsed: number;


  coverDays: number | null;

  platformCoverDays: number | null;

  level: ReplenishLevel;
  trend: ReplenishTrend;


  suggestedSea: number;

  suggestedAir: number;


  dailyLossZar: number;

  dailyLossNetZar: number;

  revenueWindow: number;


  dcEmpty: string[];

  reasons: string[];
}

function sumWithin(sales: SalesPoint[], from: Date, to: Date): { units: number; revenue: number; dates: Date[] } {
  let units = 0;
  let revenue = 0;
  const dates: Date[] = [];
  for (const s of sales) {
    const t = s.orderDate.getTime();
    if (t >= from.getTime() && t <= to.getTime()) {
      units += s.quantity;
      revenue += s.quantity * s.sellingPrice;
      dates.push(s.orderDate);
    }
  }
  return { units, revenue, dates };
}

function round(n: number, digits = 3): number {
  const f = Math.pow(10, digits);
  return Math.round(n * f) / f;
}

export function computeReplenish(input: ReplenishInput): ReplenishResult {
  const cfg: ReplenishConfig = { ...DEFAULT_REPLENISH_CONFIG, ...(input.config ?? {}) };
  const now = input.now;

  const windowStart = new Date(now.getTime() - cfg.windowDays * MS_PER_DAY);
  const recentStart = new Date(now.getTime() - cfg.recentWindowDays * MS_PER_DAY);

  const win = sumWithin(input.sales, windowStart, now);
  const rec = sumWithin(input.sales, recentStart, now);

  const unitsWindow = win.units;
  const unitsRecent = rec.units;

  const v30 = unitsWindow / cfg.windowDays;
  const v7 = unitsRecent / cfg.recentWindowDays;

  let spanDays: number | null = null;
  let vspan = 0;
  if (win.dates.length > 0) {
    const times = win.dates.map((d) => d.getTime());
    const first = Math.min(...times);
    const last = Math.max(...times);
    spanDays = Math.max(1, Math.round((last - first) / MS_PER_DAY) + 1);
    vspan = unitsWindow / spanDays;
  }

  const vUsed = v30 > 0 ? Math.max(v30, Math.min(v7, cfg.burstCapRatio * v30)) : 0;

  const stockTotal = Math.max(0, input.stockTotal);
  const onWay = Math.max(0, input.onWay);
  const coverDays = vUsed > 0 ? stockTotal / vUsed : null;

  let daysSinceLastSale: number | null = null;
  if (input.sales.length > 0) {
    const latest = Math.max(...input.sales.map((s) => s.orderDate.getTime()));
    daysSinceLastSale = Math.floor((now.getTime() - latest) / MS_PER_DAY);
  }

  let trend: ReplenishTrend = "none";
  if (unitsWindow > 0) {
    if (v7 > 1.5 * v30) trend = "rising";
    else if (v7 < 0.5 * v30) trend = "falling";
    else trend = "flat";
  }

  let level: ReplenishLevel;
  if (unitsWindow === 0 && stockTotal > 0) level = "slow_moving";
  else if (unitsWindow === 0) level = "no_data";
  else if (stockTotal <= 0) level = "out_of_stock";
  else if (coverDays !== null && coverDays <= cfg.urgentCoverDays) level = "urgent";
  else if (coverDays !== null && coverDays <= cfg.watchCoverDays) level = "watch";
  else level = "healthy";

  const need = (leadTime: number) => {
    if (vUsed <= 0) return 0;
    if (level === "slow_moving" || level === "no_data") return 0;
    const target = vUsed * (leadTime + cfg.safetyStockDays);
    return Math.max(0, Math.ceil(target - stockTotal - onWay));
  };
  const suggestedSea = need(cfg.seaLeadTimeDays);
  const suggestedAir = need(cfg.airLeadTimeDays);

  const dailyLossZar = level === "out_of_stock" ? vUsed * input.price : 0;

  const stockByDc = input.stockByDc ?? {};
  const DC_IMBALANCE_MIN_STOCK = 5;
  const dcEmpty =
    stockTotal >= DC_IMBALANCE_MIN_STOCK
      ? Object.entries(stockByDc)
          .filter(([, q]) => q <= 0)
          .map(([dc]) => dc)
      : [];

  const reasons: string[] = [];
  if (level === "out_of_stock") {
    reasons.push(`已断货，近 ${cfg.windowDays} 天卖出 ${unitsWindow} 件`);
  } else if (level === "slow_moving") {
    reasons.push(`近 ${cfg.windowDays} 天零出单，库存 ${stockTotal} 件滞压`);
  } else if (level === "no_data") {
    reasons.push(`无库存且近 ${cfg.windowDays} 天无销量，无需动作`);
  } else if (coverDays !== null) {
    reasons.push(`可售约 ${round(coverDays, 1)} 天（按日均 ${round(vUsed)} 件）`);
  }
  if (trend === "rising") reasons.push("销速上升中，建议提前下单");
  if (trend === "falling") reasons.push("销速下降中，建议下浮补货量");
  if (onWay > 0) reasons.push(`已有在途 ${onWay} 件`);
  if (dcEmpty.length > 0) reasons.push(`分仓 ${dcEmpty.join("/")} 已空，存在调拨或分拆运费`);

  return {
    sku: input.sku,
    name: input.name,
    url: input.url ?? null,
    price: input.price,
    minPrice: input.minPrice ?? null,
    stockTotal,
    stockByDc,
    onWay,
    status: input.status ?? null,

    unitsWindow,
    unitsRecent,
    spanDays,
    daysSinceLastSale,

    v30: round(v30),
    v7: round(v7),
    vspan: round(vspan),
    vUsed: round(vUsed),

    coverDays: coverDays !== null ? round(coverDays, 1) : null,
    platformCoverDays: input.platformCoverDays ?? null,

    level,
    trend,

    suggestedSea,
    suggestedAir,

    dailyLossZar: round(dailyLossZar, 2),
    dailyLossNetZar: round(dailyLossZar * cfg.netMarginRate, 2),
    revenueWindow: round(win.revenue, 2),

    dcEmpty,
    reasons
  };
}

const LEVEL_ORDER: Record<ReplenishLevel, number> = {
  out_of_stock: 0,
  urgent: 1,
  watch: 2,
  healthy: 3,
  slow_moving: 4,
  no_data: 5
};

export function computeReplenishBatch(inputs: ReplenishInput[]): ReplenishResult[] {
  return inputs
    .map(computeReplenish)
    .sort((a, b) => {
      if (b.dailyLossZar !== a.dailyLossZar) return b.dailyLossZar - a.dailyLossZar;
      const la = LEVEL_ORDER[a.level];
      const lb = LEVEL_ORDER[b.level];
      if (la !== lb) return la - lb;
      const ca = a.coverDays ?? Number.POSITIVE_INFINITY;
      const cb = b.coverDays ?? Number.POSITIVE_INFINITY;
      return ca - cb;
    });
}

export interface ReplenishSummary {
  total: number;
  outOfStock: number;
  urgent: number;
  watch: number;
  healthy: number;
  slowMoving: number;
  noData: number;

  dailyLossZar: number;

  dailyLossNetZar: number;

  monthlyLossNetZar: number;

  withOnWay: number;

  dcImbalanced: number;
}

export function summarize(results: ReplenishResult[]): ReplenishSummary {
  const count = (l: ReplenishLevel) => results.filter((r) => r.level === l).length;
  const dailyLossZar = results.reduce((a, r) => a + r.dailyLossZar, 0);
  const dailyLossNetZar = results.reduce((a, r) => a + r.dailyLossNetZar, 0);
  return {
    total: results.length,
    outOfStock: count("out_of_stock"),
    urgent: count("urgent"),
    watch: count("watch"),
    healthy: count("healthy"),
    slowMoving: count("slow_moving"),
    noData: count("no_data"),
    dailyLossZar: round(dailyLossZar, 2),
    dailyLossNetZar: round(dailyLossNetZar, 2),
    monthlyLossNetZar: round(dailyLossNetZar * 30, 2),
    withOnWay: results.filter((r) => r.onWay > 0).length,
    dcImbalanced: results.filter((r) => r.dcEmpty.length > 0).length
  };
}
