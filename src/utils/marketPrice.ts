/**
 * 多市场参考价：存成一段 JSON 文本挂在 Product.market_prices 上。
 *
 * 只做展示与比价参考，不参与利润计算，所以不拆字段、不建表；
 * 市场集合由这里维护，后端只校验「是对象 + 每个市场有数值 price」。
 */

export type MarketCode = 'CN' | 'SG' | 'TB';

export interface MarketPriceEntry {
  price: number;
  cur?: string;
  /** 官网 / 官网自动 / 淘宝均价 / 手工 */
  src?: string;
  /** 采集日期 YYYY-MM-DD */
  at?: string;
  url?: string;
  /** 淘宝均价的样本条数 */
  n?: number;
  /** 最近一次自动重读的时间（ISO 8601）；手工条目没有这个字段 */
  checked_at?: string;
  /** 连续抓取失败次数，成功一次即清零 */
  miss?: number;
  /** 连续失败到阈值后打上的标记：价格是旧的，界面要弱化显示 */
  stale?: boolean;
  /** 最近一次失败原因，只用于排障，不展示给用户 */
  last_error?: string;
}

export type MarketPrices = Partial<Record<MarketCode, MarketPriceEntry>>;

export const MARKET_ORDER: MarketCode[] = ['CN', 'SG', 'TB'];

export const MARKET_META: Record<MarketCode, { label: string; short: string; symbol: string; cur: string }> = {
  CN: { label: '中国官网', short: 'CN', symbol: '¥', cur: 'CNY' },
  SG: { label: '新加坡官网', short: 'SG', symbol: 'S$', cur: 'SGD' },
  TB: { label: '淘宝均价', short: '淘', symbol: '¥', cur: 'CNY' },
};

/** 把「像价格的值」归一成数字；null / 空串 / 布尔 / NaN / 负数一律当没有数据。
 *
 * 为什么不用 `Number(x)` 直接判：`Number(null)` 和 `Number('')` 都是 0，
 * 于是「这个市场没填」会被渲染成「¥0」——一个看起来像真价格的假价格。
 * 后端写入时有校验，但 /api/migrate 是整行搬运、不过 Pydantic，
 * 桌面版导出的历史行可能带 null 或字符串，所以展示边界要自己守。
 */
function toPrice(raw: unknown): number | null {
  if (typeof raw === 'number') return Number.isFinite(raw) && raw >= 0 ? raw : null;
  if (typeof raw === 'string' && raw.trim() !== '') {
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }
  return null;
}

export function parseMarketPrices(raw?: string | null): MarketPrices {
  if (!raw) return {};
  try {
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};
    const out: MarketPrices = {};
    for (const code of MARKET_ORDER) {
      const item = (obj as Record<string, unknown>)[code];
      if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
      const price = toPrice((item as MarketPriceEntry).price);
      if (price !== null) out[code] = { ...(item as MarketPriceEntry), price };
    }
    return out;
  } catch {
    return {};
  }
}

export function serializeMarketPrices(prices: MarketPrices): string {
  const out: Record<string, MarketPriceEntry> = {};
  for (const code of MARKET_ORDER) {
    const item = prices[code];
    const price = item ? toPrice(item.price) : null;
    if (item && price !== null) {
      out[code] = { ...item, price };
    }
  }
  return Object.keys(out).length ? JSON.stringify(out) : '';
}

export function formatMarketPrice(code: MarketCode, entry: MarketPriceEntry): string {
  const value = Number(entry.price);
  const text = Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, '');
  return `${MARKET_META[code].symbol}${text}`;
}

export function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * 比价结论：只用同币种能直接比的两项，避免引入汇率假设。
 *  - 淘宝 / 中国官网：渠道折扣深度，越大说明淘宝越贵
 * （新加坡价是 SGD，跟人民币不能直接比，不在这里出结论，只在上面的明细里展示。）
 */
export function marketInsights(prices: MarketPrices): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  const cn = prices.CN?.price;
  const tb = prices.TB?.price;
  if (cn && tb && cn > 0) {
    const ratio = Math.round((tb / cn) * 100);
    out.push({ label: '淘宝 / 中国官网', value: `${ratio}%` });
  }
  return out;
}
