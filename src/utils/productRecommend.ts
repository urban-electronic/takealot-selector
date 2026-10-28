/**
 * 产品推荐：同类商品 + 互补配件（本地数据源版本）。
 *
 * 线上走 `GET /api/products/{id}/recommendations`，实现在
 * `backend/services/product_recommendation.py`。本地（Tauri）没有这个接口，
 * 就用同一套口径在浏览器里算一遍——否则桌面端点开详情页会看到一片空白。
 *
 * **两份实现的常量必须逐字一致**（权重、阈值、上限、停用词、配件词、组合表）：
 * `scripts/test-product-recommendation.cjs` 会把两边的常量都读出来比对，
 * 只改一边会直接让门禁红掉。逻辑改动的顺序也请保持一致，方便对着看。
 *
 * 只读，不写任何数据。
 */

import type {
  Product,
  ProcurementRecord,
  RecommendationItem,
  RecommendationMeta,
  RecommendationResult,
} from '../types';

// ---- 权重（改这里就能整体调平，Python 侧同名常量必须同步）----
export const WEIGHTS = { category: 0.35, attribute: 0.30, complement: 0.25, behavior: 0.10 };

export const DEFAULT_LIMITS = { similar: 6, complement: 4 };
export const MAX_LIMIT = 12;

/** 低于这个分数不进推荐：宁可不给，也不要用「都差不多」凑数。 */
export const MIN_SCORE = 0.18;

/**
 * 英文停用词 + 电商标题里的无效词。只做轻量处理，不引第三方分词。
 * compatible / dual / max / plus / mini / pro 这类修饰词放在这里：它们会让
 * 「标题重合 6 个品类词」这种结论虚高，还会把不相干的东西凑成「同类目配件」
 * （线上真机验证里「Dual Wireless Microphones」就是这么被推给无人机的）。
 */
export const STOPWORDS = new Set(
  `a an and are as at be by for from has have in is it its of on or that the to was
with without your you our we this these those new hot sale free shipping gift
set pack pcs piece pieces item items quality high premium original genuine
inch inches cm mm size large small medium mini big
compatible dual max plus mini pro multipack`
    .split(/\s+/)
    .filter(Boolean),
);

/** 配件/周边词。出现在候选标题里，说明它可能是「配着买」的东西。 */
export const ACCESSORY_TERMS = new Set(
  `case cover sleeve pouch bag box holder stand mount bracket clip strap belt
cable cord adapter charger charging battery batteries powerbank filter filters
brush brushes comb cap net nets grip band bands wax oil serum spray lotion
shampoo conditioner cream gel foam pin pins clip clips ring rings hanger
replacement spare part parts kit tool tools pad pads mat mats liner liners
protector guard screen protector lens lenses cleaner cleaning organizer storage
rack shelf hook hanger connector plug socket switch remote controller`
    .split(/\s+/)
    .filter(Boolean),
);

/** 静态组合表：命中即视为强互补（左右两边都会匹配）。 */
export const COMPLEMENT_RULES: Record<string, string[]> = {
  wig: ['wig cap', 'wig net', 'wig grip', 'wig stand', 'wig brush', 'hair wax',
    'edge control', 'wig band', 'hair oil', 'hair serum', 'wig hanger',
    '发网', '发套', '假发支架', '假发护理', '发蜡'],
  hair: ['wig cap', 'hair clip', 'hair band', 'hair oil', 'hair serum', '发网', '发夹', '发圈'],
  drone: ['drone battery', 'propeller', 'propeller guard', 'carrying case',
    'landing gear', 'remote controller', '无人机电池', '桨叶'],
  camera: ['camera case', 'camera mount', 'memory card', 'tripod', 'lens',
    'camera bag', 'sd card', '相机包', '三脚架'],
  charger: ['charging cable', 'usb cable', 'power bank', 'charging pad',
    'wall adapter', '充电线', '充电头', '移动电源'],
  vacuum: ['vacuum filter', 'vacuum bag', 'vacuum brush', 'replacement filter',
    'hepa filter', '吸尘器滤网', '尘袋'],
  phone: ['phone case', 'screen protector', 'phone holder', 'charging cable',
    '手机壳', '钢化膜', '手机支架'],
  watch: ['watch band', 'watch strap', 'watch case', '表带', '表壳'],
  lamp: ['light bulb', 'led bulb', 'lamp shade', '灯泡', '灯罩'],
  tent: ['tent pole', 'ground sheet', 'tent peg', '帐篷杆', '地布'],
  mask: ['mask strap', 'mask filter', '口罩绳', '口罩滤芯'],
  trimmer: ['trimmer blade', 'trimmer head', 'replacement blade', '刀头'],
  telescope: ['telescope tripod', 'telescope mount', '望远镜支架', '三脚架'],
};

/**
 * 「同一个词，不是同一个东西」。候选标题里出现这些词时，即使与锚点共享 hair / mat
 * 这类泛词，也不是它的配套——假发和「宠物毛清理手套」只共享一个 hair，
 * 摆在一起就是噪音，用户会照着买。
 *
 * 只放实测踩到或一眼能说清的：宠物毛清理手套被推到假发配套里，是 2026-10-28
 * 线上真机验证抓到的。新增条目请附上触发它的真实标题。
 */
export const SUBJECT_CONFLICTS: Record<string, string[]> = {
  hair: ['pet', 'dog', 'cat', 'lint', 'carpet', 'floor'],
  cover: ['car', 'sofa', 'pillow', 'book', 'phone'],
  mat: ['floor', 'yoga', 'door', 'car'],
  brush: ['paint', 'toilet', 'shoe'],
};

const wordsOf = (text: string | null | undefined): string[] =>
  (text || '').toLowerCase().match(/[a-z0-9]+/g) || [];

/**
 * 整词匹配短语：`hair clip` 不能命中 `hair clipper`。
 *
 * 原来是 `name.includes(keyword)`，于是「Hair Clipper」被当成 hair clip 的配件——
 * 线上真机验证第一屏就抓到了。规则表里的词都是完整词组，按词对齐才安全；
 * 最后一个词允许复数（bag / bags），前面必须逐词相同。中文没有分词，直接子串判断。
 */
export function containsPhrase(name: string | null | undefined, keyword: string): boolean {
  if (!keyword) return false;
  const lower = (name || '').toLowerCase();
  // 非 ASCII（中文）直接子串
  if (!/^[\x20-\x7e]*$/.test(keyword)) return lower.includes(keyword);
  const words = wordsOf(lower);
  const target = wordsOf(keyword);
  if (!target.length) return false;
  for (let i = 0; i + target.length <= words.length; i += 1) {
    let matched = true;
    for (let j = 0; j < target.length; j += 1) {
      const want = target[j];
      const got = words[i + j];
      if (j === target.length - 1) {
        if (got !== want && got !== `${want}s` && got !== `${want}es`) { matched = false; break; }
      } else if (got !== want) {
        matched = false;
        break;
      }
    }
    if (matched) return true;
  }
  return false;
}

/** 共享的品类词是泛词，而候选讲的是另一件事（宠物毛/车用/地板）。 */
function subjectConflict(sharedTerms: Iterable<string>, name: string): boolean {
  const words = new Set(wordsOf(name));
  for (const term of sharedTerms) {
    for (const conflict of SUBJECT_CONFLICTS[term] || []) {
      if (words.has(conflict)) return true;
    }
  }
  return false;
}

/** 单词是不是配件词（认简单复数：pad / pads）。 */
function isAccessoryWord(word: string): boolean {
  return ACCESSORY_TERMS.has(word)
    || (word.endsWith('s') && ACCESSORY_TERMS.has(word.slice(0, -1)))
    || (word.endsWith('es') && ACCESSORY_TERMS.has(word.slice(0, -2)));
}

/** 单词是不是命中了给定的品类词（同样认简单复数）。 */
function matchesAny(word: string, terms: Iterable<string>): boolean {
  const set = terms instanceof Set ? terms : new Set(terms);
  if (set.has(word)) return true;
  if (word.endsWith('es') && set.has(word.slice(0, -2))) return true;
  return word.endsWith('s') && set.has(word.slice(0, -1));
}

/**
 * 配件词必须**紧挨着**品类词出现（wig cap / vacuum filter / pet pad）。
 *
 * 只看「有没有共同的词」会把不相干的东西凑成配套：线上真机验证里
 * 「Dual Wireless Microphones」被推给无人机，理由写着「同类目配件（dual）」——
 * 它和锚点只共享一个修饰词，配件词（pad）跟品类词根本不在一处。
 *
 * 多个候选时取后面最远的那个（+2 → +1 → -1 → -2）：英文标题里真正的中心词
 * 一般在修饰语后面，"vacuum cleaner filter" 的配件是 filter，不是 cleaner。
 */
export function genericCompanion(
  name: string | null | undefined,
  sharedTerms: Iterable<string>,
): { paired: boolean; reason: string } {
  const terms = new Set(sharedTerms);
  const words = wordsOf(name);
  if (!words.length || !terms.size) return { paired: false, reason: '' };
  for (let i = 0; i < words.length; i += 1) {
    if (!matchesAny(words[i], terms)) continue;
    for (const offset of [2, 1, -1, -2]) {
      const near = i + offset;
      if (near >= 0 && near < words.length && isAccessoryWord(words[near])) {
        return { paired: true, reason: `同类目配件（${words[i]} + ${words[near]}）` };
      }
    }
  }
  return { paired: false, reason: '' };
}

/** 标题归一化：小写、标点转空格、压缩空白。用于去重比较。 */
export function normalizeTitle(text: string | null | undefined): string {
  return (text || '')
    .toLowerCase()
    .replace(/[^0-9a-z\u4e00-\u9fff]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** 标题词集合。英文按词切，中文按 2-gram，过滤停用词与纯数字。 */
export function titleTokens(text: string | null | undefined): Set<string> {
  const out = new Set<string>();
  const lower = (text || '').toLowerCase();
  for (const word of lower.match(/[a-z][a-z0-9-]{2,}/g) || []) {
    if (!STOPWORDS.has(word)) out.add(word);
  }
  for (const chunk of (text || '').match(/[\u4e00-\u9fff]{2,}/g) || []) {
    for (let i = 0; i < chunk.length - 1; i += 1) out.add(chunk.slice(i, i + 2));
  }
  return out;
}

function jaccard(left: Set<string>, right: Set<string>): number {
  if (!left.size || !right.size) return 0;
  let shared = 0;
  left.forEach((token) => { if (right.has(token)) shared += 1; });
  const union = left.size + right.size - shared;
  return union ? shared / union : 0;
}

function intersection(left: Set<string>, right: Set<string>): string[] {
  const out: string[] = [];
  left.forEach((token) => { if (right.has(token)) out.push(token); });
  return out;
}

/** 价格带接近度：按对数比打分，R80 与 R120 的差距和 R800 与 R1200 一样。 */
function priceScore(a: number | null | undefined, b: number | null | undefined): number {
  if (!a || !b || a <= 0 || b <= 0) return 0;
  const ratio = Math.max(a, b) / Math.min(a, b);
  return Math.max(0, 1 - Math.log(ratio) / Math.log(3));
}

/** 尺寸/重量接近度：两边都有值才比，缺一边就不给分（不猜）。 */
function sizeScore(a: Product, b: Product): number {
  const scores: number[] = [];
  const pairs: Array<[number | null | undefined, number | null | undefined]> = [
    [a.actual_weight_kg, b.actual_weight_kg],
    [a.volume_cbm, b.volume_cbm],
  ];
  for (const [left, right] of pairs) {
    if (left && right && left > 0 && right > 0) scores.push(priceScore(left, right));
  }
  return scores.length ? scores.reduce((sum, n) => sum + n, 0) / scores.length : 0;
}

/** 品类核心词：标题词去掉配件词。用来判断「是不是同一类东西」。 */
export function categoryTerms(product: Product): Set<string> {
  const out = new Set<string>();
  titleTokens(product.product_name).forEach((token) => {
    if (!ACCESSORY_TERMS.has(token)) out.add(token);
  });
  return out;
}

/**
 * 候选是不是锚点的配件。只看标题，不猜——宁可漏掉，
 * 也不要把两件不相干的东西摆成「配套」。
 *
 * 两条判据之前都先过一道「主题冲突」：共享的品类词是 hair 这种泛词，
 * 而候选讲的是宠物毛/车用/地板，那就不是同一件事。
 */
export function isAccessoryOf(
  candidate: Product,
  anchor: Product,
): { accessory: boolean; reason: string } {
  const name = (candidate.product_name || '').toLowerCase();
  const anchorTerms = categoryTerms(anchor);
  const candidateTerms = titleTokens(candidate.product_name);
  const shared = intersection(candidateTerms, anchorTerms);
  if (subjectConflict(shared, name)) return { accessory: false, reason: '' };

  for (const term of anchorTerms) {
    // 只查表，不设长度门槛：原来是 length < 4 就跳过，于是 'wig'（3 个字母）的整张
    // 组合表从来没被查过——假发是这里最主要的品类，它的配件全靠后面的泛判据兜着，
    // 规则表等于死代码。表是白名单，短词也不会误伤。
    const rules = COMPLEMENT_RULES[term];
    if (!rules) continue;
    for (const keyword of rules) {
      if (containsPhrase(name, keyword)) {
        return { accessory: true, reason: `常见配套：${term} → ${keyword}` };
      }
    }
  }
  // 配件词是否真的挨着品类词，交给 genericCompanion 判；这里不再先用 titleTokens
  // 做一遍「有没有配件词」的预筛——titleTokens 认不出复数（pads），会和下面的判据打架。
  const paired = genericCompanion(name, shared);
  if (paired.paired) return { accessory: true, reason: paired.reason };
  return { accessory: false, reason: '' };
}

/**
 * 行为信号：同一采购单里一起出现过几次。
 *
 * 没有浏览埋点，只能拿「真下过单的共现」当行为。采购记录没有批次号，
 * 只能用日期当批次；同一商品同一天的多笔视为一次，避免「一次买 5 箱」刷高共现。
 */
export function buildCooccurrence(
  records: ProcurementRecord[],
): Record<string, Record<string, number>> {
  const byBatch = new Map<string, Set<string>>();
  for (const row of records || []) {
    if (!row || !row.product_id) continue;
    const day = String(row.recorded_at || '').slice(0, 10)
      || String(row.status_updated_at || '').slice(0, 10);
    if (!day) continue;
    const bucket = byBatch.get(day) || new Set<string>();
    bucket.add(row.product_id);
    byBatch.set(day, bucket);
  }
  const pairs: Record<string, Record<string, number>> = {};
  byBatch.forEach((ids) => {
    if (ids.size < 2) return;
    ids.forEach((left) => {
      ids.forEach((right) => {
        if (left === right) return;
        const row = pairs[left] || (pairs[left] = {});
        row[right] = (row[right] || 0) + 1;
      });
    });
  });
  return pairs;
}

function plidOf(product: Product): string {
  const direct = (product.plid || '').trim();
  if (direct) return direct;
  const match = (product.takealot_url || '').match(/(?:\/PLID|[?&]plid=)(\d+)/i);
  return match ? match[1] : '';
}

/** 同一件商品的判据集合。任一相同就算重复。 */
export function identityKeys(product: Product): string[] {
  const keys: string[] = [];
  const plid = plidOf(product);
  if (plid) keys.push(`plid:${plid}`);
  const sku = (product.sku || '').trim();
  if (sku) {
    for (const part of sku.split(/[\s,，;；]+/)) {
      if (part) keys.push(`sku:${part}`);
    }
  }
  const name = normalizeTitle(product.product_name);
  if (name) keys.push(`name:${name}`);
  const image = (product.product_image_url || '').trim();
  if (image) keys.push(`img:${image}`);
  return keys;
}

function scorePair(
  anchor: Product,
  candidate: Product,
  sharedTerms: Set<string>,
  cooccur: number,
): { score: number; reasons: string[]; accessory: boolean } {
  const reasons: string[] = [];

  let category: number;
  if (anchor.fee_category && anchor.fee_category === candidate.fee_category) {
    category = 1;
    reasons.push(`同一费率类别：${anchor.fee_category}`);
  } else {
    category = sharedTerms.size ? 0.4 : 0;
  }

  const anchorTerms = categoryTerms(anchor);
  const candidateTerms = categoryTerms(candidate);
  const titleOverlap = jaccard(anchorTerms, candidateTerms);
  const attribute = 0.55 * titleOverlap
    + 0.25 * priceScore(anchor.actual_sale_price_zar, candidate.actual_sale_price_zar)
    + 0.20 * sizeScore(anchor, candidate);
  if (titleOverlap >= 0.25) {
    reasons.push(`标题重合 ${intersection(anchorTerms, candidateTerms).length} 个品类词`);
  }
  if (priceScore(anchor.actual_sale_price_zar, candidate.actual_sale_price_zar) >= 0.6) {
    reasons.push('价格带接近');
  }

  const accessoryHit = isAccessoryOf(candidate, anchor);
  if (accessoryHit.accessory) reasons.push(accessoryHit.reason);

  const behavior = Math.min(1, cooccur / 3);
  if (cooccur) reasons.push(`采购记录同日出现 ${cooccur} 天`);

  const score = WEIGHTS.category * category
    + WEIGHTS.attribute * attribute
    + WEIGHTS.complement * (accessoryHit.accessory ? 1 : 0)
    + WEIGHTS.behavior * behavior;
  return { score, reasons, accessory: accessoryHit.accessory };
}

export interface RecommendOptions {
  similarLimit?: number;
  complementLimit?: number;
}

function clampLimit(value: number | undefined, fallback: number): number {
  if (value == null || !Number.isFinite(value)) return fallback;
  return Math.max(0, Math.min(Math.floor(value), MAX_LIMIT));
}

/** 算出同类与互补两组推荐。只读。 */
export function recommendProducts(
  anchor: Product,
  pool: Product[],
  records: ProcurementRecord[] = [],
  options: RecommendOptions = {},
): RecommendationResult {
  const similarLimit = clampLimit(options.similarLimit, DEFAULT_LIMITS.similar);
  const complementLimit = clampLimit(options.complementLimit, DEFAULT_LIMITS.complement);

  const candidates = (pool || []).filter((item) => item && !item.is_archived);
  const cooccurMap = buildCooccurrence(records)[anchor.id] || {};

  const taken = new Set(identityKeys(anchor));
  const anchorTerms = categoryTerms(anchor);

  const scored: Array<{ product: Product; score: number; reasons: string[]; accessory: boolean }> = [];
  for (const candidate of candidates) {
    if (candidate.id === anchor.id) continue;
    const keys = identityKeys(candidate);
    // 库里本来就存在同一件商品的多条记录，只留一条，不重复占位。
    if (keys.some((key) => taken.has(key))) continue;
    const shared = new Set<string>();
    const candidateTerms = categoryTerms(candidate);
    anchorTerms.forEach((token) => { if (candidateTerms.has(token)) shared.add(token); });
    const scoredOne = scorePair(anchor, candidate, shared, cooccurMap[candidate.id] || 0);
    if (scoredOne.score < MIN_SCORE) continue;
    scored.push({
      product: candidate,
      score: Math.round(scoredOne.score * 10000) / 10000,
      reasons: scoredOne.reasons,
      accessory: scoredOne.accessory,
    });
  }

  scored.sort((a, b) => b.score - a.score || (b.product.product_no ?? 0) - (a.product.product_no ?? 0));

  const toPayload = (item: typeof scored[number]): RecommendationItem => {
    const product = item.product;
    return {
      product_id: product.id,
      product_no: product.product_no,
      name: product.product_name || '',
      chinese_name: product.chinese_product_name || '',
      sku: product.sku || '',
      plid: plidOf(product),
      image_url: product.product_image_url || '',
      price_zar: product.actual_sale_price_zar,
      profit_margin: product.profit_margin,
      fee_category: product.fee_category,
      takealot_url: product.takealot_url || '',
      score: item.score,
      reasons: item.reasons,
    };
  };

  const complement: RecommendationItem[] = [];
  const similar: RecommendationItem[] = [];
  const used = new Set<string>();

  for (const item of scored) {
    if (complement.length >= complementLimit) break;
    if (!item.accessory) continue;
    const keys = identityKeys(item.product);
    if (keys.some((key) => used.has(key))) continue;
    keys.forEach((key) => used.add(key));
    complement.push(toPayload(item));
  }

  for (const item of scored) {
    if (similar.length >= similarLimit) break;
    if (item.accessory) continue;
    const keys = identityKeys(item.product);
    if (keys.some((key) => used.has(key))) continue;
    keys.forEach((key) => used.add(key));
    similar.push(toPayload(item));
  }

  const meta: RecommendationMeta = {
    strategy: '品类关联 + 属性相似 + 搭配互补 + 行为共现，加权求和后去重截断',
    weights: { ...WEIGHTS },
    dedup: '自身/归档/同 PLID/同 SKU/同标题/同图，任一相同只留最高分；同类与配件之间也不重复',
    min_score: MIN_SCORE,
    limits: { similar: similarLimit, complement: complementLimit },
    candidate_count: candidates.length,
    scored_count: scored.length,
    anchor: {
      product_id: anchor.id,
      product_no: anchor.product_no,
      name: anchor.product_name || '',
      fee_category: anchor.fee_category,
    },
  };

  return { similar, complement, meta };
}
