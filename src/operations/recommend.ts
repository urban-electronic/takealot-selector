// Adapted from PricePilot native source 2026-10-12; see docs/pricepilot-integration.md.
import { extractBrand, tokenize } from "./keywords.js";

export interface RecommendConfig {

  minSimilarity: number;

  minSharedTokens: number;

  maxPerSource: number;

  topSellerCount: number;

  salesWindowDays: number;

  priceFloorRatio: number;

  pagesPerKeyword: number;

  candidatesPerKeyword: number;
}

export const DEFAULT_RECOMMEND_CONFIG: RecommendConfig = {
  minSimilarity: 0.28,
  minSharedTokens: 2,
  maxPerSource: 12,
  topSellerCount: 8,
  salesWindowDays: 30,
  priceFloorRatio: 1.0,
  pagesPerKeyword: 1,
  candidatesPerKeyword: 40
};

export interface SourceProduct {
  sku: string;
  title: string;

  minPrice: number | null;
}

export interface CandidateProduct {
  url: string;
  title: string;
  price: number | null;
  seller?: string | null;
  imageUrl?: string | null;
  tsin?: number | null;
  unitsSold?: number | null;
  rating?: number | null;
}

export interface ScoredCandidate extends CandidateProduct {
  sourceSku: string;
  sourceTitle: string;
  keyword: string;
  similarity: number;
  priceFloor: number | null;
  reason: string;
}

export interface SimilarityResult {
  score: number;

  shared: string[];
}

const round2 = (n: number) => Math.round(n * 100) / 100;

export function scoreSimilarity(sourceTitle: string, candidateTitle: string): SimilarityResult {
  const a = new Set(tokenize(sourceTitle));
  const b = new Set(tokenize(candidateTitle));
  if (a.size === 0 || b.size === 0) return { score: 0, shared: [] };

  const shared = [...a].filter((t) => b.has(t));
  const union = new Set([...a, ...b]);
  const jaccard = shared.length / union.size;
  const coverage = shared.length / a.size;

  const brandA = extractBrand(sourceTitle);
  const brandB = extractBrand(candidateTitle);
  const brandBonus = brandA && brandB && brandA === brandB ? 0.15 : 0;

  const score = Math.min(1, jaccard * 0.45 + coverage * 0.55 + brandBonus);
  return { score, shared };
}

export interface EvaluateResult {
  accepted: boolean;
  similarity: number;
  priceFloor: number | null;
  reason: string;
}

export function evaluateCandidate(
  source: SourceProduct,
  candidate: CandidateProduct,
  config: RecommendConfig = DEFAULT_RECOMMEND_CONFIG
): EvaluateResult {
  const { score, shared } = scoreSimilarity(source.title, candidate.title);
  const priceFloor = source.minPrice != null ? round2(source.minPrice * config.priceFloorRatio) : null;

  if (!candidate.url || !candidate.title) {
    return { accepted: false, similarity: score, priceFloor, reason: "候选缺少链接或标题，丢弃" };
  }

  const pct = (n: number) => `${(n * 100).toFixed(0)}%`;

  if (score < config.minSimilarity) {
    return {
      accepted: false,
      similarity: score,
      priceFloor,
      reason: `相似度 ${pct(score)} 低于阈值 ${pct(config.minSimilarity)}（共同词：${shared.slice(0, 6).join("/") || "无"}）`
    };
  }

  const sourceTokenCount = tokenize(source.title).length;
  const requiredShared = Math.min(config.minSharedTokens, sourceTokenCount);
  if (shared.length < requiredShared) {
    return {
      accepted: false,
      similarity: score,
      priceFloor,
      reason: `共同核心词仅 ${shared.length} 个（需 ≥ ${requiredShared}）：${shared.join("/") || "无"}，不足以判定为同类商品`
    };
  }

  if (candidate.price == null || candidate.price <= 0) {
    return { accepted: false, similarity: score, priceFloor, reason: "候选无有效价格，无法核对价格门槛" };
  }

  if (priceFloor != null && candidate.price < priceFloor) {
    return {
      accepted: false,
      similarity: score,
      priceFloor,
      reason: `候选价 R${candidate.price} 低于最低价门槛 R${priceFloor}（口径：推荐价不低于我的 minPrice）`
    };
  }

  const parts = [`相似度 ${pct(score)}`, `共同词 ${shared.slice(0, 6).join("/") || "-"}`];
  if (priceFloor != null) parts.push(`价格 R${candidate.price} ≥ 门槛 R${priceFloor}`);
  else parts.push(`价格 R${candidate.price}（来源未设最低价，不做价格过滤）`);
  return { accepted: true, similarity: score, priceFloor, reason: parts.join("；") };
}

export function rankSuggestions(
  items: ScoredCandidate[],
  config: RecommendConfig = DEFAULT_RECOMMEND_CONFIG
): ScoredCandidate[] {
  const byUrl = new Map<string, ScoredCandidate>();
  for (const it of items) {
    const prev = byUrl.get(it.url);
    if (!prev || it.similarity > prev.similarity) byUrl.set(it.url, it);
  }

  const sorted = [...byUrl.values()].sort(
    (a, b) => b.similarity - a.similarity || (b.price ?? 0) - (a.price ?? 0)
  );

  const perSource = new Map<string, number>();
  const out: ScoredCandidate[] = [];
  for (const it of sorted) {
    const n = perSource.get(it.sourceSku) ?? 0;
    if (n >= config.maxPerSource) continue;
    perSource.set(it.sourceSku, n + 1);
    out.push(it);
  }
  return out;
}

export function buildSuggestions(
  source: SourceProduct,
  keyword: string,
  candidates: CandidateProduct[],
  config: RecommendConfig = DEFAULT_RECOMMEND_CONFIG
): { accepted: ScoredCandidate[]; rejected: number } {
  const passed: ScoredCandidate[] = [];
  let rejected = 0;

  for (const c of candidates) {
    const verdict = evaluateCandidate(source, c, config);
    if (!verdict.accepted) {
      rejected += 1;
      continue;
    }
    passed.push({
      ...c,
      sourceSku: source.sku,
      sourceTitle: source.title,
      keyword,
      similarity: verdict.similarity,
      priceFloor: verdict.priceFloor,
      reason: verdict.reason
    });
  }

  return { accepted: rankSuggestions(passed, config), rejected };
}
