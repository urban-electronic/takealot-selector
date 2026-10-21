// 多市场参考价（src/utils/marketPrice.ts）的口径测试：不启浏览器，直接编译 TS。
//
// 钉死的两件事：
//   1) 「没填」不能变成「¥0」。展示层是最后一道边界——后端写入有校验，但
//      /api/migrate 是整行搬运、不过 Pydantic，桌面版导出的历史行可能带 null。
//      把 null 当 0 渲染，用户看到的是一个像真价格的假价格，比空着更糟。
//   2) 空市场不许进 payload：后端 market_prices 每个市场都要求数值 price，
//      塞一个只有 cur 没有 price 的条目进去会被 400 掉，整次保存失败。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

const file = path.resolve(__dirname, '../src/utils/marketPrice.ts');
const mod = new Module(file, module);
mod.filename = file;
mod.paths = Module._nodeModulePaths(path.dirname(file));
mod._compile(
  ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
  file,
);
const { parseMarketPrices, serializeMarketPrices, formatMarketPrice, formatSyncTime, marketInsights, MARKET_ORDER, MARKET_META } = mod.exports;

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

// ---- 正常解析：三个市场都留下，price 归一成数字 -----------------------------
{
  const raw = JSON.stringify({
    CN: { price: 1289, cur: 'CNY', src: '官网', at: '2026-10-21' },
    SG: { price: 245.9, cur: 'SGD', src: '官网', at: '2026-10-21' },
    TB: { price: 899, cur: 'CNY', src: '淘宝均价', n: 128, at: '2026-10-21' },
  });
  const prices = parseMarketPrices(raw);
  check(MARKET_ORDER.every(c => prices[c]), '三个市场都应解析出来');
  check(prices.SG.price === 245.9, '价格保持原值');
  check(prices.TB.n === 128, '淘宝样本条数要带过来（悬停卡要显示）');
}

// ---- 空值：没有数据 ≠ 价格 0 ----------------------------------------------
for (const bad of [null, '', '   ', 'abc', true, false, [], {}]) {
  const prices = parseMarketPrices(JSON.stringify({ CN: { price: bad, cur: 'CNY' } }));
  check(!prices.CN, `price=${JSON.stringify(bad)} 必须当成没数据，不能渲染成 ¥0`);
}

// ---- 负数同样不接受（后端 gt=0 的护栏，前端不该先显示一个负数价格） --------
{
  const prices = parseMarketPrices(JSON.stringify({ CN: { price: -1 } }));
  check(!prices.CN, '负数价格应当被丢掉');
}

// ---- 数字字符串接受（迁移行可能把数字写成字符串） ---------------------------
{
  const prices = parseMarketPrices(JSON.stringify({ SG: { price: '245.9' } }));
  check(prices.SG && prices.SG.price === 245.9, '数字字符串要归一成数字');
}

// ---- 坏 JSON / 非对象：整段当没有，不许抛 ----------------------------------
for (const raw of ['not-json', '[1,2]', 'null', '"x"', undefined, null]) {
  const prices = parseMarketPrices(raw);
  check(prices && Object.keys(prices).length === 0, `坏输入 ${String(raw)} 应当解析成空对象`);
}

// ---- 序列化：空市场不进 payload；全空返回空串（= 清空该字段） --------------
{
  check(serializeMarketPrices({}) === '', '没有任何市场时返回空串');
  const withEmpty = serializeMarketPrices({ CN: { price: NaN, cur: 'CNY' }, SG: { price: 245.9, cur: 'SGD' } });
  check(JSON.parse(withEmpty).CN === undefined, '没有价格的市场不得写进 payload（后端会 400）');
  check(JSON.parse(withEmpty).SG.price === 245.9, '有价格的市场照常写入');
}

// ---- 往返一致 --------------------------------------------------------------
{
  const raw = JSON.stringify({ CN: { price: 1289, cur: 'CNY', src: '官网', at: '2026-10-21' } });
  const round = serializeMarketPrices(parseMarketPrices(raw));
  check(round === raw, '解析后再序列化应当原样回到同一串 JSON');
}

// ---- 展示格式：币种符号跟着市场走，小数不拖尾零 -----------------------------
{
  check(formatMarketPrice('CN', { price: 1289 }) === '¥1289', '整数不带小数位');
  check(formatMarketPrice('SG', { price: 245.9 }) === 'S$245.9', '新加坡用 S$，且不拖尾零');
  check(formatMarketPrice('TB', { price: 899 }) === '¥899', '到手价用 ¥');
}

// ---- 标签：CN/TB 都是「国内」，不能写成「中国官网 / 淘宝均价」 -------------
// 中国官网已停摆，实际数据来自天猫、京东；标签写「官网」会让人以为有官方权威价。
{
  check(MARKET_META.CN.label === '国内原价', 'CN 标签是「国内原价」');
  check(MARKET_META.TB.label === '国内到手价', 'TB 标签是「国内到手价」');
  check(MARKET_META.TB.short === '到手', 'TB 的列内缩写是「到手」，不能是「淘」（数据含京东）');
  check(MARKET_META.SG.label === '新加坡官网', 'SG 仍是唯一有官网权威价的市场');
}

// ---- 比价结论：只在同币种两项都有时给，除零不给 -----------------------------
{
  const both = marketInsights({ CN: { price: 1000 }, TB: { price: 800 } });
  check(both.length === 1 && both[0].value === '80%', '淘宝/中国官网 = 80%');
  check(marketInsights({ CN: { price: 0 }, TB: { price: 800 } }).length === 0, '中国官网为 0 时不做除法');
  check(marketInsights({ CN: { price: 1000 } }).length === 0, '只有一个市场时不给结论');
  check(marketInsights({ SG: { price: 245.9 }, TB: { price: 800 } }).length === 0,
    '只有 SGD 与 CNY 时不比（不引入汇率假设）');
}

// ---- 文案：谁自动、谁手工，必须和代码实际行为一致 -------------------------
// 现在只有官网直营市场（SG）由后端每天重读；中国官网已停摆、淘宝/天猫要登录态，
// 两者都不做自动抓取。文案写成「都会自动」会让用户等着一个永远不来的数，
// 写成「全手工」又会让用户白填一遍 SG。
{
  const detail = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductDetail.tsx'), 'utf8');
  check(detail.includes('新加坡官网价由服务端每天自动重读'), '详情页要说明 SG 是自动重读');
  check(detail.includes('国内原价与到手价需要人工录入'), '详情页要说明 CN/TB 需要人工录入');
  check(!detail.includes('三个市场都要手工录入'), 'SG 已改为自动同步，不能再写「三个市场都要手工录入」');
  check(!detail.includes('新加坡官网价取自官网商品页'),
    '不得再写「取自官网商品页」：那是人工抄价的口径，现在由服务端读取');
  check(detail.includes('不与人民币直接比价'), 'SGD 不能与人民币直接比，文案要写明');
}

// ---- 自动同步的边界：只碰官网来源，不碰淘宝 ---------------------------------
{
  const sync = fs.readFileSync(path.resolve(__dirname, '../backend/services/market_price_sync.py'), 'utf8');
  check(sync.includes('AUTO_SOURCES'), '同步服务要显式声明哪些来源可自动更新');
  check(sync.includes('手工'), '手工录入的价格必须被排除在自动更新之外');
  check(sync.includes('不做平台反爬对抗'), '淘宝/天猫不做自动抓取，代码里要有明确说明');
}

// ---- 「上次同步」按北京时间显示，不露 UTC 原始串 --------------------------
// 后端存的是 UTC ISO（…+00:00）。直接显示会差 8 小时：北京时间 13:12 同步完，
// 界面写 05:12，用户会以为今天根本没跑过。
{
  check(formatSyncTime('2026-10-21T05:12:33+00:00') === '2026-10-21 13:12', 'UTC 时间戳要换算成北京时间');
  check(formatSyncTime('2026-10-21T20:30:00+00:00') === '2026-10-22 04:30', '跨日的换算不能丢一天');
  check(formatSyncTime(undefined) === '尚未运行', '没有同步记录时写「尚未运行」');
  check(formatSyncTime(null) === '尚未运行', 'null 也要写「尚未运行」');
  check(formatSyncTime('') === '尚未运行', '空串也要写「尚未运行」');
  check(formatSyncTime('昨天') === '时间未知', '解析不了的串不能原样显示成时间');
  check(!formatSyncTime('2026-10-21T05:12:33+00:00').includes('T'), '不许把 ISO 原始串直接摆到界面上');
  const list = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
  check(list.includes('formatSyncTime(mpStatus.last_sync?.at)'), '列表页「上次同步」必须走 formatSyncTime');
  check(!list.includes("String((mpStatus.last_sync as any)?.at"), '不许再直接 String() 原始 ISO 串');
}

console.log(`test-market-prices: ${checks} checks passed`);
