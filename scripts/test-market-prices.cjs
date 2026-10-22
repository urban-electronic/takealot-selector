// 多市场参考价（src/utils/marketPrice.ts）的口径测试：不启浏览器，直接编译 TS。
//
// 钉死的两件事：
//   1) 「没填」不能变成「¥0」。展示层是最后一道边界——后端写入有校验，但
//      /api/migrate 是整行搬运、不过 Pydantic，桌面版导出的历史行可能带 null。
//      把 null 当 0 渲染，用户看到的是一个像真价格的假价格，比空着更糟。
//   2) 空市场不许进 payload：后端 market_prices 每个市场都要求数值 price，
//      塞一个只有 cur 没有 price 的条目进去会被 400 掉，整次保存失败。
//   3) 参考价的「采集日期」与「失败痕迹」只有一套口径：日期走 utils/datetime 的
//      todayLocal()（不是浏览器本地时区），手工改价要清掉 stale/miss/last_error
//      （否则填了新价界面还挂「已过期」）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

// marketPrice.ts 现在依赖 utils/datetime。Node 默认不认 .ts，注册一个扩展钩子，
// 让 require('./datetime') 也走同样的 transpile。
Module._extensions['.ts'] = (target, filename) => {
  target._compile(
    ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText,
    filename,
  );
};

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
const { parseMarketPrices, serializeMarketPrices, formatMarketPrice, formatSyncTime, marketInsights, MARKET_ORDER, MARKET_META, visibleMarkets, toZar, formatZar, formatZarPrice, fxCaption, FALLBACK_FX, buildMarketBatch, describeSkipped, manualPriceEntry, symbolFor, staleNote } = mod.exports;

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
// 0 也在这一组：参考价不可能是 0（没有商品白送），而 /status 用真值判「填没填」、
// 自动同步的 _to_price() 丢弃 <=0、批量录入把 0 判无效——三处都不认 0，
// 只有详情页输入框和这里还收，于是同一个字段会「列表显示 ¥0、覆盖率算未填」。
for (const bad of [null, '', '   ', 'abc', true, false, [], {}, 0, '0']) {
  const prices = parseMarketPrices(JSON.stringify({ CN: { price: bad, cur: 'CNY' } }));
  check(!prices.CN, `price=${JSON.stringify(bad)} 必须当成没数据，不能渲染成 ¥0`);
}

// ---- 负数同样不接受（参考价只可能是正数） ----------------------------------
{
  const prices = parseMarketPrices(JSON.stringify({ CN: { price: -1 } }));
  check(!prices.CN, '负数价格应当被丢掉');
}

// ---- 0 不许被写进 payload：后端会存下来，覆盖率却当成「没填」 ---------------
{
  const payload = serializeMarketPrices({ CN: { price: 0, cur: 'CNY' }, TB: { price: 899 } });
  check(JSON.parse(payload).CN === undefined, '价格为 0 的市场不得写进 payload');
  check(JSON.parse(payload).TB.price === 899, '同批次的正常价格照常写入');
  check(serializeMarketPrices({ CN: { price: 0 } }) === '', '只剩 0 时整段返回空串（等于清空该字段）');
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
  check(detail.includes('折成兰特'), '详情页要说明参考价会折成兰特，否则用户不知道为什么显示 R');
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

// ---- 展示：只装采到的市场，采不到的不占位 -------------------------------
// 14 款里目前只有 9 款能采到国内价（淘宝/天猫要登录态、中国官网已停摆）。
// 如果三格都画出来，剩下 5 款就是两格灰占位——用户会以为该去填，
// 而填了也改不了「抓不到」这件事。所以采不到的直接不出现。
{
  const full = visibleMarkets({ CN: { price: 699 }, SG: { price: 89.9 }, TB: { price: 639 } });
  check(full.join(',') === 'SG,CN,TB', '三个都有时新加坡官网价打头（唯一自动重读的权威值）');

  const sgOnly = visibleMarkets({ SG: { price: 89.9 } });
  check(sgOnly.join(',') === 'SG', '只有官网价时只出官网价，不留国内两格占位');

  const noSg = visibleMarkets({ CN: { price: 699 }, TB: { price: 639 } });
  check(noSg.join(',') === 'CN,TB', '官网价缺失时国内两项照样按原序显示');

  check(visibleMarkets({}).length === 0, '一个都没采到时不返回任何市场（整格留空）');
  check(visibleMarkets(parseMarketPrices('{"SG":{"price":null}}')).length === 0,
    'price=null 的条目不算采到（不能变成 S$0）');
}

// ---- 展示口径要在列表里真的用上，不能只存在于工具函数 ---------------------
{
  const list = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
  check(list.includes('visibleMarkets(prices)'), '列表单元格与悬停卡都要走 visibleMarkets');
  check(!list.includes('等待自动同步'), '不能再画「等待自动同步」占位');
  check(!list.includes('未录入（天猫/京东到手价）'), '不能再画「未录入（天猫/京东到手价）」占位');
  check(!list.includes('未录入（天猫/京东原价）'), '不能再画「未录入（天猫/京东原价）」占位');
}

// ---- 兰特折算：三地本币折成 R，才能和 Takealot 售价直接比 -------------------
// 折算方向最容易写反：上游给的是「1 ZAR = x 外币」，这里要的是「1 外币 = x ZAR」。
// 用真实汇率（2026-10-21：1 SGD=R12.8345、1 CNY=R2.4453）钉住几个具体数字，
// 一旦有人把乘除写反，这几条会立刻红。
{
  const fx = { base: 'ZAR', rates: { SGD: 12.8345, CNY: 2.4453 }, at: '2026-10-21' };

  check(formatZarPrice('SG', { price: 99.9, cur: 'SGD' }, fx) === 'R1,282', 'S$99.9 → R1,282');
  check(formatZarPrice('CN', { price: 569, cur: 'CNY' }, fx) === 'R1,391', '¥569 → R1,391');
  check(formatZarPrice('TB', { price: 479, cur: 'CNY' }, fx) === 'R1,171', '¥479 → R1,171');

  check(Math.abs(toZar(100, 'SGD', fx) - 1283.45) < 1e-9, '1 SGD 折 12.8345 兰特（不是倒数）');
  check(formatZar(1234567) === 'R1,234,567', '兰特要带千分位');
  check(formatZar(0) === 'R0', '0 也要出文案（后端已挡住非正数，这里是最后一道）');
  check(formatZar(1282.5) === 'R1,283', '四舍五入到整数');

  check(toZar(100, 'SGD', { base: 'ZAR', rates: {} }) === null, '没有汇率时返回 null，不能当 0 算');
  check(formatZarPrice('SG', { price: 99.9, cur: 'SGD' }, { base: 'ZAR', rates: { CNY: 2.4453 } }) === '',
    '缺 SGD 汇率时返回空串，由调用方退回原币');
  check(toZar(Number.NaN, 'SGD', fx) === null, '价格是 NaN 时不给结果');

  // 条目上写了 cur 就以它为准：迁移数据里可能有非默认币种的价格
  check(formatZarPrice('CN', { price: 99.9, cur: 'SGD' }, fx) === 'R1,282',
    'entry.cur 优先于市场默认币种');

  check(fxCaption(fx) === '1 SGD=R12.83 · 1 CNY=R2.45 · 2026-10-21', '汇率说明要写清比值和日期');
  check(fxCaption({ ...fx, stale: true }).includes('汇率未更新'), '过期汇率要在说明里标出来');
  check(fxCaption({ ...fx, fallback: true }).includes('兜底汇率'), '兜底汇率要标明来源不可靠');
  check(FALLBACK_FX.rates.SGD > 0 && FALLBACK_FX.rates.CNY > 0, '兜底汇率必须可用，否则整列空白');
}

// ---- 列表和详情页都得真的用上折算，不能只存在于工具函数 ---------------------
{
  const list = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
  check(list.includes('formatZarPrice('), '列表单元格要用折算后的兰特');
  check(list.includes('fxCaption(fx)'), '列表要把汇率写出来，否则用户无法核对折算结果');
  const detail = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductDetail.tsx'), 'utf8');
  check(detail.includes('formatZarPrice('), '详情页也要显示兰特');
}

// ---- 批量录入：无效行要交回去点名，不能静默丢掉 ---------------------------
// 输入框是自由文本（inputMode="decimal"，不是 type="number"），0 / -5 / abc
// 都能留在框里。旧写法直接 return 掉这些行，然后提示「已保存 N 条」或
// 「没有需要保存的改动」——用户明明改过，界面却说没改。
{
  const rows = [
    { id: 'p1', sku: 'CK2-901', market_prices: JSON.stringify({ CN: { price: 469 }, TB: { price: 268 } }) },
    { id: 'p2', sku: 'CK2-902', market_prices: '' },
  ];
  const draft = {
    p1: { CN: '469', TB: '299' },      // CN 没改、TB 改成 299
    p2: { CN: '0', TB: 'abc' },        // 两个都无效
  };
  const { items, skipped } = buildMarketBatch(rows, draft);
  check(items.length === 1 && items[0].market === 'TB' && items[0].price === 299, '只有真改过的有效行进 payload');
  check(skipped.length === 2, '无效行必须交回去（0 和 abc 各一条）');
  check(skipped.every((row) => row.label === 'CK2-902'), '提示里要点名是哪个 SKU');
  check(skipped[0].market === 'CN' && skipped[0].value === '0', '记录原样输入，便于用户对照');

  // 清空 = 删除该市场；本来就没有值的空串不算改动
  const cleared = buildMarketBatch(rows, { p1: { CN: '', TB: '' }, p2: { CN: '', TB: '' } });
  check(cleared.items.length === 2 && cleared.items.every((it) => it.price === null), '清空已有价格要发 price:null');
  check(cleared.skipped.length === 0, '清空不是无效输入');

  // 提示文案：最多点 3 个名字，其余折成「等 N 行」
  const many = Array.from({ length: 5 }, (_, i) => ({ label: `SKU-${i}`, market: 'CN', value: '0' }));
  check(describeSkipped(many) === 'SKU-0 国内原价、SKU-1 国内原价、SKU-2 国内原价 等 5 行',
    '无效行提示只点前三个 + 总数');
  check(describeSkipped([]) === '', '没有无效行时不给提示');

  // 面板必须用这个函数，而不是自己再写一套「无效就跳过」
  const list = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
  check(list.includes('buildMarketBatch('), '批量保存要用 buildMarketBatch 组装');
  check(list.includes('describeSkipped('), '无效行要在提示里点名');
  check(!/if \(!Number\.isFinite\(value\) \|\| value <= 0\) return;/.test(list),
    '不许再出现「无效就静默 return」的写法');
}

// ---- 手工改价：日期走业务时区，失败痕迹要清掉 -------------------------------
// 详情页原来自己拼 `at`（浏览器本地时区）且原样保留上次自动同步留下的
// stale/miss/last_error：新加坡价连续三次没读到被打上 stale 之后，用户手工
// 填了正确价格，界面还挂着「· 已过期（最近一次没读到）」。
{
  const beijingToday = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date());
  const fresh = manualPriceEntry(undefined, 'SG', 115);
  check(fresh.price === 115 && fresh.cur === 'SGD', '手工录入补齐价格与默认币种');
  check(fresh.src === '手工', '新条目来源是「手工」');
  check(fresh.at === beijingToday, `采集日期按北京时间，实际 ${fresh.at}`);
  check(/^\d{4}-\d{2}-\d{2}$/.test(fresh.at), '采集日期是 YYYY-MM-DD');

  const staleEntry = {
    price: 99, cur: 'SGD', src: '官网自动', at: '2026-10-01', url: 'https://x/1',
    checked_at: '2026-10-20T00:00:00+00:00', miss: 3, stale: true, last_error: 'HTTP 503',
  };
  const fixed = manualPriceEntry(staleEntry, 'SG', 115);
  check(fixed.price === 115, '手工价覆盖旧价');
  check(fixed.stale === undefined, '手工改价必须清掉 stale（否则界面仍写「已过期」）');
  check(fixed.miss === undefined, '手工改价要清掉失败计数');
  check(fixed.last_error === undefined, '手工改价要清掉上次失败原因');
  check(fixed.src === '官网自动', '不改来源：这一格仍是每日自动重读的市场（成功同步会覆盖回来）');
  check(fixed.url === 'https://x/1', '链接必须保留，否则自动同步再也回不来');

  // 详情页必须用这个函数，不能自己再拼一套日期/来源
  const detail = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductDetail.tsx'), 'utf8');
  check(detail.includes('manualPriceEntry('), '详情页手工改价要用 manualPriceEntry()');
  check(!/\bat: today\(\)/.test(detail), '详情页不许再调用私有的 today()');

  // 开发机本身就是东八区，只比日期差是抓不到的（改回本地时区照样相等）。
  // 所以同时钉实现：`at` 必须来自 todayLocal()。
  const src = fs.readFileSync(path.resolve(__dirname, '../src/utils/marketPrice.ts'), 'utf8');
  check(src.includes('at: todayLocal(),'), '采集日期必须由 todayLocal() 生成，不许自己拼');
  check(!/function today\(/.test(src), 'marketPrice 不许再留一份私有 today()');
}

// ---- 货币符号跟着条目上的 cur 走，不跟着市场写死 ---------------------------
// 迁移数据里可能有别的币种（entryCurrency 的注释就承认这一点）。价格列一直按
// 市场写死符号，于是同一行会出现「S$99」配「无汇率」——左边说新加坡元、
// 右边说不认识这个币种。
{
  check(formatMarketPrice('SG', { price: 115, cur: 'SGD' }) === 'S$115', '新加坡元用 S$');
  check(formatMarketPrice('CN', { price: 469, cur: 'CNY' }) === '¥469', '人民币用 ¥');
  check(formatMarketPrice('SG', { price: 99, cur: 'USD' }) === 'US$99', '别的币种不许冒用 S$');
  check(formatMarketPrice('SG', { price: 99, cur: 'GBP' }) === 'GBP 99', '认不出的币种写出代码，不猜符号');
  check(formatMarketPrice('TB', { price: 268 }) === '¥268', '没写 cur 时退回该市场的默认币种');
  check(symbolFor('SG', { price: 115 }) === 'S$', 'symbolFor 与价格列口径一致');
  check(!('symbol' in MARKET_META.SG), 'MARKET_META 不再自带符号表（避免两份真相）');
}

// ---- 编辑态草稿：取消要能退回去，保存失败不能吞掉整页 -----------------------
// 参考价表格两种模式都读 `mpDraft`，所以「取消」只 setEditMode(false) 时，
// 被丢弃的价格还挂在页面上，再点「编辑」拿到的也是那份旧草稿。
// 保存失败写进 `error` 更糟：那个状态渲染时整页换成一行红字，草稿一起消失。
{
  const detail = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductDetail.tsx'), 'utf8');
  check(/const cancelEdit = \(\) => \{[\s\S]{0,200}?resetDraft\(product\)/.test(detail),
    '取消编辑必须用服务端那一份重建草稿（否则参考价表格留着被丢弃的值）');
  check(/onClick=\{cancelEdit\}/.test(detail), '取消按钮要调 cancelEdit，不能只 setEditMode(false)');
  check(/resetDraft\(updated\)/.test(detail), '保存成功后要用服务端返回值重建草稿');
  // 只对 handleSave 这一段断言：整份文件里 cancelEdit 也会 setSaveError('')，
  // 用全文匹配会把「保存失败又写回 setError」漏过去（探针实测漏过一次）。
  const saveStart = detail.indexOf('const handleSave');
  const saveBody = detail.slice(saveStart, detail.indexOf('const cancelEdit', saveStart));
  check(saveStart > 0 && saveBody.length > 200, '要能定位到 handleSave 的实现（断言依赖它）');
  check(saveBody.includes('setSaveError('), '保存失败要写 saveError');
  check(!saveBody.includes('setError('), '保存失败不许写进加载用的 error（会整页替换、草稿一起丢）');
  check(/role="alert"[^>]*>\{saveError/.test(detail),
    'saveError 要在页头以行内提示渲染');
}

// ---- 按钮文案与表头同一口径 -------------------------------------------------
// 表头写「国内原价 / 国内到手价」，MARKET_META 也明确「标签必须写国内，不能写官网」，
// 但按钮 title 一直写「批量录入中国官网价」——点开面板的同屏正文却在说
// 「中国官网（charleskeith.cn）已停摆」。
{
  const list = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
  check(!/title="批量录入中国官网价/.test(list), '「参考价」按钮不许再写「中国官网价」（官网已停摆）');
  check(/title="批量录入国内原价 \/ 到手价/.test(list), '「参考价」按钮按表头口径写「国内原价 / 到手价」');
}

// ---- 「已过期」标记：一处定义，每个展示面都得挂上 ---------------------------
// 自动同步连续 3 次没读到时后端打 `stale`，价格保留但已经旧了。这个标记原来在
// 三个界面上有两种写法、还有一处根本不写：批量录入面板（只读的新加坡价那一列）
// 把一周前的 S$115 显示得和刚同步成功的一模一样，而用户正是照着它决定国内价填多少。
// 列表方框又只在**打头那个市场**过期时才整格变淡，于是「SG 新的、到手价过期」看不出来。
{
  check(staleNote({ price: 115, stale: true }) === ' · 已过期', '过期条目要带标记');
  check(staleNote({ price: 115 }) === '', '没过期不带标记');
  check(staleNote({ price: 115, miss: 2 }) === '', '失败两次还不算过期（阈值由后端 STALE_AFTER 决定）');
  check(staleNote(undefined) === '', '没有条目时不带标记');
  check(staleNote(null) === '', 'null 也不带标记');

  const list = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductList.tsx'), 'utf8');
  check(list.includes('staleNote(prices.SG)'), '批量录入面板的新加坡价也要标「已过期」');
  check(/opacity: prices\[code\]!\.stale \? 0\.6 : 1/.test(list),
    '列表方框按单个市场弱化，不能只看打头那个（否则非打头市场过期完全看不出来）');
  check(!/entry\.stale \? ' · 已过期' : ''/.test(list), '悬停卡不许再自己拼一份标记文案');
  check(!/opacity: leadEntry\.stale \? 0\.6 : 1,/.test(list), '整格弱化的旧写法必须去掉');
}

console.log(`test-market-prices: ${checks} checks passed`);

// Execute the actual detail handlers with isolated state and a mock write API.
(async () => {
  const detail = fs.readFileSync(path.resolve(__dirname, '../src/pages/ProductDetail.tsx'), 'utf8');
  const save = detail.slice(detail.indexOf('  const handleSave ='), detail.indexOf('  /** 取消编辑'));
  const price = detail.slice(detail.indexOf('  const setMarketPrice ='), detail.indexOf('  /** 价格框里该显示什么'));
  const invalid = detail.slice(detail.indexOf('  const marketInputInvalid ='), detail.indexOf('  /** 淘宝均价'));
  const code = ts.transpileModule(save + price + invalid + '\nglobalThis.handlers = { handleSave, setMarketPrice };',
    { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText;
  for (const [raw, expectedWrites] of [['0', 0], ['-1', 0], ['Infinity', 0], ['', 1], ['0.5', 1], ['150', 1]]) {
    const original = { CN: { price: 100, cur: 'CNY' } };
    let writes = 0;
    let payload;
    let error = '';
    const ctx = {
      id: 'fixture', product: {}, mpDraft: original, mpText: {}, editData: {},
      MARKET_ORDER, MARKET_META, Number, serializeMarketPrices, manualPriceEntry,
      setSaveError: value => { error = value; },
      setMpText: fn => { ctx.mpText = fn(ctx.mpText); },
      setMpDraft: value => { ctx.mpDraft = value; },
      updateField: (key, value) => { ctx.editData[key] = value; },
      api: { updateProduct: async (_id, value) => { writes++; payload = value; return value; } },
      setProduct() {}, resetDraft() {}, setEditMode() {}, notifyCatalogChanged() {},
      searchParams: { get: () => null }, navigate() {},
    };
    require('node:vm').runInNewContext(code, ctx);
    ctx.handlers.setMarketPrice('CN', raw);
    await ctx.handlers.handleSave();
    check(writes === expectedWrites, `输入 ${JSON.stringify(raw)} 后保存：预期 ${expectedWrites} 次写入，实际 ${writes}`);
    if (!expectedWrites) check(error.includes('价格'), '无效价格必须提示修正，不能静默清空原价');
    if (raw === '') check(payload.market_prices === '', '显式清空仍可保存');
    if (raw === '0.5') check(JSON.parse(payload.market_prices).CN.price === 0.5, '小数原样保存，不变成 5');
  }
  console.log('detail price save: 11 isolated checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
