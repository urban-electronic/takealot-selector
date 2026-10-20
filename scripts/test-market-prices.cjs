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
const { parseMarketPrices, serializeMarketPrices, formatMarketPrice, marketInsights, MARKET_ORDER } = mod.exports;

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
  check(formatMarketPrice('TB', { price: 899 }) === '¥899', '淘宝均价用 ¥');
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

// ---- 文案：不能让人以为新加坡价是系统自己去官网抓的 -----------------------
// 三个市场全是手工录入，代码里没有任何官网抓取（按红线也不做）。
// 旧文案写「新加坡官网价取自官网商品页」，读起来像自动读取，会让人以为填不填都行。
{
  const detail = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductDetail.tsx'), 'utf8');
  check(detail.includes('三个市场都要手工录入'), '详情页必须说清三个市场都是手工录入');
  check(!detail.includes('新加坡官网价取自官网商品页'),
    '不得再写「取自官网商品页」：系统不会自动读取，三个市场都是手输');
  check(detail.includes('不与人民币直接比价'), 'SGD 不能与人民币直接比，文案要写明');
}

console.log(`test-market-prices: ${checks} checks passed`);
