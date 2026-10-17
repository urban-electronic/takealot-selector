// Adapted from PricePilot native source 2026-10-12; see docs/pricepilot-integration.md.
export type Strategy = "lead_lowest" | "follow_lowest" | "profit_first" | "takealot_buybox";

export type OfferStatus = "in_stock" | "pre_order" | "backorder" | "out_of_stock";

export interface CompetitorOffer {
  seller: string;
  price: number;
  fetchedAt: Date;
  status?: OfferStatus;
  offerType?: "buybox" | "fastest" | "best_price" | "other";
  isMine?: boolean;
  deliveryLabel?: string | null;
  leadtimeDays?: number | null;
}

export interface PricingInput {
  currentPrice: number;
  minPrice: number | null;
  maxPrice: number | null;
  competitorOffers: CompetitorOffer[];
  rule: {
    strategy: Strategy;
    leadAmount: number; // 领先最低价的领先金额
    excludedSellers: string[];
    minPriceChange: number; // 最小价格变化幅度
    cooldownMinutes: number; // 冷却时间
  };
  state: {
    stock: number;
    lastPricedAt: Date | null;
    todayChanges: number;
    maxDailyChanges: number; // 最大日调价次数
    offersMaxAgeMs: number; // 竞品数据有效期
    now: Date;
    holdingBuybox: boolean;
    buyboxPrice: number | null;
    fastestPrice?: number | null;
    bestPricePrice?: number | null;
    bestPricePreorder?: boolean | null;
  };
}

export type Decision = "execute" | "reject" | "skip";

export interface PricingResult {
  decision: Decision;
  newPrice?: number;
  reason?: string;
  suggestedRawPrice?: number;
}

export function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

const reject = (reason: string): PricingResult => ({ decision: "reject", reason });
const skip = (reason: string): PricingResult => ({ decision: "skip", reason });

function computeBuyboxRaw(input: {
  currentPrice: number;
  minPrice: number | null;
  maxPrice: number | null;
  leadAmount: number;
  inStockCompetitors: CompetitorOffer[];
  holdingBuybox: boolean;
  buyboxPrice: number | null;
}): { raw: number } | { skipReason: string } {
  const { currentPrice, minPrice, maxPrice, leadAmount, inStockCompetitors, holdingBuybox, buyboxPrice } = input;

  if (inStockCompetitors.length === 0) {
    return { skipReason: "竞品全部非现货，无对标现货价，维持现价" };
  }

  const hasCardTyped = inStockCompetitors.some((o) => o.offerType != null && o.offerType !== "other");
  let fastestPool: CompetitorOffer[];
  let bestPricePool: CompetitorOffer[];
  if (!hasCardTyped) {
    fastestPool = inStockCompetitors;
    bestPricePool = [];
  } else {
    fastestPool = inStockCompetitors.filter((o) => o.offerType === "fastest");
    if (fastestPool.length === 0) {
      fastestPool = inStockCompetitors.filter((o) => o.offerType === "buybox");
    }
    bestPricePool = inStockCompetitors.filter((o) => o.offerType === "best_price");
  }
  const fastestMin = fastestPool.length > 0 ? Math.min(...fastestPool.map((o) => o.price)) : null;
  const bestMin = bestPricePool.length > 0 ? Math.min(...bestPricePool.map((o) => o.price)) : null;

  if (!holdingBuybox) {
    if (buyboxPrice != null && Math.abs(Number(buyboxPrice) - currentPrice) < 0.01) {
      return { skipReason: `未持有 Buybox 但 Buybox 价 ${buyboxPrice} 与当前价 ${currentPrice} 相同，疑似 holding 误判，跳过不降` };
    }

    if (fastestMin != null) {
      const raw = Math.floor(fastestMin - leadAmount);
      if (raw < currentPrice && (minPrice == null || raw >= minPrice)) {
        return { raw };
      }
    }

    if (bestMin != null) {
      const raw = Math.floor(bestMin - leadAmount);
      if (raw < currentPrice && (minPrice == null || raw >= minPrice)) {
        return { raw };
      }
    }

    const why = [
      fastestMin == null
        ? "Fastest 无现货卡"
        : (() => {
            const r = Math.floor(fastestMin - leadAmount);
            return `Fastest ${fastestMin}→${r}${r >= currentPrice ? "（不低于当前价，降无可降）" : `（跌破最低价 ${minPrice}）`}`;
          })(),
      bestMin == null
        ? "Best Price 无现货卡"
        : (() => {
            const r = Math.floor(bestMin - leadAmount);
            return `Best Price ${bestMin}→${r}${r >= currentPrice ? "（不低于当前价，降无可降）" : `（跌破最低价 ${minPrice}）`}`;
          })()
    ].join("；");
    return {
      skipReason: `未持有 Buybox，只降不升：${why}，两卡均无法以不低于最低价 ${minPrice ?? "无限制"} 拿下，放弃`
    };
  }

  const lowestInStock = Math.min(...inStockCompetitors.map((o) => o.price));
  if (lowestInStock > currentPrice) {
    const raw = Math.floor(lowestInStock - leadAmount);
    const capped = Math.min(raw, maxPrice ?? Infinity);
    if (capped <= currentPrice) {
      return { skipReason: `持有 Buybox 且竞品最低价 ${lowestInStock} 接近当前价，维持现价` };
    }
    return { raw: capped };
  }
  if (lowestInStock === currentPrice) {
    return { skipReason: `持有 Buybox 且竞品最低价 ${lowestInStock} 与当前价持平，维持现价` };
  }
  const raw = Math.floor(lowestInStock - leadAmount);
  if (raw >= currentPrice) {
    return { skipReason: `持有 Buybox，竞品最低价 ${lowestInStock} 与当前价持平，维持现价` };
  }
  return { raw };
}

export function evaluatePricing(input: PricingInput): PricingResult {
  if (!Number.isFinite(input.currentPrice) || input.currentPrice <= 0 ||
      !Number.isFinite(input.state.now.getTime()) ||
      !Number.isFinite(input.state.stock) ||
      (input.minPrice !== null && (!Number.isFinite(input.minPrice) || input.minPrice <= 0)) ||
      (input.maxPrice !== null && (!Number.isFinite(input.maxPrice) || input.maxPrice <= 0)) ||
      (input.minPrice !== null && input.maxPrice !== null && input.minPrice > input.maxPrice)) {
    return reject('价格、库存或时间无效，拒绝计算');
  }
  if (input.state.stock <= 0) {
    return reject("商品无库存，拒绝调价");
  }

  let offers = input.competitorOffers.filter(
    (o) => !input.rule.excludedSellers.includes(o.seller) && !o.isMine
  );
  if (offers.length === 0) {
    if (
      (input.rule.strategy === "takealot_buybox" || input.rule.strategy === "follow_lowest") &&
      !input.state.holdingBuybox &&
      input.state.buyboxPrice != null
    ) {
      offers = [
        {
          seller: "__buybox_snapshot__",
          price: input.state.buyboxPrice,
          fetchedAt: input.state.now,
          offerType: "buybox",
        },
      ];
    } else {
      return reject("无可用竞品报价（或无未排除的竞品）");
    }
  }

  // 行情过期/时间在未来/价格非法时保持现价，这是既定口径（docs/pricepilot-integration.md：
  // 「行情过期均保持现价」），不能为了"少卡一轮"把这条守卫放宽。任意一条竞品报价
  // 不可信就说明这一轮的市场快照不完整，此时改价是在猜。
  if (offers.some(o => !Number.isFinite(o.price) || o.price <= 0 ||
      !Number.isFinite(o.fetchedAt.getTime()) || o.fetchedAt > input.state.now ||
      input.state.now.getTime() - o.fetchedAt.getTime() > input.state.offersMaxAgeMs)) {
    return reject("竞品价格或时间无效，或数据已过期，拒绝调价");
  }
  // 有现货竞品时只跟现货竞价；缺货/预售的挂价不代表市场价，跟着它降价是白送利润。
  const inStock = offers.filter(o => (o.status ?? "in_stock") === "in_stock");
  if (inStock.length > 0) offers = inStock;

  if (input.state.maxDailyChanges > 0 && input.state.todayChanges >= input.state.maxDailyChanges) {
    return reject(`超过当日最大调价次数（${input.state.maxDailyChanges}）`);
  }

  // 冷却对所有情形生效：原来只在持有 Buybox 时检查，未持有时可以连续改价。
  if (
    input.state.lastPricedAt &&
    input.state.now.getTime() - input.state.lastPricedAt.getTime() < input.rule.cooldownMinutes * 60_000
  ) {
    return skip(`处于冷却时间内（${input.rule.cooldownMinutes} 分钟）`);
  }

  let raw: number;
  switch (input.rule.strategy) {
    case "lead_lowest":
      raw = Math.min(...offers.map((o) => o.price)) - input.rule.leadAmount;
      break;
    case "follow_lowest": {
      if (!input.state.holdingBuybox && input.state.buyboxPrice != null) {
        if (Math.abs(Number(input.state.buyboxPrice) - input.currentPrice) < 0.01) {
          return skip(
            `未持有 Buybox 但 Buybox 价 ${input.state.buyboxPrice} 与当前价 ${input.currentPrice} 相同，疑似 holding 误判，跳过不降`
          );
        }
        const inStockOffers = offers.filter((o) => (o.status ?? "in_stock") === "in_stock");
        const target =
          inStockOffers.length > 0 ? Math.min(...inStockOffers.map((o) => o.price)) : Number(input.state.buyboxPrice);
        const grab = Math.floor(target - input.rule.leadAmount);
        if (grab >= input.currentPrice) {
          return skip(
            `未持有 Buybox，只降不升（最低现货价 ${target}，建议 ${grab} 不低于当前价 ${input.currentPrice}）`
          );
        }
        raw = grab;
      } else {
        const inStock = offers.filter((o) => (o.status ?? "in_stock") === "in_stock");
        if (inStock.length === 0) {
          return skip(
            input.state.holdingBuybox
              ? "持有 Buybox 但无现货竞品报价，无对标价，维持现价"
              : "无现货竞品报价，无对标价，维持现价"
          );
        }
        const lowest = Math.min(...inStock.map((o) => o.price));
        if (!input.state.holdingBuybox && lowest >= input.currentPrice) {
          return skip(
            `未持有 Buybox 且无 Buybox 快照价，最低现货价 ${lowest} 不低于当前价 ${input.currentPrice}，只降不升，维持现价`
          );
        }
        raw = lowest;
      }
      break;
    }
    case "profit_first":
      return reject("profit_first 策略已下线（不再计算成本/利润率），请改用 min-max 区间策略");
    case "takealot_buybox": {
      const inStockCompetitors = offers.filter((o) => (o.status ?? "in_stock") === "in_stock");
      const r = computeBuyboxRaw({
        currentPrice: input.currentPrice,
        minPrice: input.minPrice,
        maxPrice: input.maxPrice,
        leadAmount: input.rule.leadAmount,
        inStockCompetitors,
        holdingBuybox: input.state.holdingBuybox,
        buyboxPrice: input.state.buyboxPrice
      });
      if ("skipReason" in r) return skip(r.skipReason);
      raw = r.raw;
      break;
    }
    default:
      return reject("未知定价策略");
  }
  raw = round2(raw);

  const floor = input.minPrice ?? -Infinity;
  const ceil = input.maxPrice ?? Infinity;
  if (floor > ceil) {
    return reject("价格区间无可行值（最低价高于最高价）");
  }
  const suggested = round2(Math.min(Math.max(raw, floor), ceil));

  if (
    (input.rule.strategy === "takealot_buybox" || input.rule.strategy === "follow_lowest") &&
    raw < floor
  ) {
    return reject(`建议价 ${raw} 低于用户最低价 ${floor}，放弃竞争`);
  }

  if (Math.abs(suggested - input.currentPrice) < input.rule.minPriceChange) {
    return skip("价格变化幅度小于最小调整阈值");
  }
  if (suggested === input.currentPrice) {
    return skip("建议价与当前价一致，无需调价");
  }

  return {
    decision: "execute",
    newPrice: suggested,
    suggestedRawPrice: raw
  };
}
