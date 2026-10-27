/** 采购货源：把「要补货了，去哪儿下单」这一步变成补货页里能直接点的东西。
 *
 * 为什么把规则抽出来而不是写在组件里：
 *  - 四个平台的搜索参数各不相同，都要 encodeURIComponent。散在 JSX 里写四遍，
 *    加一个平台就要翻四处，漏一个编码就会把带空格/中文的关键词拼成坏链接；
 *  - 「用哪个词去搜」本身是有规则的（中文品名优先、多分支商品只取第一行、
 *    都没有才退回英文标题），这条规则必须能被测试钉住，不能埋在渲染里。
 *
 * 这里只负责拼 URL 与校验，不发请求、不读凭据。
 */

export type SourcingPlatformKey = '1688' | 'taobao' | 'alibaba' | 'pinduoduo';

export interface SourcingPlatform {
  key: SourcingPlatformKey;
  label: string;
  /** 平台搜索页地址；keyword 由调用方保证非空 */
  search: (keyword: string) => string;
}

const enc = (value: string) => encodeURIComponent(value);

export const SOURCING_PLATFORMS: readonly SourcingPlatform[] = [
  {
    key: '1688',
    label: '1688',
    search: (k) => `https://s.1688.com/selloffer/offer_search.htm?keywords=${enc(k)}&charset=utf8`,
  },
  {
    key: 'taobao',
    label: '淘宝',
    search: (k) => `https://s.taobao.com/search?q=${enc(k)}`,
  },
  {
    key: 'alibaba',
    label: '阿里巴巴国际站',
    search: (k) => `https://www.alibaba.com/trade/search?SearchText=${enc(k)}`,
  },
  {
    key: 'pinduoduo',
    label: '拼多多',
    search: (k) => `https://mobile.yangkeduo.com/search_result.html?search_key=${enc(k)}`,
  },
];

export interface SourcingSource {
  chinese_product_name?: string | null;
  product_name?: string | null;
  sku?: string | null;
}

export type SourcingKeywordSource = 'chinese' | 'variant_only' | 'english' | 'sku' | 'none';

export interface SourcingKeyword {
  /** 用于搜索的词；`variant_only` / `none` 时为空串，界面据此不显示平台按钮 */
  keyword: string;
  /** 关键词是从哪来的——界面要按这个决定提示什么 */
  source: SourcingKeywordSource;
  /** 中文品名的行数；>1 说明每行是一个变体标签（棕色/卡其色/浅蓝色） */
  variantCount: number;
  /** 变体标签原文（仅 variant_only 时有值），提示里要列给用户看 */
  variants: string[];
}

/** 折行商品名只取第一行：`棕色\n卡其色\n浅蓝色` 整串丢给 1688 是搜不到东西的。 */
const lines = (value?: string | null): string[] =>
  (value ?? '').split(/\r?\n/).map((s) => s.trim()).filter(Boolean);

/** 连续空白折成一个空格：英文标题里常有 `(Please Select Size) - M` 这种，直接编码会得到一串 %20。 */
const tidy = (value: string) => value.replace(/\s+/g, ' ').trim();

export function sourcingKeyword(product: SourcingSource): SourcingKeyword {
  const chinese = lines(product.chinese_product_name);
  // 单行中文名才是"这个商品叫什么"。多行的是变体标签（棕色/卡其色/浅蓝色），
  // 拿"棕色"去 1688 搜会返回一堆不相干的绿植/服饰——宁可留空让用户填完整品名，
  // 也不要给一个看起来能用、实际一定搜错的词。
  if (chinese.length === 1) {
    return { keyword: tidy(chinese[0]), source: 'chinese', variantCount: 1, variants: [] };
  }
  if (chinese.length > 1) {
    return { keyword: '', source: 'variant_only', variantCount: chinese.length, variants: chinese };
  }
  const english = lines(product.product_name)[0];
  if (english) return { keyword: tidy(english), source: 'english', variantCount: 0, variants: [] };
  const sku = lines(product.sku)[0];
  if (sku) return { keyword: tidy(sku), source: 'sku', variantCount: 0, variants: [] };
  return { keyword: '', source: 'none', variantCount: 0, variants: [] };
}

/** 各平台搜索链接；关键词为空时返回空数组（宁可不显示，也不要给一串搜不出东西的按钮）。 */
export function sourcingLinks(keyword: string): { key: SourcingPlatformKey; label: string; url: string }[] {
  const value = tidy(keyword);
  if (!value) return [];
  return SOURCING_PLATFORMS.map((platform) => ({
    key: platform.key,
    label: platform.label,
    url: platform.search(value),
  }));
}

/** 采购链接只认 http/https。
 *
 * 这个值会被原样 `window.open`，也会出现在详情页的链接位上；`javascript:` / `data:`
 * 这类 scheme 在这里没有任何正当用途，直接判无效比"打开后再想办法"可靠。
 */
export function normalizePurchaseUrl(raw: string | null | undefined): string | null {
  const value = (raw ?? '').trim();
  if (!value) return null;
  try {
    // 不用正则先筛一遍：`new URL` 会把 `javascript:` / `data:` 也解析成功，
    // 所以真正的判据是解析后的 protocol，而不是字符串开头长什么样。
    const url = new URL(value);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    return url.toString();
  } catch {
    // 没有 scheme 的裸域名、`//host/path` 这类协议相对地址都在这里被拒。
    return null;
  }
}

const HOST_LABELS: [RegExp, string][] = [
  [/(^|\.)1688\.com$/i, '1688'],
  [/(^|\.)taobao\.com$/i, '淘宝'],
  [/(^|\.)tmall\.com$/i, '天猫'],
  [/(^|\.)alibaba\.com$/i, '阿里巴巴国际站'],
  [/(^|\.)yangkeduo\.com$/i, '拼多多'],
  [/(^|\.)jd\.com$/i, '京东'],
  [/(^|\.)aliexpress\.com$/i, '速卖通'],
];

/** 采购链接的平台名，认不出来就退回域名（总比写"外部链接"有用）。 */
export function purchaseHostLabel(raw: string | null | undefined): string {
  const value = normalizePurchaseUrl(raw);
  if (!value) return '';
  const host = new URL(value).hostname;
  for (const [pattern, label] of HOST_LABELS) {
    if (pattern.test(host)) return label;
  }
  return host.replace(/^www\./i, '');
}
