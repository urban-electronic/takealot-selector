/**
 * 多市场参考价：存成一段 JSON 文本挂在 Product.market_prices 上。
 *
 * 只做展示与比价参考，不参与利润计算，所以不拆字段、不建表；
 * 市场集合由这里维护，后端只校验「是对象 + 每个市场有数值 price」。
 */

import { todayLocal } from './datetime';

export type MarketCode = 'CN' | 'SG' | 'TB';

export interface MarketPriceEntry {
  price: number;
  cur?: string;
  /** 官网自动 / 天猫原价 / 天猫到手价 / 京东到手价 / 手工 */
  src?: string;
  /** 采集日期 YYYY-MM-DD */
  at?: string;
  url?: string;
  /** 到手价的样本条数（同款不同配色/店铺各算一条） */
  n?: number;
  /** 最近一次重读/录入的时间（ISO 8601）。自动同步与 `/batch` 都会写；
   *  详情页手工录入不写（那里记的是 `at` 业务日期）。 */
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

/**
 * 展示顺序，和上面的存储顺序不同：新加坡官网价打头。
 * 它是唯一由服务端每天自动重读的值，不需要人工维护；国内原价 / 到手价靠人工录入，
 * 只在采到时跟在后面当次要信息。
 */
export const DISPLAY_ORDER: MarketCode[] = ['SG', 'CN', 'TB'];

/**
 * 界面上该出现哪几个市场：只留采到的。
 * 采不到的直接不出现——留个「未录入」占位只会让人以为那格该去填，
 * 而淘宝/中国官网这两路本来就抓不到，填不填都改不了它。
 */
export function visibleMarkets(prices: MarketPrices): MarketCode[] {
  return DISPLAY_ORDER.filter((code) => prices[code]);
}

/**
 * 市场元信息。这里**不写货币符号**：符号跟着条目上的 `cur` 走（见 `symbolFor()`），
 * 市场只声明默认币种。两份符号表并存时，一份改了另一份不动就会出现
 * 「S$99」配「无汇率」——左边说这是新加坡元，右边说不认识这个币种。
 *
 * CN / TB 两路都指向「国内零售」：CN 是原价（吊牌/划线价），TB 是到手价（实付）。
 * 代码里的 CN/TB 只是历史键名——中国官网 charleskeith.cn 已停摆，实际数据来自
 * 天猫、京东的公开报价，所以标签必须写「国内」，不能写「官网」。
 */
export const MARKET_META: Record<MarketCode, { label: string; short: string; cur: string }> = {
  CN: { label: '国内原价', short: 'CN', cur: 'CNY' },
  SG: { label: '新加坡官网', short: 'SG', cur: 'SGD' },
  TB: { label: '国内到手价', short: '到手', cur: 'CNY' },
};

/** 把「像价格的值」归一成数字；null / 空串 / 布尔 / NaN / 负数 / 0 一律当没有数据。
 *
 * 为什么不用 `Number(x)` 直接判：`Number(null)` 和 `Number('')` 都是 0，
 * 于是「这个市场没填」会被渲染成「¥0」——一个看起来像真价格的假价格。
 * 后端写入时有校验，但 /api/migrate 是整行搬运、不过 Pydantic，
 * 桌面版导出的历史行可能带 null 或字符串，所以展示边界要自己守。
 *
 * 为什么 0 也算没有数据：参考价不可能是 0（没有商品白送），而系统里
 * 另外三处早就这么认了——`/api/market-prices/status` 用 `entry.get('price')`
 * 的真值判「填没填」，自动同步的 `_to_price()` 直接丢弃 `<= 0`，
 * 批量录入的 `buildMarketBatch()` 也把 `0` 判为无效。只有这里和详情页的
 * 输入框还收 0，于是同一个字段在列表里显示「¥0」「R0」、在覆盖率里算「未填」，
 * 批量面板还会因为它和草稿一致而清除不掉。
 */
function toPrice(raw: unknown): number | null {
  if (typeof raw === 'number') return Number.isFinite(raw) && raw > 0 ? raw : null;
  if (typeof raw === 'string' && raw.trim() !== '') {
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
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
  return `${symbolFor(code, entry)}${text}`;
}

/**
 * 价格前面的货币符号。
 *
 * 条目上写了 `cur` 就以它为准——`entryCurrency()` 已经这么认（`/api/migrate`
 * 整行搬运的历史数据可能带别的币种），价格列却一直按市场写死符号，于是同一行
 * 会出现「S$99」配「无汇率」：左边说这是新加坡元，右边说不认识这个币种。
 * 认不出的币种宁可把代码写出来（`USD 99`），也不要冒用市场的符号。
 */
export function symbolFor(code: MarketCode, entry: MarketPriceEntry): string {
  const cur = entryCurrency(code, entry);
  return CURRENCY_SYMBOL[cur] || `${cur} `;
}

/** 展示用符号表；只收本系统真会遇到的币种，其余按代码原样显示。 */
const CURRENCY_SYMBOL: Record<string, string> = {
  CNY: '¥',
  SGD: 'S$',
  ZAR: 'R',
  USD: 'US$',
  EUR: '€',
  MYR: 'RM',
};

/**
 * 手工改一个市场的参考价，返回新条目（不含「没有这个市场」的判断）。
 *
 * 两件事在这里统一，避免两个写入口径不一样：
 * 1. `at` 走 `todayLocal()`（Asia/Shanghai），与采购/库存/装箱同一份业务日期。
 *    原来这里有一份私有的 `today()`，取的是**浏览器本地时区**：非东八区的机器上
 *    早上录的价格会被记成前一天，而其它页面都按北京时间写。
 * 2. 清掉上一次自动同步留下的失败痕迹（`stale` / `miss` / `last_error`）。
 *    `/api/market-prices/batch` 一直是这么做的，详情页没有——于是新加坡价连续三次
 *    没读到（打上 `stale`）之后，用户手工把正确价格填进去，界面还挂着
 *    「· 已过期（最近一次没读到）」，看起来像这次录入没生效。
 */
export function manualPriceEntry(
  prev: MarketPriceEntry | undefined,
  code: MarketCode,
  price: number,
): MarketPriceEntry {
  const next: MarketPriceEntry = {
    ...(prev || {}),
    price,
    cur: MARKET_META[code].cur,
    src: prev?.src || '手工',
    at: todayLocal(),
  };
  delete next.stale;
  delete next.miss;
  delete next.last_error;
  return next;
}

/**
 * 「上次同步」的时间文案。
 *
 * 后端存的是 `datetime.now(timezone.utc).isoformat()`（形如
 * `2026-10-21T05:12:33+00:00`），直接塞进界面有两个问题：一是露出机器格式，
 * 二是比北京时间慢 8 小时——早上 13 点同步完，界面写 05:12，用户会以为今天没跑。
 * 这里统一按北京时间（UTC+8，与后端 `_today()` 的「今天」一致）显示，
 * 并且用 getUTC* 计算，不依赖浏览器所在时区。
 */
export function formatSyncTime(at: unknown): string {
  if (typeof at !== 'string' || at.trim() === '') return '尚未运行';
  const stamp = new Date(at);
  if (Number.isNaN(stamp.getTime())) return '时间未知';
  const beijing = new Date(stamp.getTime() + 8 * 3600 * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${beijing.getUTCFullYear()}-${pad(beijing.getUTCMonth() + 1)}-${pad(beijing.getUTCDate())} `
    + `${pad(beijing.getUTCHours())}:${pad(beijing.getUTCMinutes())}`;
}

/**
 * 比价结论：只用同币种能直接比的两项，避免引入汇率假设。
 *  - 到手价 / 原价：国内折扣深度，90% 表示实付是吊牌的九折
 * （新加坡价是 SGD，跟人民币不能直接比，不在这里出结论，只在上面的明细里展示。）
 */
export function marketInsights(prices: MarketPrices): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  const cn = prices.CN?.price;
  const tb = prices.TB?.price;
  if (cn && tb && cn > 0) {
    const ratio = Math.round((tb / cn) * 100);
    out.push({ label: '到手价 / 原价', value: `${ratio}%` });
  }
  return out;
}

// ---- 兰特折算 ---------------------------------------------------------------
//
// 参考价是三地本币（S$、¥），和 Takealot 的售价不同币种，摆在旁边没法直接比。
// 用户在 Takealot 卖货、定价用兰特，所以界面上统一折成 R 展示。

export type FxCurrency = 'SGD' | 'CNY';

export interface FxRates {
  base: string;
  /** 1 单位该货币值多少兰特 */
  rates: Partial<Record<FxCurrency, number>>;
  /** 汇率生效日期 YYYY-MM-DD（上游发布日，不是我们拉取的时刻） */
  at?: string | null;
  src?: string;
  fetched_at?: string | null;
  /** 缓存超过 24 小时且这次没拉到新的，用的是旧汇率 */
  stale?: boolean;
  /** 从没成功拉过，用的是代码里的兜底常量 */
  fallback?: boolean;
}

/**
 * 兜底汇率（2026-10-21 取自 open.er-api.com）。
 *
 * 只在后端从没成功拉过、又刚好拉不到时生效。界面必须能算出兰特——
 * 一列空白比一个「标了来源的旧汇率」更糟，因为前者看起来像「这款没有参考价」。
 */
export const FALLBACK_FX: FxRates = {
  base: 'ZAR',
  rates: { SGD: 12.8345, CNY: 2.4453 },
  src: '内置兜底',
  fallback: true,
};

export const ZAR_SYMBOL = 'R';

/** 某个价格条目用哪种货币。条目上写了 cur 就以它为准（迁移数据可能有别种货币）。 */
export function entryCurrency(code: MarketCode, entry: MarketPriceEntry): string {
  return (entry.cur || MARKET_META[code].cur).toUpperCase();
}

/** 折算成兰特；没有对应汇率时返回 null（调用方退回显示原币，不猜）。 */
export function toZar(amount: number, cur: string, fx: FxRates): number | null {
  const rate = fx.rates[cur as FxCurrency];
  if (!rate || !Number.isFinite(rate) || rate <= 0) return null;
  if (!Number.isFinite(amount)) return null;
  return amount * rate;
}

/**
 * 兰特金额文案：取整 + 千分位。
 *
 * 取整不保留小数：参考价本来就是「参考」，R1,282 和 R1,282.16 对决策没有差别，
 * 但后者会让方框变宽、且暗示精度比实际高。
 */
export function formatZar(value: number): string {
  const rounded = Math.round(value);
  const text = String(Math.abs(rounded)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${rounded < 0 ? '-' : ''}${ZAR_SYMBOL}${text}`;
}

/** 折算后的价格文案；没有汇率时返回空串，由调用方决定退回原币还是留空。 */
export function formatZarPrice(code: MarketCode, entry: MarketPriceEntry, fx: FxRates): string {
  const converted = toZar(entry.price, entryCurrency(code, entry), fx);
  return converted === null ? '' : formatZar(converted);
}

/** 汇率说明：`1 SGD=R12.83 · 1 CNY=R2.45 · 2026-10-21`，异常时带上标记。 */
export function fxCaption(fx: FxRates): string {
  const parts = (['SGD', 'CNY'] as FxCurrency[])
    .filter((cur) => fx.rates[cur])
    .map((cur) => `1 ${cur}=R${Number(fx.rates[cur]).toFixed(2)}`);
  if (fx.at) parts.push(fx.at);
  if (fx.fallback) parts.push('兜底汇率');
  else if (fx.stale) parts.push('汇率未更新');
  return parts.join(' · ');
}

// ---- 批量录入 ---------------------------------------------------------------

/** 批量录入面板能改的两个市场：新加坡官网价是服务端自动重读的，不在这里改。 */
export type ManualMarket = 'CN' | 'TB';

export interface MarketBatchItem {
  product_id: string;
  market: ManualMarket;
  /** `null` = 清空该市场 */
  price: number | null;
}

export interface MarketBatchSkipped {
  /** 产品标识（SKU 优先），用于提示里点名 */
  label: string;
  market: ManualMarket;
  /** 用户原样输入的内容 */
  value: string;
}

interface BatchRow {
  id: string;
  sku?: string | null;
  product_name?: string | null;
  market_prices?: string | null;
}

/**
 * 把面板里的输入整理成 `/api/market-prices/batch` 的 items，并**把无效行交回去**。
 *
 * 为什么不是「无效就跳过」：输入框是自由文本（`inputMode="decimal"`，不是
 * `type="number"`），敲进 `0`、`-5`、`abc` 都能留在框里。旧写法直接 `return`
 * 掉这些行，然后提示「已保存 N 条」或「没有需要保存的改动」——用户明明改过，
 * 界面却说没改，改的那一行既没保存也没有任何解释。
 *
 * 规则和展示层一致：空串 = 删除该市场（有旧值时才发 `price: null`），
 * 非空必须是有限正数。
 */
export function buildMarketBatch(
  rows: BatchRow[],
  draft: Record<string, { CN: string; TB: string }>,
): { items: MarketBatchItem[]; skipped: MarketBatchSkipped[] } {
  const items: MarketBatchItem[] = [];
  const skipped: MarketBatchSkipped[] = [];
  for (const row of rows) {
    const prices = parseMarketPrices(row.market_prices);
    const cells = draft[row.id] || { CN: '', TB: '' };
    const label = row.sku || row.product_name || row.id;
    for (const market of ['CN', 'TB'] as ManualMarket[]) {
      const before = prices[market] ? String(prices[market]!.price) : '';
      const now = (cells[market] || '').trim();
      if (now === before) continue;
      if (now === '') {
        if (before) items.push({ product_id: row.id, market, price: null });
        continue;
      }
      const value = Number(now);
      if (!Number.isFinite(value) || value <= 0) {
        skipped.push({ label, market, value: now });
        continue;
      }
      items.push({ product_id: row.id, market, price: value });
    }
  }
  return { items, skipped };
}

/** 无效行的提示文案：`SKU-A 国内原价、SKU-B 国内到手价 等 5 行`。 */
export function describeSkipped(skipped: MarketBatchSkipped[], limit = 3): string {
  if (!skipped.length) return '';
  const named = skipped.slice(0, limit).map((row) => `${row.label} ${MARKET_META[row.market].label}`);
  const rest = skipped.length > limit ? ` 等 ${skipped.length} 行` : '';
  return `${named.join('、')}${rest}`;
}
