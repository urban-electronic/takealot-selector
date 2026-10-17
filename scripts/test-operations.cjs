const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'takealot-operations-'));
try {
  execFileSync(process.execPath, [path.join(root, 'node_modules/typescript/bin/tsc'), ...['pricing', 'replenish', 'recommend', 'keywords'].map(n => path.join(root, `src/operations/${n}.ts`)), '--outDir', tmp, '--target', 'ES2020', '--module', 'commonjs', '--skipLibCheck']);
  const { evaluatePricing } = require(path.join(tmp, 'pricing.js'));
  const { computeReplenish } = require(path.join(tmp, 'replenish.js'));
  const { buildSuggestions } = require(path.join(tmp, 'recommend.js'));
  const now = new Date('2026-10-13T12:00:00Z');
  const offer = (price, offerType = 'fastest', status = 'in_stock') => ({ seller: 'competitor', price, offerType, status, fetchedAt: now });
  const input = { currentPrice: 273, minPrice: 113, maxPrice: 400, competitorOffers: [offer(274), offer(263, 'best_price')], rule: { strategy: 'takealot_buybox', leadAmount: 1, excludedSellers: [], minPriceChange: 1, cooldownMinutes: 10 }, state: { stock: 5, lastPricedAt: null, todayChanges: 0, maxDailyChanges: 5, offersMaxAgeMs: 1800000, now, holdingBuybox: false, buyboxPrice: 263 } };
  assert.equal(evaluatePricing(input).newPrice, 262, 'Fastest不可降时应转到Best Price');
  assert.notEqual(evaluatePricing({ ...input, minPrice: 270 }).decision, 'execute', '不得跌破最低价');
  assert.notEqual(evaluatePricing({ ...input, competitorOffers: [offer(198, 'best_price', 'backorder')] }).decision, 'execute', '不跟随预售价');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [offer(300)] }).decision, 'skip', '未持有时不得跟涨');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [offer(320)], state: { ...input.state, holdingBuybox: true } }).newPrice, 319, '确认持有后允许回弹');
  assert.equal(evaluatePricing({ ...input, state: { ...input.state, stock: 0 } }).decision, 'reject');
  assert.equal(evaluatePricing({ ...input, state: { ...input.state, todayChanges: 5 } }).decision, 'reject');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [{ ...offer(260), fetchedAt: new Date(now - 3600000) }] }).decision, 'reject');
  // 行情过期/未来/非法时保持现价，这是既定口径（docs/pricepilot-integration.md：
  // 「行情过期均保持现价」）。不能改成"只挑新的用"：一条报价不可信就说明这一轮
  // 的市场快照不完整，此时改价是在猜。
  assert.equal(evaluatePricing({ ...input, competitorOffers: [offer(300), { ...offer(200), fetchedAt: new Date(now - 3600000) }] }).decision, 'reject', '一条过期报价就保持现价，不能只挑新的用');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [offer(263, 'best_price'), { ...offer(200), fetchedAt: new Date(now - 3600000) }] }).decision, 'reject', '新 263 + 旧 200 不得压到 262');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [offer(250, 'best_price', 'backorder'), offer(263, 'best_price')] }).newPrice, 262, '缺货竞品不参与压价');
  assert.equal(evaluatePricing({ ...input, state: { ...input.state, lastPricedAt: new Date(now.getTime() - 5 * 60000) } }).decision, 'skip', '未持有时冷却同样生效');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [{ ...offer(260), fetchedAt: new Date(now.getTime() + 60000) }] }).decision, 'reject', '拒绝未来快照');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [offer(NaN)] }).decision, 'reject', '拒绝无效报价');
  assert.equal(evaluatePricing({ ...input, minPrice: 500 }).decision, 'reject', '拒绝上下限倒置');
  assert.equal(evaluatePricing({ ...input, competitorOffers: [{ ...offer(200), isMine: true }], state: { ...input.state, buyboxPrice: null } }).decision, 'reject', '不得与自身竞价');
  const replenish = { sku: 'test', name: 'test', price: 100, stockTotal: 3, onWay: 10, now, sales: [{ orderDate: new Date(now - 86400000), quantity: 30, sellingPrice: 100 }] };
  const r = computeReplenish(replenish);
  assert.equal(r.vUsed, 3, '近期爆发最多采用30天日均的三倍');
  assert.equal(r.suggestedSea, 164, '海运45天+安全14天并扣除库存与在途');
  assert.equal(r.suggestedAir, 74);
  assert.equal(computeReplenish({ ...replenish, onWay: 200 }).suggestedSea, 0);
  assert.equal(computeReplenish({ ...replenish, sales: [] }).level, 'slow_moving');
  assert.equal(computeReplenish({ ...replenish, stockTotal: 0 }).dailyLossZar, 300);
  const source = { sku: 'test', title: 'Anker USB Cable', minPrice: 100 };
  const candidate = { title: 'Anker USB Cable', price: 120, url: 'https://www.takealot.com/test/PLID1' };
  assert.equal(buildSuggestions(source, '', [candidate, candidate]).accepted.length, 1, '候选链接去重');
  assert.equal(buildSuggestions(source, '', [{ ...candidate, price: 99 }]).accepted.length, 0, '拒绝低于价格门槛的候选');
  assert.equal(buildSuggestions(source, '', [{ ...candidate, title: 'Anker Power Bank' }]).accepted.length, 0, '同品牌不同品类不能误推荐');
  console.log('22 operations regression assertions passed');
} finally { fs.rmSync(tmp, { recursive: true, force: true }); }
